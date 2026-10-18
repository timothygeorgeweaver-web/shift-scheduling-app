"""Settings authorization, concurrency and rollback in the disposable copy."""

from concurrent.futures import ThreadPoolExecutor
import re
from threading import Barrier

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Settings tests must use the disposable database.",
    )
    install("0008_location_settings", test_db)
    url = "/api/v1/staff/locations/" + location + "/settings"
    token = identities["admin_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}

    with TestClient(app) as anonymous:
        require(anonymous.get(url).status_code == 401, "Anonymous settings access.")

    for label, status in (
        ("scheduler_a", 403), ("employee_a", 403), ("admin_b", 404)
    ):
        with TestClient(app) as denied:
            denied.cookies.set(COOKIE_NAME, identities[label]["token"])
            require(denied.get(url).status_code == status,
                    "Settings authorization boundary failed.")

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        response = client.get(url)
        require(response.status_code == 200, "Settings read failed.")
        state = response.json()

        def body(current):
            return {
                "expected_organization_revision": current["organization_revision"],
                "expected_location_revision": current["location_revision"],
                "business_name": "Updated fixture business",
                "location_name": "Updated fixture location",
                "timezone": "America/Los_Angeles",
            }

        payload = body(state)
        require(client.patch(url, json=payload).status_code == 403,
                "Settings mutation accepted missing CSRF.")
        for change in (
            {"business_name": "   "}, {"timezone": "Invalid/Timezone"},
            {"expected_location_revision": True}, {"organization_id": location},
        ):
            require(client.patch(
                url, headers=headers, json={**payload, **change}
            ).status_code == 422, "Invalid settings input accepted.")

        for label, status in (
            ("scheduler_a", 403), ("employee_a", 403), ("admin_b", 404)
        ):
            with TestClient(app) as denied:
                other = identities[label]["token"]
                denied.cookies.set(COOKIE_NAME, other)
                require(denied.patch(url, json=payload,
                    headers={"X-CSRF-Token": _csrf(other)}
                ).status_code == status, "Unauthorized settings mutation accepted.")

        saved = client.patch(url, headers=headers, json=payload)
        require(saved.status_code == 200, "Settings save failed.")
        require(saved.json()["business_name"] == payload["business_name"]
                and saved.json()["location_revision"] == state["location_revision"]+1,
                "Saved settings differ.")
        require(client.patch(url, headers=headers, json=payload).status_code == 409,
                "Stale settings save accepted.")
        state = client.get(url).json()

    # Verify the database function itself denies a scheduler context.
    try:
        with psycopg.connect(database_url()) as conn:
            conn.execute(
                "SELECT set_config('scheduling.session_sha256',%s,true)",
                (identities["scheduler_a"]["digest"],),
            )
            conn.execute(
                "SELECT scheduling.edit_location_settings(%s,%s,%s,%s,%s,%s)",
                (location, state["organization_revision"], state["location_revision"],
                 "Denied", "Denied", "UTC"),
            )
    except psycopg.Error as error:
        require(error.sqlstate == "42501", "Unexpected direct settings denial.")
    else:
        raise SystemExit("STOP: Direct unauthorized settings update accepted.")

    barrier = Barrier(2)
    payload = body(state)

    def concurrent(name):
        with TestClient(app) as client:
            client.cookies.set(COOKIE_NAME, token)
            barrier.wait(timeout=10)
            return client.patch(url, headers=headers,
                json={**payload, "location_name": name}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(concurrent, ("Concurrent one", "Concurrent two")))
    require(sorted(outcomes) == [200, 409], "Concurrent settings saves overwrote changes.")

    admin("""
        CREATE FUNCTION scheduling.reject_settings_audit_test()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.action LIKE 'settings.%' THEN
            RAISE EXCEPTION 'Synthetic settings audit failure';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER reject_settings_audit_test
        BEFORE INSERT ON scheduling.audit_event
        FOR EACH ROW EXECUTE FUNCTION scheduling.reject_settings_audit_test();
    """, test_db)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        state = client.get(url).json()
        before = fingerprint(test_db)
        require(client.patch(
            url, headers=headers, json=body(state)
        ).status_code == 503, "Settings audit failure was not exercised.")
        require(fingerprint(test_db) == before,
                "Failed settings audit retained changes.")
    admin(
        "DROP TRIGGER reject_settings_audit_test ON scheduling.audit_event;"
        "DROP FUNCTION scheduling.reject_settings_audit_test();", test_db,
    )
    print("BUSINESS_LOCATION_AND_TIMEZONE_SETTINGS=PASS")
    print("SETTINGS_AUTHORIZATION_CSRF_AND_VALIDATION=PASS")
    print("SETTINGS_STALE_AND_CONCURRENT_EDITS=PASS")
    print("SETTINGS_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
