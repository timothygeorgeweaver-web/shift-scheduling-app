"""Real staff-edit verification, called only inside the disposable DB harness."""

from concurrent.futures import ThreadPoolExecutor
import re
from threading import Barrier

import psycopg
from psycopg.conninfo import conninfo_to_dict
from fastapi.testclient import TestClient

from app.auth_routes import COOKIE_NAME
from app.database import database_url
from app.main import app
from scripts.create_recovery_checkpoint import admin


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def verify(test_db, test_dsn, identities, locations, employees, roles):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db
        and conninfo_to_dict(test_dsn).get("dbname") == test_db,
        "Write checks must target the disposable database.",
    )
    base = "/api/v1/staff/locations/" + locations["a"]
    token = identities["admin_a"]["token"]

    with TestClient(app) as anonymous:
        require(
            anonymous.post(base + "/employees", json={"name": "Denied"}).status_code == 401,
            "Anonymous staff creation was accepted.",
        )

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        identity = client.get("/api/v1/auth/me")
        require(identity.status_code == 200, "Fixture identity lookup failed.")
        headers = {"X-CSRF-Token": identity.json()["csrf_token"]}

        require(
            client.post(base + "/employees", json={"name": "Denied"}).status_code == 403,
            "Staff creation without CSRF was accepted.",
        )
        require(
            client.post(base + "/employees", json={"name": "   "}, headers=headers).status_code == 422,
            "Blank staff name was accepted.",
        )

        created = client.post(
            base + "/employees", json={"name": "New API employee"}, headers=headers
        )
        require(created.status_code == 201, "Employee creation failed.")
        employee = created.json()
        require(employee["revision"] == 1, "Unexpected initial employee revision.")

        created_role = client.post(
            base + "/roles", json={"name": "New API role"}, headers=headers
        )
        require(created_role.status_code == 201, "Role creation failed.")
        role = created_role.json()
        qualification_url = (
            base + "/employees/" + employee["id"]
            + "/qualifications/" + role["id"]
        )

        qualified = client.put(
            qualification_url,
            json={"expected_revision": 0, "active": True}, headers=headers,
        )
        require(
            qualified.status_code == 200 and qualified.json()["revision"] == 1,
            "Qualification creation failed.",
        )
        require(
            client.put(
                qualification_url,
                json={"expected_revision": 0, "active": True}, headers=headers,
            ).status_code == 409,
            "Stale qualification creation was accepted.",
        )
        changed = client.put(
            qualification_url,
            json={"expected_revision": 1, "active": False}, headers=headers,
        )
        require(
            changed.status_code == 200 and changed.json()["revision"] == 2,
            "Qualification update failed.",
        )

        employee_url = base + "/employees/" + employee["id"]
        changed = client.patch(
            employee_url,
            json={"expected_revision": 1, "name": "Updated API employee", "active": False},
            headers=headers,
        )
        require(
            changed.status_code == 200
            and changed.json()["revision"] == 2
            and changed.json()["active"] is False,
            "Employee edit/deactivation failed.",
        )
        require(
            client.patch(
                employee_url,
                json={"expected_revision": 1, "name": "Stale overwrite"},
                headers=headers,
            ).status_code == 409,
            "Stale employee overwrite was accepted.",
        )
        reactivated = client.patch(
            employee_url,
            json={"expected_revision": 2, "active": True}, headers=headers,
        )
        require(
            reactivated.status_code == 200
            and reactivated.json()["revision"] == 3,
            "Employee reactivation failed.",
        )

        role_url = base + "/roles/" + role["id"]
        changed_role = client.patch(
            role_url,
            json={"expected_revision": 1, "name": "Updated API role", "active": False},
            headers=headers,
        )
        require(
            changed_role.status_code == 200
            and changed_role.json()["revision"] == 2,
            "Role edit failed.",
        )
        require(
            client.patch(
                role_url,
                json={"expected_revision": 1, "name": "Stale role"},
                headers=headers,
            ).status_code == 409,
            "Stale role overwrite was accepted.",
        )

        require(
            client.post(
                "/api/v1/staff/locations/" + locations["b"] + "/employees",
                json={"name": "Cross-business write"}, headers=headers,
            ).status_code == 404,
            "Cross-business creation was accepted.",
        )
        require(
            client.patch(
                base + "/employees/" + employees["b"],
                json={"expected_revision": 1, "name": "Cross-business edit"},
                headers=headers,
            ).status_code == 404,
            "Cross-business employee edit was accepted.",
        )
        require(
            client.put(
                base + "/employees/" + employee["id"] + "/qualifications/" + roles["b"],
                json={"expected_revision": 0, "active": True}, headers=headers,
            ).status_code == 404,
            "Cross-business qualification was accepted.",
        )

        # Two real DB requests race using the same expected revision.
        barrier = Barrier(2)

        def race(name):
            with TestClient(app) as competing:
                competing.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return competing.patch(
                    employee_url,
                    json={"expected_revision": 3, "name": name},
                    headers=headers,
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(race, "Concurrent edit A")
            second = pool.submit(race, "Concurrent edit B")
            outcomes = sorted([first.result(timeout=20), second.result(timeout=20)])
        require(outcomes == [200, 409], "Concurrent stale-edit protection failed.")

        # Force an audit failure only in the disposable database.
        admin("""
            BEGIN;
            SET LOCAL ROLE shift_scheduling_owner;
            CREATE FUNCTION scheduling.reject_audit_probe()
            RETURNS trigger LANGUAGE plpgsql AS $test$
            BEGIN
                IF NEW.after_data->>'display_name' = 'Audit rollback probe' THEN
                    RAISE EXCEPTION 'Synthetic audit failure';
                END IF;
                RETURN NEW;
            END
            $test$;
            CREATE TRIGGER reject_audit_probe
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_audit_probe();
            COMMIT;
        """, test_db)

        rejected = client.post(
            base + "/employees",
            json={"name": "Audit rollback probe"}, headers=headers,
        )
        require(rejected.status_code == 503, "Synthetic audit failure was not reported.")
        people = client.get(base + "/employees")
        require(
            people.status_code == 200
            and all(row["display_name"] != "Audit rollback probe" for row in people.json()),
            "Staff insert survived its failed audit write.",
        )

        events = client.get(base + "/history?limit=100")
        require(
            events.status_code == 200 and len(events.json()) == 8,
            "Unexpected audit count; denied or failed writes may have left events.",
        )
        require(
            all(event["actor_user_id"] == identities["admin_a"]["user_id"]
                for event in events.json()),
            "Audit actor mismatch.",
        )
        require(
            token not in events.text and "password_hash" not in events.text,
            "Private authentication data leaked into history.",
        )
        require(
            len(client.get(base + "/history?limit=2").json()) == 2,
            "History limit failed.",
        )

    with TestClient(app) as employee_client:
        employee_client.cookies.set(COOKIE_NAME, identities["employee_a"]["token"])
        me = employee_client.get("/api/v1/auth/me")
        denied = employee_client.post(
            base + "/employees", json={"name": "Denied employee write"},
            headers={"X-CSRF-Token": me.json()["csrf_token"]},
        )
        require(denied.status_code == 403, "Employee-only staff mutation accepted.")

    with TestClient(app) as scheduler:
        scheduler.cookies.set(COOKIE_NAME, identities["scheduler_a"]["token"])
        me = scheduler.get("/api/v1/auth/me")
        created = scheduler.post(
            base + "/employees", json={"name": "Scheduler-created employee"},
            headers={"X-CSRF-Token": me.json()["csrf_token"]},
        )
        require(created.status_code == 201, "Authorized scheduler creation failed.")

    # Audit events cannot be changed even through the runtime DB credentials.
    try:
        with psycopg.connect(test_dsn) as conn:
            conn.execute(
                "SELECT set_config('scheduling.session_sha256', %s, true)",
                (identities["admin_a"]["digest"],),
            )
            conn.execute("UPDATE scheduling.audit_event SET action = 'tampered'")
    except psycopg.errors.InsufficientPrivilege:
        pass
    else:
        raise SystemExit("STOP: runtime database role modified audit history.")

    print("REAL_STAFF_CREATE_EDIT_AND_QUALIFICATIONS=PASS")
    print("CSRF_AND_BUSINESS_WRITE_ISOLATION=PASS")
    print("STALE_AND_CONCURRENT_EDITS=PASS")
    print("ATOMIC_STAFF_AND_AUDIT_ROLLBACK=PASS")
    print("AUDIT_ACTORS_HISTORY_AND_IMMUTABILITY=PASS")
