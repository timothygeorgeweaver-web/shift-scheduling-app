DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.publication_availability (
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    publication_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    local_date date NOT NULL,
    start_minute integer NOT NULL,
    end_minute integer NOT NULL,
    blocked_windows jsonb NOT NULL
        CHECK (jsonb_typeof(blocked_windows)='array'),
    PRIMARY KEY (publication_id,employee_id,local_date),
    CHECK (start_minute>=0 AND end_minute<=1440
           AND start_minute<end_minute
           AND start_minute%15=0 AND end_minute%15=0),
    FOREIGN KEY (organization_id,location_id,publication_id)
        REFERENCES scheduling.schedule_publication(organization_id,location_id,id),
    FOREIGN KEY (organization_id,location_id,employee_id)
        REFERENCES scheduling.employee(organization_id,location_id,id)
);
ALTER TABLE scheduling.publication_availability ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON scheduling.publication_availability
FROM PUBLIC,shift_scheduling_app;

CREATE FUNCTION scheduling.preserve_publication_availability()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'Published availability evidence is immutable'
        USING ERRCODE='55000';
END
$$;
REVOKE ALL ON FUNCTION scheduling.preserve_publication_availability()
FROM PUBLIC,shift_scheduling_app;

CREATE TRIGGER publication_availability_immutable
BEFORE UPDATE OR DELETE ON scheduling.publication_availability
FOR EACH ROW EXECUTE FUNCTION scheduling.preserve_publication_availability();

CREATE FUNCTION scheduling.planner_cell_windows(cells jsonb)
RETURNS jsonb
LANGUAGE sql IMMUTABLE
SET search_path=pg_catalog
AS $$
    WITH selected AS (
        SELECT n FROM generate_series(0,95) g(n)
        WHERE cells @> jsonb_build_array(n)
    ), islands AS (
        SELECT n,n-row_number() OVER (ORDER BY n) AS group_number
        FROM selected
    ), portions AS (
        SELECT min(n)*15 AS left_minute,(max(n)+1)*15 AS right_minute
        FROM islands GROUP BY group_number
    )
    SELECT coalesce(jsonb_agg(
        jsonb_build_array(left_minute,right_minute) ORDER BY left_minute
    ),'[]'::jsonb) FROM portions;
$$;
REVOKE ALL ON FUNCTION scheduling.planner_cell_windows(jsonb)
FROM PUBLIC,shift_scheduling_app;

CREATE FUNCTION scheduling.capture_publication_availability()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
DECLARE
    employee_row scheduling.employee%ROWTYPE;
    first_day date;
    rollover integer;
    day_value date;
    left_minute integer;
    right_minute integer;
    effective_windows jsonb;
    clipped_windows jsonb;
BEGIN
    first_day := (NEW.roster->'week'->>'week_start_date')::date;
    rollover := (NEW.roster->'week'->>'business_day_start_minute')::integer;
    IF first_day IS NULL OR rollover IS NULL
       OR rollover NOT BETWEEN 0 AND 1439 OR rollover%15<>0 THEN
        RAISE EXCEPTION 'Invalid publication business-week metadata'
            USING ERRCODE='22023';
    END IF;

    FOR employee_row IN
        SELECT e.* FROM scheduling.employee e
        WHERE e.organization_id=NEW.organization_id
          AND e.location_id=NEW.location_id
        ORDER BY e.id FOR SHARE
    LOOP
        FOR offset_value IN 0..7 LOOP
            day_value := first_day+offset_value;
            left_minute := CASE WHEN offset_value=0 THEN rollover ELSE 0 END;
            right_minute := CASE WHEN offset_value=7 THEN rollover ELSE 1440 END;
            IF left_minute>=right_minute THEN CONTINUE; END IF;

            SELECT k.blocked_windows INTO effective_windows
            FROM scheduling.unavailability_day_lock k
            WHERE k.employee_id=employee_row.id AND k.local_date=day_value;

            IF NOT FOUND THEN
                effective_windows := scheduling.planner_cell_windows((
                    SELECT d.blocked_cells FROM scheduling.unavailability_date d
                    WHERE d.employee_id=employee_row.id AND d.local_date=day_value
                ));
            END IF;

            SELECT coalesce(jsonb_agg(jsonb_build_array(
                greatest((portion->>0)::integer,left_minute),
                least((portion->>1)::integer,right_minute)
            ) ORDER BY (portion->>0)::integer),'[]'::jsonb)
            INTO clipped_windows
            FROM jsonb_array_elements(effective_windows) portion
            WHERE (portion->>0)::integer<right_minute
              AND (portion->>1)::integer>left_minute;

            INSERT INTO scheduling.publication_availability(
                organization_id,location_id,publication_id,employee_id,
                local_date,start_minute,end_minute,blocked_windows
            ) VALUES (
                NEW.organization_id,NEW.location_id,NEW.id,employee_row.id,
                day_value,left_minute,right_minute,clipped_windows
            );
        END LOOP;
    END LOOP;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION scheduling.capture_publication_availability()
