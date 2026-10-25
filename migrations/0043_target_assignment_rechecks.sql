DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION scheduling.queue_assignment_review()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
DECLARE
    item jsonb;
    previous jsonb;
    organization_value uuid;
    location_value uuid;
    employee_value uuid;
    role_value uuid;
    day_value date;
    excluded_assignment uuid;
    cause_value text;
    actor_value uuid;
    changed_start timestamptz;
    changed_end timestamptz;
BEGIN
    item := CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
    previous := CASE WHEN TG_OP='INSERT' THEN NULL ELSE to_jsonb(OLD) END;
    organization_value := (item->>'organization_id')::uuid;
    location_value := (item->>'location_id')::uuid;

    CASE TG_TABLE_NAME
    WHEN 'location' THEN
        IF item->'rest_window_start_minute' IS NOT DISTINCT FROM
             previous->'rest_window_start_minute'
           AND item->'rest_window_end_minute' IS NOT DISTINCT FROM
             previous->'rest_window_end_minute'
           AND item->'minimum_rest_minutes' IS NOT DISTINCT FROM
             previous->'minimum_rest_minutes' THEN
            RETURN NEW;
        END IF;
        location_value := (item->>'id')::uuid;
        cause_value := 'rest_policy';
    WHEN 'employee' THEN
        IF item->'active' IS NOT DISTINCT FROM previous->'active' THEN
            RETURN NEW;
        END IF;
        employee_value := (item->>'id')::uuid;
        cause_value := 'employee_status';
    WHEN 'job_role' THEN
        IF item->'active' IS NOT DISTINCT FROM previous->'active' THEN
            RETURN NEW;
        END IF;
        role_value := (item->>'id')::uuid;
        cause_value := 'role_status';
    WHEN 'employee_qualification' THEN
        IF TG_OP='UPDATE'
           AND item->'active' IS NOT DISTINCT FROM previous->'active' THEN
            RETURN NEW;
        END IF;
        employee_value := (item->>'employee_id')::uuid;
        role_value := (item->>'job_role_id')::uuid;
        cause_value := 'qualification';
    WHEN 'unavailability_date' THEN
        IF TG_OP='UPDATE'
           AND item->'blocked_cells' IS NOT DISTINCT FROM previous->'blocked_cells' THEN
            RETURN NEW;
        END IF;
        employee_value := (item->>'employee_id')::uuid;
        day_value := (item->>'local_date')::date;
        cause_value := 'availability';
    WHEN 'unavailability_day_lock' THEN
        employee_value := (item->>'employee_id')::uuid;
        day_value := (item->>'local_date')::date;
        cause_value := 'availability';
    WHEN 'shift_assignment' THEN
        IF TG_OP='UPDATE'
           AND item->'active' IS NOT DISTINCT FROM previous->'active' THEN
            RETURN NEW;
        END IF;
        employee_value := (item->>'employee_id')::uuid;
        excluded_assignment := (item->>'id')::uuid;
        cause_value := 'assignment_change';
    ELSE
        RAISE EXCEPTION 'Unsupported assignment review source';
    END CASE;

    SELECT s.user_id INTO actor_value
    FROM scheduling.auth_session s
    JOIN scheduling.organization_member m
      ON m.user_id=s.user_id AND m.organization_id=organization_value
    WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
      AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
      AND m.active;

    IF cause_value='assignment_change' THEN
        SELECT start_at,end_at INTO STRICT changed_start,changed_end
        FROM scheduling.shift_slot WHERE id=(item->>'shift_slot_id')::uuid;
    END IF;

    INSERT INTO scheduling.assignment_review_event(
        organization_id,location_id,assignment_id,assignment_revision,
        cause,status,details,actor_user_id
    )
    SELECT a.organization_id,a.location_id,a.id,a.revision,
           cause_value,'pending',
           jsonb_build_object('source_table',TG_TABLE_NAME,'operation',TG_OP),
           actor_value
    FROM scheduling.shift_assignment a
    JOIN scheduling.shift_slot s ON s.id=a.shift_slot_id
    JOIN scheduling.schedule_week w ON w.id=s.schedule_week_id
    WHERE a.organization_id=organization_value AND a.location_id=location_value
      AND a.active AND s.active AND w.deleted_at IS NULL AND w.state='draft'
      AND (employee_value IS NULL OR a.employee_id=employee_value)
      AND (role_value IS NULL OR s.role_id=role_value)
      AND (excluded_assignment IS NULL OR a.id<>excluded_assignment)
      AND (
          cause_value<>'assignment_change'
          OR (
              (
                  s.start_at<changed_end+interval '24 hours'
                  AND s.end_at>changed_start-interval '24 hours'
              )
              OR (
                  ((s.start_at AT TIME ZONE w.timezone)
                    -w.business_day_start_minute*interval '1 minute')::date
                  <=
                  (((changed_end-interval '1 microsecond') AT TIME ZONE w.timezone)
                    -w.business_day_start_minute*interval '1 minute')::date
                  AND
                  (((s.end_at-interval '1 microsecond') AT TIME ZONE w.timezone)
                    -w.business_day_start_minute*interval '1 minute')::date
                  >=
                  ((changed_start AT TIME ZONE w.timezone)
                    -w.business_day_start_minute*interval '1 minute')::date
              )
          )
      )
      AND (
          day_value IS NULL OR (
              (s.start_at AT TIME ZONE w.timezone)::date<=day_value
              AND ((s.end_at-interval '1 microsecond')
                   AT TIME ZONE w.timezone)::date>=day_value
          )
      );

    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0043_target_assignment_rechecks', :'migration_sha256');
