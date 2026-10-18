-- Prepared migration: caller supplies transaction, owner and checksum.
DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.create_employee_account(
    target_location uuid,
    target_employee uuid,
    expected_revision integer,
    account_email text,
    account_password_hash text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    employee_row scheduling.employee%ROWTYPE;
    actor_id uuid;
    account_id uuid;
    normalized_email text;
    result jsonb;
BEGIN
    -- Resolve the target first, but reveal nothing until authorized.
    SELECT * INTO employee_row
    FROM scheduling.employee
    WHERE id = target_employee AND location_id = target_location
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Account operation unavailable'
            USING ERRCODE = '42501';
    END IF;

    SELECT u.id INTO actor_id
    FROM scheduling.auth_session s
    JOIN scheduling.app_user u ON u.id = s.user_id
    JOIN scheduling.organization_member m ON m.user_id = u.id
    JOIN scheduling.member_permission p
      ON p.organization_id = m.organization_id
     AND p.user_id = m.user_id
    WHERE s.token_sha256 =
          current_setting('scheduling.session_sha256', true)
      AND s.revoked_at IS NULL AND s.expires_at > now()
      AND u.active AND m.active
      AND m.organization_id = employee_row.organization_id
      AND p.permission = 'administrator';

    IF actor_id IS NULL THEN
        RAISE EXCEPTION 'Account operation unavailable'
            USING ERRCODE = '42501';
    END IF;

    IF expected_revision IS NULL
       OR employee_row.revision <> expected_revision
       OR employee_row.user_id IS NOT NULL
       OR NOT employee_row.active THEN
        RAISE EXCEPTION 'Employee changed or cannot receive an account'
            USING ERRCODE = '40001';
    END IF;

    normalized_email := lower(btrim(account_email));
    IF normalized_email IS NULL
       OR length(normalized_email) > 254
       OR normalized_email !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'
       OR account_password_hash IS NULL
       OR account_password_hash NOT LIKE '$argon2id$%'
       OR length(account_password_hash) > 1024 THEN
        RAISE EXCEPTION 'Invalid account input'
            USING ERRCODE = '22023';
    END IF;

    -- Existing global accounts are never reused or altered by this operation.
    INSERT INTO scheduling.app_user(email, password_hash)
    VALUES (normalized_email, account_password_hash)
    ON CONFLICT (email) DO NOTHING
    RETURNING id INTO account_id;

    IF account_id IS NULL THEN
        RAISE EXCEPTION 'Account could not be created with these details'
            USING ERRCODE = '23505';
    END IF;

    INSERT INTO scheduling.organization_member(organization_id, user_id)
    VALUES (employee_row.organization_id, account_id);

    -- This operation can grant only employee access.
    INSERT INTO scheduling.member_permission(
        organization_id, user_id, permission
    )
    VALUES (employee_row.organization_id, account_id, 'employee');

    UPDATE scheduling.employee
    SET user_id = account_id, revision = revision + 1
    WHERE id = target_employee;

    result := jsonb_build_object(
        'employee_id', target_employee,
        'user_id', account_id,
        'email', normalized_email,
        'revision', employee_row.revision + 1
    );

    INSERT INTO scheduling.audit_event(
        organization_id, location_id, actor_user_id,
        action, entity_type, entity_id, before_data, after_data
    )
    VALUES (
        employee_row.organization_id, target_location, actor_id,
        'employee.account_created', 'employee', target_employee,
        jsonb_build_object(
            'user_id', employee_row.user_id,
            'revision', employee_row.revision
        ),
        result
    );

    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION scheduling.create_employee_account(
    uuid, uuid, integer, text, text
) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION scheduling.create_employee_account(
    uuid, uuid, integer, text, text
) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version, sha256)
VALUES ('0006_employee_account_creation', :'migration_sha256');
