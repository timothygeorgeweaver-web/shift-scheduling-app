DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.require_business_week_rollover(
    first_date date, rollover_minute integer, location_timezone text
)
RETURNS void
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
DECLARE
    day_offset integer;
    local_value timestamp;
    boundary_value timestamptz;
    previous_value timestamptz;
    matches integer;
BEGIN
    IF first_date IS NULL OR NOT isfinite(first_date)
       OR first_date<date '2000-01-01' OR first_date>date '9998-12-25'
       OR rollover_minute IS NULL OR rollover_minute NOT BETWEEN 0 AND 1439
       OR rollover_minute % 15<>0
       OR location_timezone IS NULL
       OR NOT EXISTS (
           SELECT FROM pg_timezone_names WHERE name=location_timezone
       ) THEN
        RAISE EXCEPTION 'Invalid business-week policy' USING ERRCODE='22023';
    END IF;

    FOR day_offset IN 0..7 LOOP
        local_value := (first_date+day_offset)::timestamp
                       + rollover_minute*interval '1 minute';
        SELECT count(*),min(candidate) INTO matches,boundary_value
        FROM (
            SELECT (local_value AT TIME ZONE 'UTC')
                   + n*interval '1 minute' AS candidate
            FROM generate_series(-1560,1560) g(n)
        ) possible
        WHERE candidate AT TIME ZONE location_timezone=local_value;

        IF matches<>1 THEN
            RAISE EXCEPTION 'Rollover must occur exactly once on every date'
                USING ERRCODE='22023';
        END IF;
        IF previous_value IS NOT NULL AND boundary_value<=previous_value THEN
            RAISE EXCEPTION 'Business-day boundaries must advance'
                USING ERRCODE='22023';
        END IF;
        previous_value := boundary_value;
    END LOOP;
END
$$;

REVOKE ALL ON FUNCTION
scheduling.require_business_week_rollover(date,integer,text)
FROM PUBLIC,shift_scheduling_app;

-- Validate the live baseline without altering any saved week.
DO $$
DECLARE
    week_row scheduling.schedule_week%ROWTYPE;
BEGIN
    FOR week_row IN
        SELECT * FROM scheduling.schedule_week WHERE deleted_at IS NULL
    LOOP
        PERFORM scheduling.require_business_week_rollover(
            week_row.week_start_date,
            week_row.business_day_start_minute,
            week_row.timezone
        );
    END LOOP;
END
$$;


CREATE OR REPLACE FUNCTION scheduling.create_blank_schedule_week(
    target_location uuid, target_start date, expected_location_revision integer
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    actor_value uuid;
    l scheduling.location%ROWTYPE;
    result jsonb;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.location WHERE id=target_location;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Schedule location unavailable' USING ERRCODE='42501';
    END IF;

    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    SELECT * INTO STRICT l FROM scheduling.location
    WHERE id=target_location FOR UPDATE;

    SELECT u.id INTO actor_value
    FROM scheduling.auth_session s
    JOIN scheduling.app_user u ON u.id=s.user_id
    JOIN scheduling.organization_member m
      ON m.user_id=u.id AND m.organization_id=organization_value
    JOIN scheduling.member_permission p
      ON p.organization_id=m.organization_id AND p.user_id=m.user_id
    WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
      AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
      AND u.active AND m.active
      AND p.permission IN ('administrator','scheduler')
    FOR SHARE OF s,u,m,p;

    IF actor_value IS NULL THEN
        RAISE EXCEPTION 'Scheduler access required' USING ERRCODE='42501';
    END IF;
    IF expected_location_revision IS NULL
       OR expected_location_revision<>l.revision THEN
        RAISE EXCEPTION 'Location changed' USING ERRCODE='40001';
    END IF;
    IF target_start IS NULL OR NOT isfinite(target_start)
       OR target_start<date '2000-01-01'
       OR target_start>date '9998-12-25'
       OR extract(isodow FROM target_start)::integer-1<>l.week_start THEN
        RAISE EXCEPTION 'Choose the configured week-start date'
            USING ERRCODE='22023';
    END IF;

    PERFORM scheduling.require_business_week_rollover(
        target_start,l.business_day_start_minute,l.timezone
    );

    -- Serialize creation and reject overlapping weeks even after a later
    -- administrator change to the location's configured week-start day.
    IF EXISTS (
        SELECT FROM scheduling.schedule_week
        WHERE location_id=target_location AND deleted_at IS NULL
          AND week_start_date<=target_start+6
          AND week_start_date+6>=target_start
    ) THEN
        RAISE EXCEPTION 'A schedule already covers these dates'
            USING ERRCODE='40001';
    END IF;

    INSERT INTO scheduling.schedule_week(
        organization_id,location_id,week_start_date,week_start_day,
        timezone,created_by,business_day_start_minute
    ) VALUES (
        organization_value,target_location,target_start,l.week_start,
        l.timezone,actor_value,l.business_day_start_minute
    ) RETURNING to_jsonb(schedule_week) INTO result;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_value,target_location,actor_value,
        'schedule.week_created','location',target_location,NULL,result
    );
    RETURN result;
END
$$;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0029_business_day_dst_guards', :'migration_sha256');
