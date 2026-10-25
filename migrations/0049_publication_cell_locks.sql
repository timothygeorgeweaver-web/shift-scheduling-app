DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.publication_day_windows(
    target_location uuid,target_day date
)
RETURNS jsonb
LANGUAGE sql STABLE
SET search_path=pg_catalog
AS $$
    WITH saved AS (
        SELECT (p.roster->'week'->>'week_start_date')::date AS first_day,
               (p.roster->'week'->>'business_day_start_minute')::integer AS rollover
        FROM scheduling.schedule_publication p
        WHERE p.location_id=target_location
    ), portions AS (
        SELECT DISTINCT
            CASE WHEN target_day=first_day THEN rollover ELSE 0 END AS left_minute,
            CASE WHEN target_day=first_day+7 THEN rollover ELSE 1440 END AS right_minute
        FROM saved
        WHERE target_day BETWEEN first_day AND first_day+7
    )
    SELECT coalesce(jsonb_agg(
        jsonb_build_array(left_minute,right_minute)
        ORDER BY left_minute,right_minute
    ),'[]'::jsonb)
    FROM portions WHERE left_minute<right_minute;
$$;

REVOKE ALL ON FUNCTION scheduling.publication_day_windows(uuid,date)
FROM PUBLIC,shift_scheduling_app;

CREATE FUNCTION scheduling.guard_published_availability_cells()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    location_value uuid;
    day_value date;
    windows jsonb;
    before_cells jsonb;
    after_cells jsonb;
    changed_cell jsonb;
    minute_value integer;
BEGIN
    IF TG_OP='UPDATE' AND
       ROW(NEW.organization_id,NEW.location_id,NEW.employee_id,NEW.local_date)
       IS DISTINCT FROM
       ROW(OLD.organization_id,OLD.location_id,OLD.employee_id,OLD.local_date)
    THEN
        RAISE EXCEPTION 'Planner identity cannot be changed'
            USING ERRCODE='42501';
    END IF;

    IF TG_OP='DELETE' THEN
        organization_value := OLD.organization_id;
        location_value := OLD.location_id;
        day_value := OLD.local_date;
    ELSE
        organization_value := NEW.organization_id;
        location_value := NEW.location_id;
        day_value := NEW.local_date;
    END IF;

    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    windows := scheduling.publication_day_windows(location_value,day_value);

    IF windows<>'[]'::jsonb THEN
        before_cells := CASE WHEN TG_OP='INSERT'
            THEN '[]'::jsonb ELSE OLD.blocked_cells END;
        after_cells := CASE WHEN TG_OP='DELETE'
            THEN '[]'::jsonb ELSE NEW.blocked_cells END;

        FOR changed_cell IN
            SELECT DISTINCT value
            FROM jsonb_array_elements(before_cells || after_cells)
            WHERE (before_cells @> jsonb_build_array(value))
               IS DISTINCT FROM
                  (after_cells @> jsonb_build_array(value))
        LOOP
            minute_value := (changed_cell::text)::integer * 15;
            IF EXISTS (
                SELECT FROM jsonb_array_elements(windows) portion
                WHERE minute_value>=(portion->>0)::integer
                  AND minute_value<(portion->>1)::integer
            ) THEN
                RAISE EXCEPTION 'Published availability cells are locked'
                    USING ERRCODE='55000';
            END IF;
        END LOOP;
    END IF;

    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;

REVOKE ALL ON FUNCTION scheduling.guard_published_availability_cells()
FROM PUBLIC,shift_scheduling_app;

CREATE TRIGGER published_availability_cells
BEFORE INSERT OR UPDATE OR DELETE ON scheduling.unavailability_date
FOR EACH ROW EXECUTE FUNCTION scheduling.guard_published_availability_cells();

CREATE OR REPLACE FUNCTION scheduling.planner_day_state(
    target_location uuid, target_employee uuid, target_day date
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
DECLARE
    l scheduling.location%ROWTYPE;
    frozen scheduling.unavailability_day_lock%ROWTYPE;
    cutoff_value timestamptz;
    reason_value text;
    published_windows jsonb;
    fully_published boolean;
BEGIN
    SELECT * INTO STRICT l FROM scheduling.location WHERE id=target_location;
    SELECT * INTO frozen FROM scheduling.unavailability_day_lock
    WHERE employee_id=target_employee AND local_date=target_day;

    BEGIN
        cutoff_value := scheduling.unavailability_cutoff(
            target_location,target_day
        );
    EXCEPTION WHEN SQLSTATE '22023' THEN
        reason_value := 'invalid_cutoff_time';
    END;

    IF frozen.employee_id IS NOT NULL THEN
        reason_value := CASE WHEN frozen.reason='publication'
            THEN 'published_week' ELSE 'cutoff_reached' END;
    ELSIF target_day<(clock_timestamp() AT TIME ZONE l.timezone)::date
          OR clock_timestamp()>=cutoff_value THEN
        reason_value := 'cutoff_reached';
    END IF;

    published_windows := scheduling.publication_day_windows(
        target_location,target_day
    );
    SELECT NOT EXISTS (
        SELECT FROM generate_series(0,95) cell
        WHERE NOT EXISTS (
            SELECT FROM jsonb_array_elements(published_windows) portion
            WHERE cell*15>=(portion->>0)::integer
              AND cell*15<(portion->>1)::integer
        )
    ) INTO fully_published;
    IF fully_published THEN reason_value := 'published_week'; END IF;

    RETURN jsonb_build_object(
        'publication_locked_windows',published_windows,
        'editable',reason_value IS NULL,
        'lock_reason',reason_value,
        'cutoff',cutoff_value,
        'frozen_blocked',CASE WHEN frozen.employee_id IS NOT NULL
            THEN frozen.blocked_windows ELSE NULL END
    );
END
$$;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0049_publication_cell_locks', :'migration_sha256');
