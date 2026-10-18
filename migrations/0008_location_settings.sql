DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

ALTER TABLE scheduling.organization
ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0);
ALTER TABLE scheduling.location
ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0);

ALTER TABLE scheduling.audit_event
DROP CONSTRAINT audit_event_entity_type_check;
ALTER TABLE scheduling.audit_event
ADD CONSTRAINT audit_event_entity_type_check CHECK (
    entity_type IN (
        'employee','job_role','employee_qualification','organization','location'
    )
);

CREATE FUNCTION scheduling.edit_location_settings(
    target_location uuid,
    expected_organization_revision integer,
    expected_location_revision integer,
    business_name text,
    location_name text,
    location_timezone text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_id_value uuid;
    organization_row scheduling.organization%ROWTYPE;
    location_row scheduling.location%ROWTYPE;
    actor_id uuid;
BEGIN
    SELECT l.organization_id INTO organization_id_value
    FROM scheduling.location l WHERE l.id=target_location;

    IF organization_id_value IS NULL THEN
        RAISE EXCEPTION 'Settings unavailable' USING ERRCODE='42501';
    END IF;

    SELECT * INTO organization_row FROM scheduling.organization o
    WHERE o.id=organization_id_value FOR UPDATE;

    SELECT u.id INTO actor_id
    FROM scheduling.auth_session s
    JOIN scheduling.app_user u ON u.id=s.user_id
    JOIN scheduling.organization_member m ON m.user_id=u.id
    JOIN scheduling.member_permission p
      ON p.organization_id=m.organization_id AND p.user_id=m.user_id
    WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
      AND s.revoked_at IS NULL AND s.expires_at>now()
      AND u.active AND m.active
      AND m.organization_id=organization_id_value
      AND p.permission='administrator';

    IF actor_id IS NULL THEN
        RAISE EXCEPTION 'Administrator access required' USING ERRCODE='42501';
    END IF;

    SELECT * INTO location_row FROM scheduling.location l
    WHERE l.id=target_location FOR UPDATE;

    IF expected_organization_revision IS NULL
       OR expected_location_revision IS NULL
       OR organization_row.revision<>expected_organization_revision
       OR location_row.revision<>expected_location_revision THEN
        RAISE EXCEPTION 'Settings changed' USING ERRCODE='40001';
    END IF;

    IF business_name IS NULL OR location_name IS NULL
       OR length(btrim(business_name)) NOT BETWEEN 1 AND 200
       OR length(btrim(location_name)) NOT BETWEEN 1 AND 200
       OR location_timezone IS NULL
       OR length(location_timezone)>100
       OR NOT EXISTS (
           SELECT FROM pg_timezone_names z WHERE z.name=location_timezone
       ) THEN
        RAISE EXCEPTION 'Invalid settings' USING ERRCODE='22023';
    END IF;

    UPDATE scheduling.organization
    SET name=btrim(business_name), revision=revision+1
    WHERE id=organization_id_value;

    UPDATE scheduling.location
    SET name=btrim(location_name), timezone=location_timezone,
        revision=revision+1
    WHERE id=target_location;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_id_value,target_location,actor_id,
        'settings.business_updated','organization',organization_id_value,
        jsonb_build_object('name',organization_row.name,
                           'revision',organization_row.revision),
        jsonb_build_object('name',btrim(business_name),
                           'revision',organization_row.revision+1)
    ), (
        organization_id_value,target_location,actor_id,
        'settings.location_updated','location',target_location,
        jsonb_build_object('name',location_row.name,
                           'timezone',location_row.timezone,
                           'revision',location_row.revision),
        jsonb_build_object('name',btrim(location_name),
                           'timezone',location_timezone,
                           'revision',location_row.revision+1)
    );

    RETURN jsonb_build_object(
        'organization_id',organization_id_value,
        'location_id',target_location,
        'business_name',btrim(business_name),
        'location_name',btrim(location_name),
        'timezone',location_timezone,
        'organization_revision',organization_row.revision+1,
        'location_revision',location_row.revision+1
    );
END
$$;

REVOKE ALL ON FUNCTION scheduling.edit_location_settings(
    uuid,integer,integer,text,text,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.edit_location_settings(
    uuid,integer,integer,text,text,text
) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0008_location_settings', :'migration_sha256');
