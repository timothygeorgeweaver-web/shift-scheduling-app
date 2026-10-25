DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

ALTER TABLE scheduling.schedule_week
ADD COLUMN deleted_at timestamptz;

ALTER TABLE scheduling.schedule_week
DROP CONSTRAINT schedule_week_location_id_week_start_date_key;

CREATE UNIQUE INDEX schedule_week_live_location_start_idx
ON scheduling.schedule_week(location_id,week_start_date)
WHERE deleted_at IS NULL;

CREATE FUNCTION scheduling.remove_draft_schedule_week(
    target_location uuid,
    target_week uuid,
    expected_week_revision integer
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    actor_value uuid;
    w scheduling.schedule_week%ROWTYPE;
    before_value jsonb;
    result jsonb;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.location WHERE id=target_location;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Location unavailable' USING ERRCODE='42501';
    END IF;
    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;

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
        RAISE EXCEPTION 'Staff access required' USING ERRCODE='42501';
    END IF;

    SELECT * INTO w FROM scheduling.schedule_week
    WHERE id=target_week AND location_id=target_location
      AND organization_id=organization_value FOR UPDATE;
    IF w.id IS NULL OR w.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Week unavailable' USING ERRCODE='42501';
    END IF;
    IF w.state<>'draft' THEN
        RAISE EXCEPTION 'Only draft weeks can be deleted' USING ERRCODE='55000';
    END IF;
    IF expected_week_revision IS NULL OR expected_week_revision<>w.revision
       OR w.revision=2147483647 THEN
        RAISE EXCEPTION 'Week changed' USING ERRCODE='40001';
    END IF;
    IF EXISTS (
        SELECT FROM scheduling.shift_slot
        WHERE schedule_week_id=w.id AND active AND revision=2147483647
    ) THEN
        RAISE EXCEPTION 'Shift revision exhausted' USING ERRCODE='40001';
    END IF;

    before_value := scheduling.read_schedule_week(w.id);

    UPDATE scheduling.shift_slot
    SET active=false,revision=revision+1
    WHERE schedule_week_id=w.id AND active;

    UPDATE scheduling.schedule_week
    SET deleted_at=clock_timestamp(),revision=revision+1
    WHERE id=w.id
    RETURNING to_jsonb(schedule_week) INTO result;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_value,target_location,actor_value,
        'schedule.week_deleted','location',target_location,
        before_value,result
    );

    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session
        WHERE token_sha256=current_setting('scheduling.session_sha256',true)
          AND revoked_at IS NULL AND expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;
    RETURN jsonb_build_object(
        'id',w.id,'deleted',true,'revision',w.revision+1
    );
END
$$;

