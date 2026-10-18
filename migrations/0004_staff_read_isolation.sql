-- Caller supplies transaction, owner role and migration_sha256.

DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.staff_read_allowed(target_organization uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER
SET search_path = pg_catalog
AS $function$
    SELECT EXISTS (
        SELECT 1
        FROM scheduling.auth_session s
        JOIN scheduling.app_user u ON u.id = s.user_id
        JOIN scheduling.organization_member m ON m.user_id = u.id
        JOIN scheduling.member_permission p
          ON p.organization_id = m.organization_id
         AND p.user_id = m.user_id
        WHERE s.token_sha256 =
              current_setting('scheduling.session_sha256', true)
          AND s.revoked_at IS NULL
          AND s.expires_at > now()
          AND u.active AND m.active
          AND m.organization_id = target_organization
          AND p.permission IN ('administrator', 'scheduler')
    );
$function$;

REVOKE ALL ON FUNCTION scheduling.staff_read_allowed(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.staff_read_allowed(uuid)
    TO shift_scheduling_app;

DO $$
DECLARE
    target record;
BEGIN
    FOR target IN
        SELECT * FROM (VALUES
            ('organization', 'id'),
            ('location', 'organization_id'),
            ('employee', 'organization_id'),
            ('job_role', 'organization_id'),
            ('employee_qualification', 'organization_id')
        ) AS targets(table_name, organization_column)
    LOOP
        EXECUTE format(
            'ALTER TABLE scheduling.%I ENABLE ROW LEVEL SECURITY',
            target.table_name
        );
        EXECUTE format(
            'CREATE POLICY staff_read ON scheduling.%I
             FOR SELECT TO shift_scheduling_app
             USING (scheduling.staff_read_allowed(%I))',
            target.table_name, target.organization_column
        );
    END LOOP;
END
$$;

GRANT SELECT ON scheduling.organization, scheduling.location,
    scheduling.employee, scheduling.job_role,
    scheduling.employee_qualification TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version, sha256)
VALUES ('0004_staff_read_isolation', :'migration_sha256');
