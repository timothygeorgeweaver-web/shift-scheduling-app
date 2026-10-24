DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.lock_my_planner_scope(target_location uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    employee_value uuid;
    profile jsonb;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.location WHERE id=target_location;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Employee profile unavailable' USING ERRCODE='42501';
    END IF;

    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    PERFORM 1 FROM scheduling.location
    WHERE id=target_location FOR UPDATE;

    SELECT e.id INTO employee_value
    FROM scheduling.auth_session s
    JOIN scheduling.app_user u ON u.id=s.user_id
    JOIN scheduling.organization_member m
      ON m.user_id=u.id AND m.organization_id=organization_value
    JOIN scheduling.employee e
      ON e.organization_id=m.organization_id AND e.user_id=u.id
    WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
      AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
      AND u.active AND m.active AND e.active
      AND e.location_id=target_location
    FOR UPDATE OF e FOR SHARE OF s,u,m;

    IF employee_value IS NULL THEN
        RAISE EXCEPTION 'Employee profile unavailable' USING ERRCODE='42501';
    END IF;

    SELECT x.value INTO profile
    FROM jsonb_array_elements(scheduling.my_unavailability_profiles()) x(value)
    WHERE x.value->>'employee_id'=employee_value::text;
    IF profile IS NULL THEN
        RAISE EXCEPTION 'Employee profile unavailable' USING ERRCODE='42501';
    END IF;
    RETURN profile;
END
$$;

REVOKE ALL ON FUNCTION
scheduling.lock_my_planner_scope(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.lock_my_planner_scope(uuid) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0013_planner_bulk_scope', :'migration_sha256');
