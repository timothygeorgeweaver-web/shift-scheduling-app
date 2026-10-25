DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
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
    IF w.state<>'draft' THEN
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
    IF w.state<>'draft' THEN
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
VALUES ('0024_business_week_shift_boundaries', :'migration_sha256');
