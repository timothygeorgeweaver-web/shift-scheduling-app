DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

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
            WHERE w.location_id=target_location
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
        WHERE location_id=target_location
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

CREATE OR REPLACE FUNCTION scheduling.save_schedule_template(
    target_location uuid, source_week uuid,
    expected_source_revision integer, template_name text
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    actor_value uuid;
    w scheduling.schedule_week%ROWTYPE;
    source_data jsonb;
    snapshot_value jsonb;
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
    WHERE id=source_week AND location_id=target_location
      AND organization_id=organization_value FOR SHARE;
    IF w.id IS NULL THEN
        RAISE EXCEPTION 'Source unavailable' USING ERRCODE='42501';
    END IF;
    IF expected_source_revision IS NULL
       OR expected_source_revision<>w.revision THEN
        RAISE EXCEPTION 'Source changed' USING ERRCODE='40001';
    END IF;
    IF template_name IS NULL OR template_name<>btrim(template_name)
       OR length(template_name) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'Invalid template name' USING ERRCODE='22023';
    END IF;

    source_data := scheduling.read_schedule_week(w.id);
    IF jsonb_array_length(source_data->'slots')>500 THEN
        RAISE EXCEPTION 'Template exceeds 500 shifts' USING ERRCODE='22023';
    END IF;

    SELECT jsonb_build_object(
        'week',jsonb_build_object(
            'week_start_date',w.week_start_date,
            'week_start_day',w.week_start_day,
            'business_day_start_minute',w.business_day_start_minute
        ),
        'slots',coalesce(jsonb_agg(jsonb_build_object(
            'role_id',s->'role_id',
            'local_start',s->'local_start',
            'local_end',s->'local_end',
            'note',s->'note'
        ) ORDER BY ordinal),'[]'::jsonb)
    ) INTO snapshot_value
    FROM jsonb_array_elements(source_data->'slots')
         WITH ORDINALITY AS entries(s,ordinal);

    INSERT INTO scheduling.schedule_template(
        organization_id,location_id,name,week_start_day,snapshot,created_by,
        business_day_start_minute
    ) VALUES (
        organization_value,target_location,template_name,
        w.week_start_day,snapshot_value,actor_value,w.business_day_start_minute
    ) RETURNING to_jsonb(schedule_template) INTO result;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_value,target_location,actor_value,
        'schedule.template_saved','location',target_location,NULL,
        (result-'snapshot') || jsonb_build_object(
            'source_week_id',w.id,'source_revision',w.revision,
            'shift_count',jsonb_array_length(snapshot_value->'slots')
        )
    );

    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session
        WHERE token_sha256=current_setting('scheduling.session_sha256',true)
          AND revoked_at IS NULL AND expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;
    RETURN (result-'snapshot') || jsonb_build_object(
        'shift_count',jsonb_array_length(snapshot_value->'slots')
    );
END
$$;

CREATE OR REPLACE FUNCTION scheduling.change_schedule_template(
    target_template uuid,
    expected_template_revision integer,
    template_name text,
    template_active boolean,
    source_week uuid,
    expected_source_revision integer
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    actor_value uuid;
    t scheduling.schedule_template%ROWTYPE;
    source_data jsonb;
    snapshot_value jsonb;
    weekday_value integer;
    rollover_value integer;
    result jsonb;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.schedule_template WHERE id=target_template;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Template unavailable' USING ERRCODE='42501';
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

    SELECT * INTO t FROM scheduling.schedule_template
    WHERE id=target_template AND active FOR UPDATE;
    IF t.id IS NULL THEN
        RAISE EXCEPTION 'Template unavailable' USING ERRCODE='42501';
    END IF;
    IF expected_template_revision IS NULL
       OR expected_template_revision<>t.revision
       OR t.revision=2147483647 THEN
        RAISE EXCEPTION 'Template changed' USING ERRCODE='40001';
    END IF;
    IF template_name IS NULL OR template_name<>btrim(template_name)
       OR length(template_name) NOT BETWEEN 1 AND 100
       OR template_active IS NULL
       OR (source_week IS NULL)<>(expected_source_revision IS NULL) THEN
        RAISE EXCEPTION 'Invalid template change' USING ERRCODE='22023';
    END IF;

    snapshot_value := t.snapshot;
    weekday_value := t.week_start_day;
    rollover_value := t.business_day_start_minute;
    IF source_week IS NOT NULL THEN
        source_data := scheduling.read_schedule_week(source_week);
        IF source_data->'week'->>'location_id'<>t.location_id::text THEN
            RAISE EXCEPTION 'Source unavailable' USING ERRCODE='42501';
        END IF;
        IF (source_data->'week'->>'revision')::integer<>expected_source_revision THEN
            RAISE EXCEPTION 'Source changed' USING ERRCODE='40001';
        END IF;
        IF jsonb_array_length(source_data->'slots')>500 THEN
            RAISE EXCEPTION 'Template exceeds 500 shifts' USING ERRCODE='22023';
        END IF;
        weekday_value := (source_data->'week'->>'week_start_day')::integer;
        rollover_value := (source_data->'week'->>'business_day_start_minute')::integer;
        SELECT jsonb_build_object(
            'week',jsonb_build_object(
                'week_start_date',source_data->'week'->'week_start_date',
                'week_start_day',weekday_value,
                'business_day_start_minute',rollover_value
            ),
            'slots',coalesce(jsonb_agg(jsonb_build_object(
                'role_id',s->'role_id',
                'local_start',s->'local_start',
                'local_end',s->'local_end',
                'note',s->'note'
            ) ORDER BY ordinal),'[]'::jsonb)
        ) INTO snapshot_value
        FROM jsonb_array_elements(source_data->'slots')
             WITH ORDINALITY AS entries(s,ordinal);
    END IF;

    UPDATE scheduling.schedule_template
    SET name=template_name, active=template_active,
        snapshot=snapshot_value, week_start_day=weekday_value,
        business_day_start_minute=rollover_value,
        revision=revision+1
    WHERE id=t.id
    RETURNING to_jsonb(schedule_template) INTO result;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        t.organization_id,t.location_id,actor_value,
        'schedule.template_changed','location',t.location_id,
        to_jsonb(t),result || jsonb_build_object(
            'source_week_id',source_week,
            'source_revision',expected_source_revision
        )
    );

    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session
        WHERE token_sha256=current_setting('scheduling.session_sha256',true)
          AND revoked_at IS NULL AND expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;
    RETURN (result-'snapshot') || jsonb_build_object(
        'shift_count',jsonb_array_length(snapshot_value->'slots')
    );
END
$$;

CREATE OR REPLACE FUNCTION scheduling.save_template_pattern(
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
    rollover_value integer;
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
    rollover_value := l.business_day_start_minute;
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
        rollover_value := previous.business_day_start_minute;
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
       OR (pattern->'week'->>'week_start_day') IS DISTINCT FROM weekday_value::text
       OR (pattern->'week'->>'business_day_start_minute')
          IS DISTINCT FROM rollover_value::text THEN
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
        IF (start_value-rollover_value*interval '1 minute')::date
             NOT BETWEEN anchor AND anchor+6
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
            organization_id,location_id,name,week_start_day,snapshot,created_by,
            business_day_start_minute
        ) VALUES (
            organization_value,target_location,template_name,
            weekday_value,pattern,actor_value,rollover_value
        ) RETURNING to_jsonb(schedule_template) INTO result;
    ELSE
        UPDATE scheduling.schedule_template
        SET name=template_name,snapshot=pattern,revision=revision+1,
            business_day_start_minute=rollover_value
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

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0025_capture_business_day_policy', :'migration_sha256');
