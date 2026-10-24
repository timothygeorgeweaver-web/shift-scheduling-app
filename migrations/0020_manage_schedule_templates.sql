DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.change_schedule_template(
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
        SELECT jsonb_build_object(
            'week',jsonb_build_object(
                'week_start_date',source_data->'week'->'week_start_date',
                'week_start_day',weekday_value
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

REVOKE ALL ON FUNCTION
scheduling.change_schedule_template(uuid,integer,text,boolean,uuid,integer)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.change_schedule_template(uuid,integer,text,boolean,uuid,integer)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0020_manage_schedule_templates', :'migration_sha256');
