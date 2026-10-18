-- Caller has an open transaction as owner. All fixtures are rolled back.
SAVEPOINT staff_write_fixtures;

DO $$
DECLARE
    org_a uuid;
    org_b uuid;
    loc_a uuid;
    loc_b uuid;
    user_a uuid;
    user_c uuid;
BEGIN
    INSERT INTO scheduling.organization(name)
        VALUES ('Write fixture A') RETURNING id INTO org_a;
    INSERT INTO scheduling.organization(name)
        VALUES ('Write fixture B') RETURNING id INTO org_b;
    INSERT INTO scheduling.location(organization_id,name,timezone)
        VALUES (org_a,'A','America/Los_Angeles') RETURNING id INTO loc_a;
    INSERT INTO scheduling.location(organization_id,name,timezone)
        VALUES (org_b,'B','America/Los_Angeles') RETURNING id INTO loc_b;

    user_a := gen_random_uuid();
    user_c := gen_random_uuid();

    INSERT INTO scheduling.app_user(id,email) VALUES
        (user_a,user_a::text || '@example.invalid'),
        (user_c,user_c::text || '@example.invalid');
    INSERT INTO scheduling.organization_member(organization_id,user_id)
        VALUES (org_a,user_a),(org_a,user_c);
    INSERT INTO scheduling.member_permission(
        organization_id,user_id,permission
    ) VALUES
        (org_a,user_a,'administrator'),
        (org_a,user_c,'employee');
    INSERT INTO scheduling.auth_session(token_sha256,user_id,expires_at)
        VALUES
        (repeat('a',64),user_a,now() + interval '8 hours'),
        (repeat('c',64),user_c,now() + interval '8 hours');

    PERFORM set_config('scheduling.test_org_a',org_a::text,true);
    PERFORM set_config('scheduling.test_org_b',org_b::text,true);
    PERFORM set_config('scheduling.test_loc_a',loc_a::text,true);
    PERFORM set_config('scheduling.test_loc_b',loc_b::text,true);
    PERFORM set_config('scheduling.test_user_a',user_a::text,true);
    PERFORM set_config('scheduling.test_user_c',user_c::text,true);
    PERFORM set_config('scheduling.session_sha256',repeat('a',64),true);
END
$$;

SET LOCAL ROLE shift_scheduling_app;

DO $$
DECLARE
    org_a uuid := current_setting('scheduling.test_org_a')::uuid;
    org_b uuid := current_setting('scheduling.test_org_b')::uuid;
    loc_a uuid := current_setting('scheduling.test_loc_a')::uuid;
    loc_b uuid := current_setting('scheduling.test_loc_b')::uuid;
    user_a uuid := current_setting('scheduling.test_user_a')::uuid;
    user_c uuid := current_setting('scheduling.test_user_c')::uuid;
    employee_a uuid;
    role_a uuid;
    event_a uuid;
    actual_revision integer;
BEGIN
    INSERT INTO scheduling.employee(
        organization_id,location_id,display_name
    ) VALUES (org_a,loc_a,'Write verification employee')
      RETURNING id INTO employee_a;

    INSERT INTO scheduling.job_role(organization_id,location_id,name)
        VALUES (org_a,loc_a,'Write verification role')
        RETURNING id INTO role_a;

    INSERT INTO scheduling.employee_qualification(
        organization_id,location_id,employee_id,job_role_id,active
    ) VALUES (org_a,loc_a,employee_a,role_a,true);

    UPDATE scheduling.employee
        SET display_name = 'Updated verification employee',
            active = false, revision = revision + 1
        WHERE id = employee_a RETURNING revision INTO actual_revision;
    IF actual_revision <> 2 THEN
        RAISE EXCEPTION 'Employee revision update failed';
    END IF;

    UPDATE scheduling.job_role SET active = false, revision = revision + 1
        WHERE id = role_a;
    UPDATE scheduling.employee_qualification
        SET active = false, revision = revision + 1
        WHERE employee_id = employee_a AND job_role_id = role_a;

    BEGIN
        UPDATE scheduling.employee SET revision = 0 WHERE id = employee_a;
        RAISE EXCEPTION 'Invalid revision was accepted';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        org_a,loc_a,user_a,'employee.updated','employee',employee_a,
        '{"active":true}'::jsonb,'{"active":false}'::jsonb
    ) RETURNING id INTO event_a;

    BEGIN
        UPDATE scheduling.audit_event SET action = 'forged' WHERE id = event_a;
        RAISE EXCEPTION 'Audit update was permitted';
    EXCEPTION WHEN insufficient_privilege THEN
        NULL;
    END;

    BEGIN
        DELETE FROM scheduling.audit_event WHERE id = event_a;
        RAISE EXCEPTION 'Audit deletion was permitted';
    EXCEPTION WHEN insufficient_privilege THEN
        NULL;
    END;

    BEGIN
        INSERT INTO scheduling.audit_event(
            organization_id,location_id,actor_user_id,action,
            entity_type,entity_id,after_data
        ) VALUES (
            org_a,loc_a,user_c,'forged.actor','employee',employee_a,'{}'
        );
        RAISE EXCEPTION 'Forged audit actor was accepted';
    EXCEPTION WHEN insufficient_privilege THEN
        NULL;
    END;

    BEGIN
        INSERT INTO scheduling.employee(
            organization_id,location_id,display_name
        ) VALUES (org_b,loc_b,'Invalid cross-business employee');
        RAISE EXCEPTION 'Cross-business employee creation was permitted';
    EXCEPTION WHEN insufficient_privilege THEN
        NULL;
    END;

    BEGIN
        UPDATE scheduling.employee
        SET organization_id = org_b WHERE id = employee_a;
        RAISE EXCEPTION 'Employee ownership change was permitted';
    EXCEPTION WHEN insufficient_privilege THEN
        NULL;
    END;

    PERFORM set_config('scheduling.session_sha256',repeat('c',64),true);
    BEGIN
        INSERT INTO scheduling.employee(
            organization_id,location_id,display_name
        ) VALUES (org_a,loc_a,'Invalid employee-only write');
        RAISE EXCEPTION 'Employee-only staff creation was permitted';
    EXCEPTION WHEN insufficient_privilege THEN
        NULL;
    END;

    PERFORM set_config('scheduling.session_sha256','',true);
    IF EXISTS (SELECT FROM scheduling.employee) THEN
        RAISE EXCEPTION 'Staff records visible without session context';
    END IF;
END
$$;

SET LOCAL ROLE shift_scheduling_owner;
ROLLBACK TO SAVEPOINT staff_write_fixtures;
RELEASE SAVEPOINT staff_write_fixtures;
