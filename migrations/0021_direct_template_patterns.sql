DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.save_template_pattern(
    target_location uuid,
    target_template uuid,
    expected_template_revision integer,
    expected_location_revision integer,
    template_name text,
    pattern jsonb
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    actor_value uuid;
    l scheduling.location%ROWTYPE;
    previous scheduling.schedule_template%ROWTYPE;
    weekday_value integer;
    anchor date;
    entry jsonb;
    start_value timestamp;
    end_value timestamp;
    role_value uuid;
    role_values uuid[] := ARRAY[]::uuid[];
    locked_count integer;
    result jsonb;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.location WHERE id=target_location;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Location unavailable' USING ERRCODE='42501';
    END IF;
    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    SELECT * INTO STRICT l FROM scheduling.location
    WHERE id=target_location FOR SHARE;

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
    IF expected_location_revision IS NULL
       OR expected_location_revision<>l.revision THEN
        RAISE EXCEPTION 'Location changed' USING ERRCODE='40001';
    END IF;

    weekday_value := l.week_start;
    IF target_template IS NULL THEN
        IF expected_template_revision IS DISTINCT FROM 0 THEN
            RAISE EXCEPTION 'Invalid new template revision' USING ERRCODE='40001';
        END IF;
    ELSE
        SELECT * INTO previous FROM scheduling.schedule_template
        WHERE id=target_template AND location_id=target_location
          AND organization_id=organization_value AND active FOR UPDATE;
        IF previous.id IS NULL THEN
            RAISE EXCEPTION 'Template unavailable' USING ERRCODE='42501';
        END IF;
        IF expected_template_revision IS NULL
           OR expected_template_revision<>previous.revision
           OR previous.revision=2147483647 THEN
            RAISE EXCEPTION 'Template changed' USING ERRCODE='40001';
        END IF;
        weekday_value := previous.week_start_day;
    END IF;

    IF template_name IS NULL OR template_name<>btrim(template_name)
       OR length(template_name) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'Invalid template name' USING ERRCODE='22023';
    END IF;
    IF pattern IS NULL
       OR jsonb_typeof(pattern) IS DISTINCT FROM 'object'
       OR jsonb_typeof(pattern->'week') IS DISTINCT FROM 'object'
       OR jsonb_typeof(pattern->'slots') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Invalid pattern structure' USING ERRCODE='22023';
    END IF;
    anchor := date '2000-01-03'+weekday_value;
    IF jsonb_array_length(pattern->'slots')>500
       OR (pattern->'week'->>'week_start_date') IS DISTINCT FROM anchor::text
       OR (pattern->'week'->>'week_start_day') IS DISTINCT FROM weekday_value::text THEN
        RAISE EXCEPTION 'Invalid generic week' USING ERRCODE='22023';
    END IF;

    FOR entry IN SELECT value FROM jsonb_array_elements(pattern->'slots') LOOP
        IF jsonb_typeof(entry) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'Invalid shift object' USING ERRCODE='22023';
        END IF;
        IF NOT (entry ?& ARRAY['role_id','local_start','local_end','note'])
           OR (SELECT count(*) FROM jsonb_object_keys(entry))<>4
           OR jsonb_typeof(entry->'role_id') IS DISTINCT FROM 'string'
           OR jsonb_typeof(entry->'local_start') IS DISTINCT FROM 'string'
           OR jsonb_typeof(entry->'local_end') IS DISTINCT FROM 'string'
           OR jsonb_typeof(entry->'note') IS DISTINCT FROM 'string'
           OR length(entry->>'note')>500 THEN
            RAISE EXCEPTION 'Invalid shift fields' USING ERRCODE='22023';
        END IF;
        IF (entry->>'local_start') !~
             '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:00$'
           OR (entry->>'local_end') !~
             '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:00$' THEN
            RAISE EXCEPTION 'Invalid local endpoints' USING ERRCODE='22023';
        END IF;
        BEGIN
            role_value := (entry->>'role_id')::uuid;
            start_value := (entry->>'local_start')::timestamp;
            end_value := (entry->>'local_end')::timestamp;
        EXCEPTION WHEN invalid_text_representation
                    OR invalid_datetime_format OR datetime_field_overflow THEN
            RAISE EXCEPTION 'Invalid role or time' USING ERRCODE='22023';
        END;
        IF start_value::date NOT BETWEEN anchor AND anchor+6
           OR end_value::date NOT BETWEEN start_value::date AND start_value::date+1
           OR end_value<=start_value
           OR extract(minute FROM start_value)::integer % 15<>0
           OR extract(minute FROM end_value)::integer % 15<>0 THEN
            RAISE EXCEPTION 'Invalid quarter-hour pattern' USING ERRCODE='22023';
        END IF;
        role_values := array_append(role_values,role_value);
    END LOOP;

    PERFORM 1 FROM scheduling.job_role
    WHERE id=ANY(role_values) AND location_id=target_location
      AND organization_id=organization_value AND active
    ORDER BY id FOR SHARE;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count<>(SELECT count(DISTINCT value) FROM unnest(role_values) AS r(value)) THEN
        RAISE EXCEPTION 'Active role unavailable' USING ERRCODE='22023';
    END IF;

    IF target_template IS NULL THEN
        INSERT INTO scheduling.schedule_template(
            organization_id,location_id,name,week_start_day,snapshot,created_by
        ) VALUES (
            organization_value,target_location,template_name,
            weekday_value,pattern,actor_value
        ) RETURNING to_jsonb(schedule_template) INTO result;
    ELSE
        UPDATE scheduling.schedule_template
        SET name=template_name,snapshot=pattern,revision=revision+1
        WHERE id=previous.id
        RETURNING to_jsonb(schedule_template) INTO result;
    END IF;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_value,target_location,actor_value,
        'schedule.template_pattern_saved','location',target_location,
        CASE WHEN previous.id IS NULL THEN NULL ELSE to_jsonb(previous) END,
        result
    );

    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session
        WHERE token_sha256=current_setting('scheduling.session_sha256',true)
          AND revoked_at IS NULL AND expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;
    RETURN (result-'snapshot') || jsonb_build_object(
        'shift_count',jsonb_array_length(pattern->'slots')
    );
END
$$;

REVOKE ALL ON FUNCTION
scheduling.save_template_pattern(uuid,uuid,integer,integer,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.save_template_pattern(uuid,uuid,integer,integer,text,jsonb)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0021_direct_template_patterns', :'migration_sha256');
