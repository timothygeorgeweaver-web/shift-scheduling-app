"""Atomic whole-list role ordering in disposable PostgreSQL."""

from fastapi.testclient import TestClient

from app.auth_routes import COOKIE_NAME, _csrf
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, locations):
    require(test_db.startswith("shift_sched_test_"),
            "Reorder checks require a disposable database.")
    token = identities["scheduler_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    base = "/api/v1/staff/locations/"+locations["a"]+"/roles"
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)

        def read():
            response = client.get(base)
            require(response.status_code == 200, "Reorder fixture read failed.")
            return response.json()

        def payload(rows):
            return {"roles": [
                {"id": row["id"], "expected_revision": row["revision"]}
                for row in rows
            ]}

        original = read()
        body = payload(list(reversed(original)))
        require(client.post(base+"/order", json=body).status_code == 403,
                "Reorder accepted missing CSRF.")
        require(client.post(base+"/order", headers=headers,
                            json={"roles": body["roles"][:1]*2}).status_code == 422,
                "Duplicate job types accepted.")
        response = client.post(base+"/order", headers=headers, json=body)
        require(response.status_code == 200, "Whole-list reorder failed.")
        require([r["id"] for r in read()] ==
                [r["id"] for r in reversed(original)],
                "Saved role arrangement was not preserved.")
        require(client.post(base+"/order", headers=headers, json=body)
                .status_code == 409, "Stale whole-list reorder accepted.")

        rows = read()
        admin("""
            CREATE FUNCTION scheduling.reject_role_reorder_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='job_role.reordered' THEN
                    RAISE EXCEPTION 'Synthetic reorder audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_role_reorder_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_role_reorder_test();
        """, test_db)
        before = fingerprint(test_db)
        require(client.post(base+"/order", headers=headers,
                            json=payload(list(reversed(rows)))).status_code == 503,
                "Reorder audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Failed reorder partially changed the arrangement.")
        admin(
            "DROP TRIGGER reject_role_reorder_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_role_reorder_test();", test_db,
        )
    print("WHOLE_JOB_TYPE_ORDER_AND_STALE_LIST_PROTECTION=PASS")
    print("JOB_TYPE_REORDER_FULL_AUDIT_ROLLBACK=PASS")