FROM PUBLIC,shift_scheduling_app;

CREATE TRIGGER capture_publication_availability
AFTER INSERT ON scheduling.schedule_publication
FOR EACH ROW EXECUTE FUNCTION scheduling.capture_publication_availability();

CREATE FUNCTION scheduling.guard_published_frozen_availability()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    location_value uuid;
    employee_value uuid;
    day_value date;
    windows jsonb;
    stored_windows jsonb;
    before_windows jsonb;
    after_windows jsonb;
BEGIN
    IF TG_OP='UPDATE' AND
       ROW(NEW.organization_id,NEW.location_id,NEW.employee_id,NEW.local_date)
       IS DISTINCT FROM
       ROW(OLD.organization_id,OLD.location_id,OLD.employee_id,OLD.local_date)
    THEN
        RAISE EXCEPTION 'Frozen availability identity cannot be changed'
            USING ERRCODE='42501';
    END IF;

    IF TG_OP='DELETE' THEN
        organization_value := OLD.organization_id;
        location_value := OLD.location_id;
        employee_value := OLD.employee_id;
        day_value := OLD.local_date;
    ELSE
        organization_value := NEW.organization_id;
        location_value := NEW.location_id;
        employee_value := NEW.employee_id;
        day_value := NEW.local_date;
    END IF;
    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    windows := scheduling.publication_day_windows(location_value,day_value);

    IF windows<>'[]'::jsonb THEN
        stored_windows := scheduling.planner_cell_windows((
            SELECT d.blocked_cells FROM scheduling.unavailability_date d
            WHERE d.employee_id=employee_value AND d.local_date=day_value
        ));
        before_windows := CASE WHEN TG_OP='INSERT'
            THEN stored_windows ELSE OLD.blocked_windows END;
        after_windows := CASE WHEN TG_OP='DELETE'
            THEN stored_windows ELSE NEW.blocked_windows END;

        IF EXISTS (
            SELECT FROM generate_series(0,1439) g(minute_value)
            WHERE EXISTS (
                SELECT FROM jsonb_array_elements(windows) portion
                WHERE g.minute_value>=(portion->>0)::integer
                  AND g.minute_value<(portion->>1)::integer
            ) AND (
                EXISTS (
                    SELECT FROM jsonb_array_elements(before_windows) blocked
                    WHERE g.minute_value>=(blocked->>0)::integer
                      AND g.minute_value<(blocked->>1)::integer
                )
                IS DISTINCT FROM
                EXISTS (
                    SELECT FROM jsonb_array_elements(after_windows) blocked
                    WHERE g.minute_value>=(blocked->>0)::integer
                      AND g.minute_value<(blocked->>1)::integer
                )
            )
        ) THEN
            RAISE EXCEPTION 'Published frozen availability cannot change'
                USING ERRCODE='55000';
        END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION scheduling.guard_published_frozen_availability()
FROM PUBLIC,shift_scheduling_app;

CREATE TRIGGER published_frozen_availability
BEFORE INSERT OR UPDATE OR DELETE ON scheduling.unavailability_day_lock
FOR EACH ROW EXECUTE FUNCTION scheduling.guard_published_frozen_availability();

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0050_publication_availability_snapshots', :'migration_sha256');
