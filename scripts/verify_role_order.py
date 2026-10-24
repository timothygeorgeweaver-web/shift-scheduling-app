"""Role-order certification in the disposable database only."""

from concurrent.futures import ThreadPoolExecutor
import re
from threading import Barrier

from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, locations):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Role-order checks require the disposable database.",
    )
    token = identities["scheduler_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    base = "/api/v1/staff/locations/"+locations["a"]+"/roles"
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)

        def create(name, order):
            response = client.post(base, headers=headers,
                                   json={"name": name, "display_order": order})
            require(response.status_code == 201, "Ordered role creation failed.")
            return response.json()

        first = create("ZZ ordering role", 10)
        second = create("AA ordering role", 20)
        ids = {first["id"], second["id"]}
        url = base+"/"+second["id"]

        def listing():
            response = client.get(base)
            require(response.status_code == 200, "Role-order read failed.")
            return response.json()

        def ordering():
            return [r["id"] for r in listing() if r["id"] in ids]

        require(ordering() == [first["id"], second["id"]],
                "Names took precedence over display order.")
        body = {"expected_revision": second["revision"], "display_order": 5}
        require(client.patch(url, json=body).status_code == 403,
                "Order edit accepted missing CSRF.")
        for value in (-1, 10000, True, "5", 1.5):
            require(client.patch(url, headers=headers,
                    json={**body, "display_order": value}).status_code == 422,
                    "Invalid display order accepted.")
        response = client.patch(url, headers=headers, json=body)
        require(response.status_code == 200
                and response.json()["display_order"] == 5,
                "Order-only edit failed.")
        require(ordering() == [second["id"], first["id"]],
                "Edited role order did not persist.")
        require(client.patch(url, headers=headers, json=body).status_code == 409,
                "Stale order edit accepted.")

        revision = response.json()["revision"]
        barrier = Barrier(2)

        def race(order):
            with TestClient(app) as other:
                other.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return other.patch(url, headers=headers, json={
                    "expected_revision": revision, "display_order": order,
                }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(race, (6, 7)))
        require(sorted(outcomes) == [200, 409],
                "Concurrent order edits bypassed revisions.")

        current = next(r for r in listing() if r["id"] == second["id"])
        admin("""
            CREATE FUNCTION scheduling.reject_role_order_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.entity_type='job_role'
                   AND NEW.after_data->>'display_order'='9999' THEN
                    RAISE EXCEPTION 'Synthetic role-order audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_role_order_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_role_order_test();
        """, test_db)
        before = fingerprint(test_db)
        require(client.patch(url, headers=headers, json={
            "expected_revision": current["revision"], "display_order": 9999,
        }).status_code == 503, "Order audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Order edit survived an audit failure.")
        admin(
            "DROP TRIGGER reject_role_order_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_role_order_test();", test_db,
        )

        for label, expected in (("employee_a", 403), ("admin_b", 404)):
            with TestClient(app) as denied:
                other = identities[label]["token"]
                denied.cookies.set(COOKIE_NAME, other)
                require(denied.patch(
                    url, headers={"X-CSRF-Token": _csrf(other)}, json={
                        "expected_revision": current["revision"],
                        "display_order": 4,
                    }).status_code == expected, "Order isolation failed.")

        events = client.get(base.rsplit("/", 1)[0]+"/history?limit=100").json()
        require(any(
            event["entity_id"] == second["id"]
            and event["before_data"] is not None
            and event["before_data"].get("display_order") == 20
            and event["after_data"].get("display_order") == 5
            for event in events
        ), "Order audit omitted before/after values.")

    print("JOB_ROLE_CREATE_EDIT_AND_DISPLAY_ORDER=PASS")
    print("ORDER_VALIDATION_AND_CONCURRENT_REVISIONS=PASS")
    print("JOB_ORDER_AUTHORIZATION_AND_BUSINESS_ISOLATION=PASS")
    print("JOB_ORDER_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
