from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth_routes import COOKIE_NAME, _csrf
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin
from scripts.publication_commit_checks import literal


def verify(database, token, location, role):
    require(admin("""
        SELECT p.prosecdef
          AND pg_get_userbyid(p.proowner)='shift_scheduling_owner'
          AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
          AND NOT has_function_privilege(
              'shift_scheduling_app',p.oid,'EXECUTE')
          AND NOT EXISTS (
              SELECT FROM aclexplode(
                  coalesce(p.proacl,acldefault('f',p.proowner))
              ) acl WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
          )
        FROM pg_proc p
        WHERE p.oid='scheduling.queue_assignment_review()'::regprocedure;
    """, database) == "t", "Unexpected review trigger security.")
    require(admin("""
        SELECT NOT has_table_privilege(
            'shift_scheduling_app','scheduling.assignment_review_event','SELECT')
          AND NOT has_table_privilege(
            'shift_scheduling_app','scheduling.assignment_review_event','INSERT')
          AND NOT has_table_privilege(
            'shift_scheduling_app','scheduling.assignment_review_event','UPDATE')
          AND NOT has_table_privilege(
            'shift_scheduling_app','scheduling.assignment_review_event','DELETE');
    """, database) == "t", "Private review table access was granted.")
    print("REVIEW_TRIGGER_OWNER_EXECUTION_AND_PRIVATE_ACCESS=PASS")
    employee = admin(
        "SELECT id FROM scheduling.employee WHERE location_id=" +
        literal(location), database,
    )
    second = str(uuid4())
    admin(f"""
        INSERT INTO scheduling.job_role(id,organization_id,location_id,name)
        SELECT {literal(second)},organization_id,id,'Second qualification'
        FROM scheduling.location WHERE id={literal(location)};
    """, database)
    path = (
        f"/api/v1/staff/locations/{location}/employees/"
        f"{employee}/qualifications"
    )
    body = {"roles": [
        {"role_id": role, "expected_revision": 0, "active": True},
        {"role_id": second, "expected_revision": 0, "active": True},
    ]}
    headers = {"X-CSRF-Token": _csrf(token)}

    def state():
        return admin("""
            SELECT json_build_array(
                (SELECT jsonb_agg(to_jsonb(q) ORDER BY employee_id,job_role_id)
                 FROM scheduling.employee_qualification q),
                (SELECT count(*) FROM scheduling.audit_event),
                (SELECT count(*) FROM scheduling.assignment_review_event)
            );
        """, database)

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        require(client.put(path, json=body).status_code == 403,
                "Qualification selection accepted missing CSRF.")
        invalid = {"roles": [
            body["roles"][0],
            {**body["roles"][1], "expected_revision": 1},
        ]}
        before = state()
        require(client.put(path, headers=headers, json=invalid).status_code == 409,
                "Stale qualification selection was accepted.")
        require(state() == before, "Stale selection retained partial writes.")
        saved = client.put(path, headers=headers, json=body)
        require(saved.status_code == 200 and saved.json()["changed"] == 2,
                "Whole qualification selection did not save.")
        before = state()
        require(client.put(path, headers=headers, json=body).status_code == 409,
                "Repeated stale selection was accepted.")
        require(state() == before, "Stale repeat wrote records.")

        replacement = {"roles": [
            {"role_id": role, "expected_revision": 1, "active": False},
            {"role_id": second, "expected_revision": 1, "active": False},
        ]}
        admin("""
            CREATE FUNCTION scheduling.reject_qualification_batch_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='qualification.updated' THEN
                    RAISE EXCEPTION 'Synthetic qualification audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_qualification_batch_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION
                scheduling.reject_qualification_batch_test();
        """, database)
        failed = client.put(
            path, headers=headers, json=replacement
        )
        require(failed.status_code == 503,
                "Qualification audit failure was not exercised.")
        require(state() == before, "Failed qualification save retained changes.")
        admin("""
            DROP TRIGGER reject_qualification_batch_test
            ON scheduling.audit_event;
            DROP FUNCTION scheduling.reject_qualification_batch_test();
        """, database)
        saved = client.put(path, headers=headers, json=replacement)
        require(saved.status_code == 200 and saved.json()["changed"] == 2,
                "Unchecking qualifications failed.")

    print("WHOLE_QUALIFICATION_SELECTION_AND_UNCHECK=PASS")
    print("STALE_SELECTION_AND_CSRF_GUARDS=PASS")
    print("QUALIFICATION_AND_AUDIT_ROLLBACK=PASS")
