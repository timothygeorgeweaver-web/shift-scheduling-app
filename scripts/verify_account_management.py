"""Account suspension, permissions and last-administrator integration checks."""

from concurrent.futures import ThreadPoolExecutor
import re
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.auth_store import create_session, identity
from app.database import database_url
from scripts.account_access_migration import install
from scripts.create_recovery_checkpoint import admin, fingerprint


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def verify(test_db, identities, location, employee, email, password):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Account access tests must use the disposable database.",
    )
    install(test_db)
    from scripts.verify_location_settings import verify as verify_settings
    verify_settings(test_db, identities, location)
    prefix = "/api/v1/staff/locations/" + location + "/employees/"
    url = prefix + employee + "/account"
    admin_token = identities["admin_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(admin_token)}

    for label, status in (
        ("scheduler_a", 403), ("employee_a", 403), ("admin_b", 404)
    ):
        with TestClient(app) as denied:
            token = identities[label]["token"]
            denied.cookies.set(COOKIE_NAME, token)
            require(denied.get(url).status_code == status,
                    "Unauthorized account details were exposed.")
            require(denied.patch(url, headers={"X-CSRF-Token": _csrf(token)},
                json={"expected_revision": 1, "active": True,
                      "permissions": ["employee"]}
            ).status_code == status, "Unauthorized access mutation accepted.")

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, admin_token)
        response = client.get(url)
        require(response.status_code == 200, "Account access read failed.")
        state = response.json()
        require(
            state["email"] == email and state["permissions"] == ["employee"],
            "Account access details differ.",
        )

        def update(active=True, permissions=None, revision=None):
            return client.patch(url, headers=headers, json={
                "expected_revision": state["revision"] if revision is None else revision,
                "active": active,
                "permissions": ["employee"] if permissions is None else permissions,
            })

        require(client.patch(url, json={
            "expected_revision": 1, "active": True, "permissions": ["employee"]
        }).status_code == 403, "Access mutation accepted missing CSRF.")
        for invalid in (
            {"active": "false"}, {"permissions": []},
            {"permissions": ["owner"]}, {"expected_revision": True},
        ):
            body = {"expected_revision": 1, "active": True,
                    "permissions": ["employee"], **invalid}
            require(client.patch(url, headers=headers, json=body).status_code == 422,
                    "Invalid account access input accepted.")
        require(update(revision=999).status_code == 409,
                "Stale access revision accepted.")

        token = create_session(email, password)
        require(token is not None, "Employee fixture sign-in failed.")
        granted = update(permissions=["employee", "scheduler"])
        require(granted.status_code == 200, "Scheduler permission grant failed.")
        state = granted.json()
        require(identity(token) is None, "Access change retained an old session.")
        token = create_session(email, password)
        require(token is not None and
                "scheduler" in identity(token)["memberships"][0]["permissions"],
                "Scheduler permission was not effective.")

        changed = update(active=False, permissions=["employee"])
        require(changed.status_code == 200, "Business suspension failed.")
        state = changed.json()
        require(identity(token) is None and create_session(email, password) is None,
                "Suspended business account retained access.")
        restored = update(active=True)
        require(restored.status_code == 200, "Business access reactivation failed.")
        state = restored.json()
        require(identity(token) is None, "Reactivation revived a revoked session.")
        require(create_session(email, password) is not None,
                "Reactivated account could not sign in.")

        # Force a failure after membership, permission and session mutations.
        admin("""
            CREATE FUNCTION scheduling.reject_access_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.action='employee.account_access_changed' THEN
                RAISE EXCEPTION 'Synthetic access audit failure';
              END IF;
              RETURN NEW;
            END $$;
            CREATE TRIGGER reject_access_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_access_audit_test();
        """, test_db)
        before = fingerprint(test_db)
        require(update(active=False).status_code == 503,
                "Access audit failure path was not exercised.")
        require(fingerprint(test_db) == before,
                "Failed access update left membership, permissions or sessions changed.")
        admin(
            "DROP TRIGGER reject_access_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_access_audit_test();", test_db,
        )

        # A synthetic employee link makes the fixture administrator addressable.
        own_employee = str(uuid4())
        literal = lambda value: sql.Literal(value).as_string()
        admin(
            "INSERT INTO scheduling.employee("
            "id,organization_id,location_id,user_id,display_name) "
            f"SELECT {literal(own_employee)},organization_id,id,"
            f"{literal(identities['admin_a']['user_id'])},'Admin access fixture' "
            f"FROM scheduling.location WHERE id={literal(location)};",
            test_db,
        )
        own_url = prefix + own_employee + "/account"
        own = client.get(own_url).json()
        for active, permissions in (
            (False, ["administrator"]), (True, ["employee"])
        ):
            require(client.patch(own_url, headers=headers, json={
                "expected_revision": own["revision"],
                "active": active, "permissions": permissions,
            }).status_code == 409, "Last administrator could be removed.")

        # Promote the employee to a second administrator.
        promoted = update(permissions=["administrator", "employee"])
        require(promoted.status_code == 200, "Second administrator grant failed.")
        auxiliary_token = create_session(email, password)
        require(auxiliary_token is not None, "Second administrator sign-in failed.")
        auxiliary_state = promoted.json()

    barrier = Barrier(2)

    def demote(arguments):
        path, token, revision = arguments
        with TestClient(app) as concurrent:
            concurrent.cookies.set(COOKIE_NAME, token)
            barrier.wait(timeout=10)
            return concurrent.patch(
                path, headers={"X-CSRF-Token": _csrf(token)},
                json={"expected_revision": revision, "active": True,
                      "permissions": ["employee"]},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(demote, (
            (own_url, admin_token, own["revision"]),
            (url, auxiliary_token, auxiliary_state["revision"]),
        )))
    require(sorted(outcomes) == [200, 409],
            "Concurrent demotions did not preserve the last administrator.")

    print("ACCOUNT_SUSPENSION_REACTIVATION_AND_PERMISSIONS=PASS")
    print("ACCESS_CHANGES_REVOKE_OLD_SESSIONS=PASS")
    print("ACCOUNT_ACCESS_CSRF_BUSINESS_AND_REVISION_CHECKS=PASS")
    print("ACCOUNT_ACCESS_AUDIT_FAILURE_ROLLBACK=PASS")
    print("LAST_ADMINISTRATOR_AND_CONCURRENT_DEMOTION_PROTECTION=PASS")


from app.main import app
