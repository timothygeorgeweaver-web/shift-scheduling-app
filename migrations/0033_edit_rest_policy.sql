DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.edit_rest_policy(
    target_location uuid,
    expected_location_revision integer,
    window_start integer,
    window_end integer,
    required_minutes integer
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
    IF window_start IS NULL OR window_end IS NULL
       OR required_minutes IS NULL
       OR window_start NOT BETWEEN 0 AND 1439 OR window_start % 15<>0
       OR window_end NOT BETWEEN 0 AND 1439 OR window_end % 15<>0
       OR required_minutes NOT BETWEEN 0 AND 1440
       OR required_minutes % 15<>0 THEN
        RAISE EXCEPTION 'Invalid rest policy' USING ERRCODE='22023';
    END IF;

    result := jsonb_build_object(
        'location_id',target_location,
        'location_revision',previous.revision,
        'window_start',window_start,'window_end',window_end,
        'rest_minutes',required_minutes
    );
    IF window_start=previous.rest_window_start_minute
       AND window_end=previous.rest_window_end_minute
       AND required_minutes=previous.minimum_rest_minutes THEN
        RETURN result;
    END IF;

    -- Temporary transition guard: replace this with audited affected-
    -- assignment review before allowing policy edits over live assignments.
    IF EXISTS (
        SELECT FROM scheduling.shift_assignment a
        JOIN scheduling.shift_slot s ON s.id=a.shift_slot_id
        JOIN scheduling.schedule_week w ON w.id=s.schedule_week_id
        WHERE a.location_id=target_location
          AND a.active AND s.active AND w.deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'Assignment review required before changing rest policy'
            USING ERRCODE='55000';
    END IF;

    UPDATE scheduling.location
    SET rest_window_start_minute=window_start,
        rest_window_end_minute=window_end,
        minimum_rest_minutes=required_minutes,
        revision=revision+1
    WHERE id=target_location;
    result := result || jsonb_build_object(
        'location_revision',previous.revision+1
    );

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_value,target_location,actor_value,
        'settings.rest_policy_updated','location',target_location,
        jsonb_build_object(
            'location_revision',previous.revision,
            'window_start',previous.rest_window_start_minute,
            'window_end',previous.rest_window_end_minute,
            'rest_minutes',previous.minimum_rest_minutes
        ),result
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
scheduling.edit_rest_policy(uuid,integer,integer,integer,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.edit_rest_policy(uuid,integer,integer,integer,integer)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0033_edit_rest_policy', :'migration_sha256');
