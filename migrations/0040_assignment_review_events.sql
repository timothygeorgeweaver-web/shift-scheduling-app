DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.assignment_review_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    assignment_id uuid NOT NULL,
    assignment_revision integer NOT NULL CHECK (assignment_revision>0),
    cause text NOT NULL CHECK (cause IN (
        'availability','qualification','employee_status','role_status',
        'rest_policy','assignment_change','manual_recheck'
    )),
    status text NOT NULL CHECK (status IN ('pending','clear','needs_review')),
    details jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(details)='object'),
    actor_user_id uuid,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (organization_id,location_id,assignment_id)
        REFERENCES scheduling.shift_assignment(organization_id,location_id,id),
    FOREIGN KEY (organization_id,actor_user_id)
        REFERENCES scheduling.organization_member(organization_id,user_id)
);

CREATE INDEX assignment_review_latest_idx
ON scheduling.assignment_review_event(assignment_id,id DESC);

ALTER TABLE scheduling.assignment_review_event ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON scheduling.assignment_review_event
FROM PUBLIC,shift_scheduling_app;

CREATE FUNCTION scheduling.preserve_assignment_review_event()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'Assignment review history is immutable'
        USING ERRCODE='55000';
END
$$;

REVOKE ALL ON FUNCTION scheduling.preserve_assignment_review_event()
FROM PUBLIC,shift_scheduling_app;

CREATE TRIGGER assignment_review_event_immutable
BEFORE UPDATE OR DELETE ON scheduling.assignment_review_event
FOR EACH ROW EXECUTE FUNCTION scheduling.preserve_assignment_review_event();

CREATE FUNCTION scheduling.queue_assignment_review()
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

REVOKE ALL ON FUNCTION scheduling.queue_assignment_review()
FROM PUBLIC,shift_scheduling_app;

CREATE TRIGGER rest_policy_assignment_review
AFTER UPDATE OF rest_window_start_minute,rest_window_end_minute,minimum_rest_minutes
ON scheduling.location
FOR EACH ROW EXECUTE FUNCTION scheduling.queue_assignment_review();

CREATE TRIGGER employee_status_assignment_review
AFTER UPDATE OF active ON scheduling.employee
FOR EACH ROW EXECUTE FUNCTION scheduling.queue_assignment_review();

CREATE TRIGGER role_status_assignment_review
AFTER UPDATE OF active ON scheduling.job_role
FOR EACH ROW EXECUTE FUNCTION scheduling.queue_assignment_review();

CREATE TRIGGER qualification_assignment_review
AFTER INSERT OR UPDATE OR DELETE ON scheduling.employee_qualification
FOR EACH ROW EXECUTE FUNCTION scheduling.queue_assignment_review();

CREATE TRIGGER availability_assignment_review
AFTER INSERT OR UPDATE OR DELETE ON scheduling.unavailability_date
FOR EACH ROW EXECUTE FUNCTION scheduling.queue_assignment_review();

CREATE TRIGGER frozen_availability_assignment_review
AFTER INSERT OR UPDATE OR DELETE ON scheduling.unavailability_day_lock
FOR EACH ROW EXECUTE FUNCTION scheduling.queue_assignment_review();

CREATE TRIGGER neighboring_assignment_review
AFTER INSERT OR UPDATE OF active ON scheduling.shift_assignment
FOR EACH ROW EXECUTE FUNCTION scheduling.queue_assignment_review();

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0040_assignment_review_events', :'migration_sha256');
