-- Caller supplies transaction, owner and checksum.
DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

ALTER TABLE scheduling.organization_member
ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0);

CREATE FUNCTION scheduling.employee_account_access(
    target_location uuid,
    target_employee uuid,
    new_state jsonb DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    organization_id_value uuid;
    target_user uuid;
    actor_id uuid;
    previous_state jsonb;
    result jsonb;
    requested_permissions text[];
    requested_active boolean;
    requested_revision integer;
BEGIN
    SELECT l.organization_id INTO organization_id_value
    FROM scheduling.location l WHERE l.id = target_location;

    IF organization_id_value IS NULL THEN
        RAISE EXCEPTION 'Account unavailable' USING ERRCODE='42501';
    END IF;

    IF new_state IS NOT NULL THEN
        -- Serialize account-access mutations within this business.
        PERFORM 1 FROM scheduling.organization o
        WHERE o.id = organization_id_value FOR UPDATE;
    END IF;

    SELECT u.id INTO actor_id
    FROM scheduling.auth_session s
    JOIN scheduling.app_user u ON u.id=s.user_id
    JOIN scheduling.organization_member m ON m.user_id=u.id
    JOIN scheduling.member_permission p
      ON p.organization_id=m.organization_id AND p.user_id=m.user_id
    WHERE s.token_sha256 =
          current_setting('scheduling.session_sha256',true)
      AND s.revoked_at IS NULL AND s.expires_at > now()
      AND u.active AND m.active
      AND m.organization_id=organization_id_value
      AND p.permission='administrator';

    IF actor_id IS NULL THEN
        RAISE EXCEPTION 'Administrator access required' USING ERRCODE='42501';
    END IF;

    SELECT e.user_id INTO target_user
    FROM scheduling.employee e
    WHERE e.id=target_employee AND e.location_id=target_location;

    IF target_user IS NULL THEN
        RAISE EXCEPTION 'Linked account not found' USING ERRCODE='P0002';
    END IF;

    IF new_state IS NOT NULL THEN
        PERFORM 1 FROM scheduling.organization_member m
        WHERE m.organization_id=organization_id_value
          AND m.user_id=target_user
        FOR UPDATE;
    END IF;

    SELECT jsonb_build_object(
        'employee_id',target_employee,
        'user_id',u.id,
        'email',u.email,
        'user_active',u.active,
        'active',m.active,
        'revision',m.revision,
        'permissions',coalesce((
            SELECT jsonb_agg(p.permission ORDER BY p.permission)
            FROM scheduling.member_permission p
            WHERE p.organization_id=m.organization_id AND p.user_id=m.user_id
        ),'[]'::jsonb)
    ) INTO previous_state
    FROM scheduling.organization_member m
    JOIN scheduling.app_user u ON u.id=m.user_id
    WHERE m.organization_id=organization_id_value AND m.user_id=target_user;

    IF previous_state IS NULL THEN
        RAISE EXCEPTION 'Linked account not found' USING ERRCODE='P0002';
    END IF;

    IF new_state IS NULL THEN
        RETURN previous_state;
    END IF;

    IF jsonb_typeof(new_state) IS DISTINCT FROM 'object'
       OR NOT (new_state ?& ARRAY['active','permissions','expected_revision'])
       OR (new_state - ARRAY['active','permissions','expected_revision'])
          <> '{}'::jsonb THEN
        RAISE EXCEPTION 'Invalid account access input' USING ERRCODE='22023';
    END IF;

    IF jsonb_typeof(new_state->'active') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(new_state->'permissions') IS DISTINCT FROM 'array'
       OR jsonb_typeof(new_state->'expected_revision') IS DISTINCT FROM 'number'
       OR (new_state->>'expected_revision') !~ '^[1-9][0-9]*$' THEN
        RAISE EXCEPTION 'Invalid account access input' USING ERRCODE='22023';
    END IF;

    IF EXISTS (
        SELECT FROM jsonb_array_elements(new_state->'permissions') v
        WHERE jsonb_typeof(v) IS DISTINCT FROM 'string'
           OR v #>> '{}' NOT IN ('administrator','scheduler','employee')
    ) THEN
        RAISE EXCEPTION 'Invalid permission' USING ERRCODE='22023';
    END IF;

    requested_permissions := ARRAY(
        SELECT DISTINCT value
        FROM jsonb_array_elements_text(new_state->'permissions')
        ORDER BY value
    );
    IF cardinality(requested_permissions) < 1 THEN
        RAISE EXCEPTION 'Select at least one permission' USING ERRCODE='22023';
    END IF;

    requested_active := (new_state->>'active')::boolean;
    requested_revision := (new_state->>'expected_revision')::integer;

    IF requested_revision <> (previous_state->>'revision')::integer THEN
        RAISE EXCEPTION 'Account access changed' USING ERRCODE='40001';
    END IF;

    IF requested_active AND NOT (previous_state->>'user_active')::boolean THEN
        RAISE EXCEPTION 'Global account is disabled' USING ERRCODE='40001';
    END IF;

    IF (previous_state->>'active')::boolean
       AND (previous_state->>'user_active')::boolean
       AND previous_state->'permissions' ? 'administrator'
       AND (
           NOT requested_active
           OR NOT ('administrator'=ANY(requested_permissions))
       )
       AND NOT EXISTS (
           SELECT FROM scheduling.organization_member m
           JOIN scheduling.app_user u ON u.id=m.user_id
           JOIN scheduling.member_permission p
             ON p.organization_id=m.organization_id AND p.user_id=m.user_id
           WHERE m.organization_id=organization_id_value
             AND m.user_id<>target_user
             AND m.active AND u.active AND p.permission='administrator'
       ) THEN
        RAISE EXCEPTION 'Keep at least one active administrator'
            USING ERRCODE='40001';
    END IF;

    UPDATE scheduling.organization_member
    SET active=requested_active, revision=revision+1
    WHERE organization_id=organization_id_value AND user_id=target_user;

    DELETE FROM scheduling.member_permission
    WHERE organization_id=organization_id_value AND user_id=target_user;

    INSERT INTO scheduling.member_permission(organization_id,user_id,permission)
    SELECT organization_id_value,target_user,unnest(requested_permissions);

    UPDATE scheduling.auth_session
    SET revoked_at=coalesce(revoked_at,now())
    WHERE user_id=target_user AND revoked_at IS NULL;

    result := previous_state || jsonb_build_object(
        'active',requested_active,
        'permissions',to_jsonb(requested_permissions),
        'revision',requested_revision+1
    );

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,
        action,entity_type,entity_id,before_data,after_data
    )
    VALUES (
        organization_id_value,target_location,actor_id,
        'employee.account_access_changed','employee',target_employee,
        previous_state,result
    );

    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION scheduling.employee_account_access(uuid,uuid,jsonb)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.employee_account_access(uuid,uuid,jsonb)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0007_account_access_management', :'migration_sha256');
