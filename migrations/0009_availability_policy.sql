DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

ALTER TABLE scheduling.location
ADD COLUMN week_start smallint NOT NULL DEFAULT 0
    CHECK (week_start BETWEEN 0 AND 6),
ADD COLUMN availability_cutoff_days integer NOT NULL DEFAULT 7
    CHECK (availability_cutoff_days BETWEEN 0 AND 365),
ADD COLUMN availability_cutoff_time time NOT NULL DEFAULT '17:00'
    CHECK (
        availability_cutoff_time < time '24:00'
        AND extract(second FROM availability_cutoff_time)=0
    );

CREATE FUNCTION scheduling.edit_availability_policy(
    target_location uuid,
    expected_revision integer,
    new_week_start integer,
    new_cutoff_days integer,
    new_cutoff_time text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    location_row scheduling.location%ROWTYPE;
    organization_id_value uuid;
    actor_id uuid;
    previous_state jsonb;
    result jsonb;
BEGIN
    SELECT l.organization_id INTO organization_id_value
    FROM scheduling.location l WHERE l.id=target_location;
    IF organization_id_value IS NULL THEN
        RAISE EXCEPTION 'Policy unavailable' USING ERRCODE='42501';
    END IF;

    PERFORM 1 FROM scheduling.organization o
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

    IF expected_revision IS NULL OR expected_revision<>location_row.revision THEN
        RAISE EXCEPTION 'Location policy changed' USING ERRCODE='40001';
    END IF;

    IF new_week_start IS NULL OR new_week_start NOT BETWEEN 0 AND 6
       OR new_cutoff_days IS NULL OR new_cutoff_days NOT BETWEEN 0 AND 365
       OR new_cutoff_time IS NULL
       OR new_cutoff_time !~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' THEN
        RAISE EXCEPTION 'Invalid availability policy' USING ERRCODE='22023';
    END IF;

    previous_state := jsonb_build_object(
        'location_id',target_location,
        'name',location_row.name,
        'timezone',location_row.timezone,
        'week_start',location_row.week_start,
        'cutoff_days_before',location_row.availability_cutoff_days,
        'cutoff_time',left(location_row.availability_cutoff_time::text,5),
        'revision',location_row.revision
    );

    UPDATE scheduling.location
    SET week_start=new_week_start,
        availability_cutoff_days=new_cutoff_days,
        availability_cutoff_time=new_cutoff_time::time,
        revision=revision+1
    WHERE id=target_location;

    result := previous_state || jsonb_build_object(
        'week_start',new_week_start,
        'cutoff_days_before',new_cutoff_days,
        'cutoff_time',new_cutoff_time,
        'revision',location_row.revision+1
    );

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,
        action,entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_id_value,target_location,actor_id,
        'availability.policy_updated','location',target_location,
        previous_state,result
    );

    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION scheduling.edit_availability_policy(
    uuid,integer,integer,integer,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.edit_availability_policy(
    uuid,integer,integer,integer,text
) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0009_availability_policy', :'migration_sha256');