REVOKE ALL ON FUNCTION
scheduling.remove_draft_schedule_week(uuid,uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.remove_draft_schedule_week(uuid,uuid,integer)
TO shift_scheduling_app;


CREATE OR REPLACE FUNCTION scheduling.list_schedule_weeks(
    target_location uuid, range_start date, range_end date
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    l scheduling.location%ROWTYPE;
BEGIN
    SELECT * INTO l FROM scheduling.location WHERE id=target_location;
    IF l.id IS NULL OR NOT scheduling.staff_read_allowed(l.organization_id) THEN
        RAISE EXCEPTION 'Schedule location unavailable' USING ERRCODE='42501';
    END IF;
    IF range_start IS NULL OR range_end IS NULL
       OR NOT isfinite(range_start) OR NOT isfinite(range_end)
       OR range_start<date '2000-01-01' OR range_end>date '9998-12-31'
       OR range_end<range_start OR range_end-range_start>365 THEN
        RAISE EXCEPTION 'Invalid schedule range' USING ERRCODE='22023';
    END IF;

    RETURN jsonb_build_object(
        'location',jsonb_build_object(
            'id',l.id,'name',l.name,'timezone',l.timezone,
            'revision',l.revision,'week_start_day',l.week_start,
            'business_day_start_minute',l.business_day_start_minute
        ),
        'weeks',coalesce((
            SELECT jsonb_agg(to_jsonb(w) ORDER BY w.week_start_date,w.id)
            FROM scheduling.schedule_week w
            WHERE w.location_id=target_location AND w.deleted_at IS NULL
              AND w.week_start_date BETWEEN range_start AND range_end
        ),'[]'::jsonb)
    );
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

CREATE OR REPLACE FUNCTION scheduling.read_schedule_week(target_week uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    w scheduling.schedule_week%ROWTYPE;
BEGIN
    SELECT * INTO w FROM scheduling.schedule_week WHERE id=target_week;
    IF w.id IS NULL OR w.deleted_at IS NOT NULL
       OR NOT scheduling.staff_read_allowed(w.organization_id) THEN
        RAISE EXCEPTION 'Schedule unavailable' USING ERRCODE='42501';
    END IF;
    RETURN jsonb_build_object(
        'week',to_jsonb(w),
        'slots',coalesce((
            SELECT jsonb_agg(
                to_jsonb(s)||jsonb_build_object(
                    'role_name',r.name,
                    'local_start',s.start_at AT TIME ZONE w.timezone,
                    'local_end',s.end_at AT TIME ZONE w.timezone
                )
                ORDER BY s.start_at,s.role_id,s.id
            )
            FROM scheduling.shift_slot s
            JOIN scheduling.job_role r ON r.id=s.role_id
            WHERE s.schedule_week_id=w.id AND s.active
        ),'[]'::jsonb)
    );
END
$$;

CREATE OR REPLACE FUNCTION scheduling.create_draft_shift(
    target_week uuid,
    target_role uuid,
    expected_week_revision integer,
    shift_start timestamptz,
    shift_end timestamptz,
    shift_note text DEFAULT ''
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    actor_value uuid;
    w scheduling.schedule_week%ROWTYPE;
    local_start timestamp;
    local_end timestamp;
    matches integer;
    endpoint timestamp;
    result jsonb;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.schedule_week WHERE id=target_week;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Schedule unavailable' USING ERRCODE='42501';
    END IF;

    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    SELECT * INTO STRICT w FROM scheduling.schedule_week
    WHERE id=target_week FOR UPDATE;

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
    IF w.state<>'draft' OR w.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Schedule is not a draft' USING ERRCODE='55000';
    END IF;
    IF expected_week_revision IS NULL
       OR expected_week_revision<>w.revision OR w.revision=2147483647 THEN
        RAISE EXCEPTION 'Schedule changed' USING ERRCODE='40001';
    END IF;

    PERFORM 1 FROM scheduling.job_role
    WHERE id=target_role AND organization_id=w.organization_id
      AND location_id=w.location_id AND active
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Active role unavailable' USING ERRCODE='22023';
    END IF;

    IF shift_start IS NULL OR shift_end IS NULL
       OR NOT isfinite(shift_start) OR NOT isfinite(shift_end)
       OR shift_end<=shift_start
       OR shift_note IS NULL OR length(shift_note)>500 THEN
        RAISE EXCEPTION 'Invalid shift' USING ERRCODE='22023';
    END IF;
    local_start := shift_start AT TIME ZONE w.timezone;
    local_end := shift_end AT TIME ZONE w.timezone;

    IF (local_start-w.business_day_start_minute*interval '1 minute')::date
         NOT BETWEEN w.week_start_date AND w.week_start_date+6
       OR local_end::date NOT BETWEEN local_start::date AND local_start::date+1
       OR local_end<=local_start
       OR extract(second FROM local_start)<>0
       OR extract(second FROM local_end)<>0
       OR extract(epoch FROM (shift_end-shift_start)) % 60<>0 THEN
        RAISE EXCEPTION 'Invalid local shift dates or minutes'
            USING ERRCODE='22023';
    END IF;

    -- Independently reject ambiguous local endpoints, including direct
    -- database calls supplying one arbitrarily chosen UTC interpretation.
    FOREACH endpoint IN ARRAY ARRAY[local_start,local_end] LOOP
        SELECT count(*) INTO matches
        FROM (
            SELECT (endpoint AT TIME ZONE 'UTC')
                   + n*interval '1 minute' AS candidate
            FROM generate_series(-1560,1560) g(n)
        ) possible
        WHERE candidate AT TIME ZONE w.timezone=endpoint;
        IF matches<>1 THEN
            RAISE EXCEPTION 'Ambiguous or nonexistent local shift endpoint'
                USING ERRCODE='22023';
        END IF;
    END LOOP;

    INSERT INTO scheduling.shift_slot(
        organization_id,location_id,schedule_week_id,role_id,
        start_at,end_at,note,created_by
    ) VALUES (
        w.organization_id,w.location_id,w.id,target_role,
        shift_start,shift_end,shift_note,actor_value
    ) RETURNING to_jsonb(shift_slot) INTO result;

    UPDATE scheduling.schedule_week
    SET revision=revision+1 WHERE id=w.id;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        w.organization_id,w.location_id,actor_value,
        'schedule.shift_created','location',w.location_id,NULL,result
    );

    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session
        WHERE token_sha256=current_setting('scheduling.session_sha256',true)
          AND revoked_at IS NULL AND expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;
    RETURN scheduling.read_schedule_week(w.id);
END
$$;

CREATE OR REPLACE FUNCTION scheduling.change_draft_shift(
    target_week uuid,
    target_shift uuid,
    expected_shift_revision integer,
    remove_shift boolean,
    target_role uuid,
    expected_week_revision integer,
    shift_start timestamptz,
    shift_end timestamptz,
    shift_note text
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    actor_value uuid;
    w scheduling.schedule_week%ROWTYPE;
    previous scheduling.shift_slot%ROWTYPE;
    local_start timestamp;
    local_end timestamp;
    matches integer;
    endpoint timestamp;
    result jsonb;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.schedule_week WHERE id=target_week;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Schedule unavailable' USING ERRCODE='42501';
    END IF;

    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    SELECT * INTO STRICT w FROM scheduling.schedule_week
    WHERE id=target_week FOR UPDATE;

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
    IF w.state<>'draft' OR w.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Schedule is not a draft' USING ERRCODE='55000';
    END IF;
    IF expected_week_revision IS NULL
       OR expected_week_revision<>w.revision OR w.revision=2147483647 THEN
        RAISE EXCEPTION 'Schedule changed' USING ERRCODE='40001';
    END IF;

    SELECT * INTO previous FROM scheduling.shift_slot
    WHERE id=target_shift AND schedule_week_id=w.id
      AND organization_id=w.organization_id AND location_id=w.location_id
    FOR UPDATE;
    IF previous.id IS NULL THEN
        RAISE EXCEPTION 'Shift unavailable' USING ERRCODE='42501';
    END IF;
    IF expected_shift_revision IS NULL
       OR expected_shift_revision<>previous.revision
       OR previous.revision=2147483647 OR NOT previous.active THEN
        RAISE EXCEPTION 'Shift changed' USING ERRCODE='40001';
    END IF;
    IF remove_shift IS NULL THEN
        RAISE EXCEPTION 'Invalid shift operation' USING ERRCODE='22023';
    END IF;

    IF NOT remove_shift THEN
    PERFORM 1 FROM scheduling.job_role
    WHERE id=target_role AND organization_id=w.organization_id
      AND location_id=w.location_id AND active
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Active role unavailable' USING ERRCODE='22023';
    END IF;

    IF shift_start IS NULL OR shift_end IS NULL
       OR NOT isfinite(shift_start) OR NOT isfinite(shift_end)
       OR shift_end<=shift_start
       OR shift_note IS NULL OR length(shift_note)>500 THEN
        RAISE EXCEPTION 'Invalid shift' USING ERRCODE='22023';
    END IF;
    local_start := shift_start AT TIME ZONE w.timezone;
    local_end := shift_end AT TIME ZONE w.timezone;

    IF (local_start-w.business_day_start_minute*interval '1 minute')::date
         NOT BETWEEN w.week_start_date AND w.week_start_date+6
       OR local_end::date NOT BETWEEN local_start::date AND local_start::date+1
       OR local_end<=local_start
       OR extract(second FROM local_start)<>0
       OR extract(second FROM local_end)<>0
       OR extract(epoch FROM (shift_end-shift_start)) % 60<>0 THEN
        RAISE EXCEPTION 'Invalid local shift dates or minutes'
            USING ERRCODE='22023';
    END IF;

    -- Independently reject ambiguous local endpoints, including direct
    -- database calls supplying one arbitrarily chosen UTC interpretation.
    FOREACH endpoint IN ARRAY ARRAY[local_start,local_end] LOOP
        SELECT count(*) INTO matches
        FROM (
            SELECT (endpoint AT TIME ZONE 'UTC')
                   + n*interval '1 minute' AS candidate
            FROM generate_series(-1560,1560) g(n)
        ) possible
        WHERE candidate AT TIME ZONE w.timezone=endpoint;
        IF matches<>1 THEN
            RAISE EXCEPTION 'Ambiguous or nonexistent local shift endpoint'
                USING ERRCODE='22023';
        END IF;
    END LOOP;

    END IF;

    IF remove_shift THEN
        UPDATE scheduling.shift_slot
        SET active=false, revision=revision+1
        WHERE id=previous.id
        RETURNING to_jsonb(shift_slot) INTO result;
    ELSE
        UPDATE scheduling.shift_slot
        SET role_id=target_role, start_at=shift_start, end_at=shift_end,
            note=shift_note, revision=revision+1
        WHERE id=previous.id
        RETURNING to_jsonb(shift_slot) INTO result;
    END IF;

    UPDATE scheduling.schedule_week
    SET revision=revision+1 WHERE id=w.id;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        w.organization_id,w.location_id,actor_value,
        CASE WHEN remove_shift THEN 'schedule.shift_removed'
             ELSE 'schedule.shift_updated' END,
        'location',w.location_id,to_jsonb(previous),result
    );

    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session
        WHERE token_sha256=current_setting('scheduling.session_sha256',true)
          AND revoked_at IS NULL AND expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;
    RETURN scheduling.read_schedule_week(w.id);
END
$$;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0026_remove_draft_schedule_weeks', :'migration_sha256');
