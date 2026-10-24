DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.schedule_template (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    name text NOT NULL CHECK (
        name=btrim(name) AND length(name) BETWEEN 1 AND 100
    ),
    week_start_day smallint NOT NULL CHECK (week_start_day BETWEEN 0 AND 6),
    snapshot jsonb NOT NULL CHECK (
        jsonb_typeof(snapshot->'slots')='array'
        AND jsonb_array_length(snapshot->'slots')<=500
    ),
    revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
    active boolean NOT NULL DEFAULT true,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (organization_id,location_id)
      REFERENCES scheduling.location(organization_id,id),
    FOREIGN KEY (organization_id,created_by)
      REFERENCES scheduling.organization_member(organization_id,user_id)
);
CREATE UNIQUE INDEX schedule_template_location_name_idx
ON scheduling.schedule_template(location_id,lower(name));
ALTER TABLE scheduling.schedule_template ENABLE ROW LEVEL SECURITY;
-- No direct runtime table grants.

CREATE FUNCTION scheduling.list_schedule_templates(target_location uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.location WHERE id=target_location;
    IF organization_value IS NULL
       OR NOT scheduling.staff_read_allowed(organization_value) THEN
        RAISE EXCEPTION 'Templates unavailable' USING ERRCODE='42501';
    END IF;
    RETURN coalesce((
        SELECT jsonb_agg(
            (to_jsonb(t)-'snapshot') || jsonb_build_object(
                'shift_count',jsonb_array_length(t.snapshot->'slots')
            ) ORDER BY lower(t.name),t.id
        )
        FROM scheduling.schedule_template t
        WHERE t.location_id=target_location AND t.active
    ),'[]'::jsonb);
END
$$;

CREATE FUNCTION scheduling.read_schedule_template(target_template uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    t scheduling.schedule_template%ROWTYPE;
BEGIN
    SELECT * INTO t FROM scheduling.schedule_template
    WHERE id=target_template AND active;
    IF t.id IS NULL OR NOT scheduling.staff_read_allowed(t.organization_id) THEN
        RAISE EXCEPTION 'Template unavailable' USING ERRCODE='42501';
    END IF;
    RETURN to_jsonb(t);
END
$$;

CREATE FUNCTION scheduling.save_schedule_template(
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
            'week_start_day',w.week_start_day
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
        organization_id,location_id,name,week_start_day,snapshot,created_by
    ) VALUES (
        organization_value,target_location,template_name,
        w.week_start_day,snapshot_value,actor_value
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

REVOKE ALL ON FUNCTION scheduling.list_schedule_templates(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION scheduling.read_schedule_template(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION
scheduling.save_schedule_template(uuid,uuid,integer,text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION scheduling.list_schedule_templates(uuid)
TO shift_scheduling_app;
GRANT EXECUTE ON FUNCTION scheduling.read_schedule_template(uuid)
TO shift_scheduling_app;
GRANT EXECUTE ON FUNCTION
scheduling.save_schedule_template(uuid,uuid,integer,text)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0019_schedule_templates', :'migration_sha256');
