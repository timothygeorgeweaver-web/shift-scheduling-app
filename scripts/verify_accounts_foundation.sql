-- Run inside a transaction as the owner. All fixtures are rolled back.
SAVEPOINT account_fixtures;

DO $$
DECLARE
    org_a uuid;
    org_b uuid;
    loc_a uuid;
    loc_b uuid;
    user_a uuid;
    employee_a uuid;
    role_a uuid;
    role_b uuid;
BEGIN
    INSERT INTO scheduling.organization(name)
        VALUES ('Verification A') RETURNING id INTO org_a;
    INSERT INTO scheduling.organization(name)
        VALUES ('Verification B') RETURNING id INTO org_b;

    INSERT INTO scheduling.location(organization_id, name, timezone)
        VALUES (org_a, 'A', 'America/Los_Angeles')
        RETURNING id INTO loc_a;
    INSERT INTO scheduling.location(organization_id, name, timezone)
        VALUES (org_b, 'B', 'America/Los_Angeles')
        RETURNING id INTO loc_b;

    INSERT INTO scheduling.app_user(email)
        VALUES ('fixture@example.invalid') RETURNING id INTO user_a;

    INSERT INTO scheduling.organization_member(organization_id, user_id)
        VALUES (org_a, user_a);

    INSERT INTO scheduling.member_permission(
        organization_id, user_id, permission
    ) VALUES
        (org_a, user_a, 'administrator'),
        (org_a, user_a, 'scheduler'),
        (org_a, user_a, 'employee');

    INSERT INTO scheduling.employee(
        organization_id, location_id, user_id, display_name
    ) VALUES (org_a, loc_a, user_a, 'Verification Employee')
        RETURNING id INTO employee_a;

    INSERT INTO scheduling.job_role(organization_id, location_id, name)
        VALUES (org_a, loc_a, 'Cook') RETURNING id INTO role_a;
    INSERT INTO scheduling.job_role(organization_id, location_id, name)
        VALUES (org_b, loc_b, 'Cook') RETURNING id INTO role_b;

    INSERT INTO scheduling.employee_qualification(
        organization_id, location_id, employee_id, job_role_id
    ) VALUES (org_a, loc_a, employee_a, role_a);

    BEGIN
        INSERT INTO scheduling.employee_qualification(
            organization_id, location_id, employee_id, job_role_id
        ) VALUES (org_a, loc_a, employee_a, role_b);
        RAISE EXCEPTION 'Cross-business qualification incorrectly accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        NULL;
    END;

    BEGIN
        INSERT INTO scheduling.employee(
            organization_id, location_id, user_id, display_name
        ) VALUES (org_b, loc_b, user_a, 'Invalid Membership');
        RAISE EXCEPTION 'Missing business membership incorrectly accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        NULL;
    END;

    BEGIN
        INSERT INTO scheduling.member_permission(
            organization_id, user_id, permission
        ) VALUES (org_a, user_a, 'unknown_permission');
        RAISE EXCEPTION 'Unknown permission incorrectly accepted';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;

    BEGIN
        INSERT INTO scheduling.employee_qualification(
            organization_id, location_id, employee_id, job_role_id
        ) VALUES (org_a, loc_a, employee_a, role_a);
        RAISE EXCEPTION 'Duplicate qualification incorrectly accepted';
    EXCEPTION WHEN unique_violation THEN
        NULL;
    END;
END
$$;

ROLLBACK TO SAVEPOINT account_fixtures;
RELEASE SAVEPOINT account_fixtures;
