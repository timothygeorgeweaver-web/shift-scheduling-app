DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.edit_business_day_policy(
    target_location uuid,
    expected_location_revision integer,
    rollover_minute integer
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    actor_value uuid;
    previous scheduling.location%ROWTYPE;
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
      AND u.active AND m.active AND p.permission='administrator'
    FOR SHARE OF s,u,m,p;

    IF actor_value IS NULL THEN
        RAISE EXCEPTION 'Administrator required' USING ERRCODE='42501';
    END IF;

    SELECT * INTO STRICT previous FROM scheduling.location
    WHERE id=target_location FOR UPDATE;

    IF expected_location_revision IS NULL
       OR expected_location_revision<>previous.revision
       OR previous.revision=2147483647 THEN
        RAISE EXCEPTION 'Location changed' USING ERRCODE='40001';
    END IF;
    IF rollover_minute IS NULL OR rollover_minute NOT BETWEEN 0 AND 1439
       OR rollover_minute % 15<>0 THEN
        RAISE EXCEPTION 'Invalid rollover' USING ERRCODE='22023';
    END IF;

    IF rollover_minute=previous.business_day_start_minute THEN
        RETURN jsonb_build_object(
            'location_id',previous.id,
            'location_revision',previous.revision,
            'business_day_start_minute',previous.business_day_start_minute,
            'timezone',previous.timezone
        );
    END IF;

    UPDATE scheduling.location
    SET business_day_start_minute=rollover_minute,revision=revision+1
    WHERE id=target_location;

    result := jsonb_build_object(
        'location_id',target_location,
        'location_revision',previous.revision+1,
        'business_day_start_minute',rollover_minute,
        'timezone',previous.timezone
    );

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_value,target_location,actor_value,
        'settings.business_day_updated','location',target_location,
        jsonb_build_object(
            'business_day_start_minute',previous.business_day_start_minute,
            'location_revision',previous.revision
        ),
        result
    );

    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session
        WHERE token_sha256=current_setting('scheduling.session_sha256',true)
          AND revoked_at IS NULL AND expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;
    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION
scheduling.edit_business_day_policy(uuid,integer,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.edit_business_day_policy(uuid,integer,integer)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0023_business_day_policy', :'migration_sha256');
