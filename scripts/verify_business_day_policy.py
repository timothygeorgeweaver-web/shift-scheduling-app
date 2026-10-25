"""Administrator rollover policy checks in the disposable database."""

from concurrent.futures import ThreadPoolExecutor
import re
from threading import Barrier

from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, locations):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Policy certification requires the disposable database.",
    )
    install("0023_business_day_policy", test_db, rehearse=True)
    url = "/api/v1/staff/locations/"+locations["a"]+"/business-day-policy"
    token = identities["admin_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    protected = fingerprint(test_db)["data"]

    with TestClient(app) as client:
        require(client.get(url).status_code == 401,
                "Anonymous policy read accepted.")
        client.cookies.set(COOKIE_NAME, token)
        response = client.get(url)
        require(response.status_code == 200, "Administrator policy read failed.")
        original = response.json()

        def body(minute, revision=None):
            return {
                "business_day_start_minute": minute,
                "expected_location_revision":
                    original["location_revision"] if revision is None else revision,
            }

        require(client.patch(url, json=body(360)).status_code == 403,
                "Policy write accepted missing CSRF.")
        require(client.patch(url, headers={
            **headers, "Origin": "https://other.example.invalid",
        }, json=body(360)).status_code == 403,
                "Policy write accepted foreign origin.")

        for value in (True, "300", -15, 301, 1440):
            require(client.patch(url, headers=headers, json=body(value))
                    .status_code == 422, "Invalid rollover accepted.")

        for label, expected in (
            ("scheduler_a", 403), ("employee_a", 403), ("admin_b", 404),
        ):
            other_token = identities[label]["token"]
            with TestClient(app) as other:
                other.cookies.set(COOKIE_NAME, other_token)
                require(other.get(url).status_code == expected,
                        "Policy read authorization failed.")
                require(other.patch(
                    url, headers={"X-CSRF-Token": _csrf(other_token)},
                    json=body(360),
                ).status_code == expected, "Policy write authorization failed.")

        updated = client.patch(url, headers=headers, json=body(360))
        require(updated.status_code == 200, "Policy update failed.")
        current = updated.json()
        require(current["business_day_start_minute"] == 360
                and current["location_revision"] == original["location_revision"]+1,
                "Policy value or revision did not persist.")
        require(client.patch(url, headers=headers, json=body(420))
                .status_code == 409, "Stale policy update accepted.")

        before = fingerprint(test_db)
        require(client.patch(url, headers=headers,
            json=body(360, current["location_revision"])).status_code == 200,
            "Unchanged policy request failed.")
        require(fingerprint(test_db) == before,
                "Unchanged policy request changed records or audit history.")

        barrier = Barrier(2)
        def race(minute):
            with TestClient(app) as other:
                other.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return other.patch(
                    url, headers=headers,
                    json=body(minute, current["location_revision"]),
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(race, (420, 480)))
        require(sorted(outcomes) == [200, 409],
                "Concurrent policy updates bypassed revisions.")
        current = client.get(url).json()

        admin("""
            CREATE FUNCTION scheduling.reject_rollover_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='settings.business_day_updated' THEN
                    RAISE EXCEPTION 'Synthetic rollover audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_rollover_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_rollover_audit_test();
        """, test_db)
        before = fingerprint(test_db)
        require(client.patch(url, headers=headers,
            json=body(540, current["location_revision"])).status_code == 503,
            "Policy audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Audit failure left a partial policy change.")
        admin(
            "DROP TRIGGER reject_rollover_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_rollover_audit_test();", test_db,
        )

        restored = client.patch(url, headers=headers, json=body(
            original["business_day_start_minute"], current["location_revision"],
        ))
        require(restored.status_code == 200, "Fixture policy restoration failed.")

    after = fingerprint(test_db)["data"]
    for table in protected:
        if table not in {"location", "audit_event"}:
            require(after[table] == protected[table],
                    "Policy update changed saved schedules or other records.")

    print("ADMINISTRATOR_ROLLOVER_READ_WRITE_AND_PERSISTENCE=PASS")
    print("ROLLOVER_CSRF_VALIDATION_AND_BUSINESS_ISOLATION=PASS")
    print("ROLLOVER_STALE_AND_CONCURRENT_UPDATE_PROTECTION=PASS")
    print("ROLLOVER_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
    print("SAVED_WEEKS_TEMPLATES_AND_UNAVAILABILITY=UNCHANGED")
