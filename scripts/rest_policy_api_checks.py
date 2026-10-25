"""Administrator rest-policy authorization, revisions and atomic audit checks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql

from app.auth_routes import COOKIE_NAME, _csrf
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin


def verify(database, token, location, role):
    url = f"/api/v1/staff/locations/{location}/rest-policy"
    headers = {"X-CSRF-Token": _csrf(token)}

    def audit_count():
        return admin("SELECT count(*) FROM scheduling.audit_event;", database)

    with TestClient(app) as client:
        require(client.get(url).status_code == 401, "Anonymous policy read accepted.")
        client.cookies.set(COOKIE_NAME, token)
        response = client.get(url)
        require(response.status_code == 200, "Rest policy read failed.")
        original = response.json()
        require(original["window_start"] == 1200
                and original["window_end"] == 360
                and original["rest_minutes"] == 720, "Incorrect rest defaults.")

        def body(minutes, revision=original["location_revision"]):
            return {
                "expected_location_revision": revision,
                "window_start": 1200, "window_end": 360,
                "rest_minutes": minutes,
            }

        require(client.patch(url, json=body(600)).status_code == 403,
                "Missing CSRF accepted.")
        require(client.patch(url, headers={
            **headers, "Origin": "https://invalid.example",
        }, json=body(600)).status_code == 403, "Foreign origin accepted.")

        for value in (True, "600", -15, 601, 1455):
            require(client.patch(url, headers=headers, json=body(value))
                    .status_code == 422, "Invalid rest duration accepted.")

        changed = client.patch(url, headers=headers, json=body(600))
        require(changed.status_code == 200, "Rest policy update failed.")
        current = changed.json()
        require(current["rest_minutes"] == 600
                and current["location_revision"] == original["location_revision"]+1,
                "Rest policy did not persist with revision.")
        require(client.patch(url, headers=headers, json=body(660))
                .status_code == 409, "Stale policy update accepted.")

        before = audit_count()
        require(client.patch(url, headers=headers, json=body(
            600, current["location_revision"],
        )).status_code == 200, "Unchanged policy failed.")
        require(audit_count() == before, "Unchanged policy wrote audit history.")

        barrier = Barrier(2)
        def race(minutes):
            with TestClient(app) as other:
                other.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return other.patch(url, headers=headers, json=body(
                    minutes, current["location_revision"],
                )).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(race, (660, 780)))
        require(sorted(outcomes) == [200, 409],
                "Concurrent rest policy updates bypassed revisions.")
        current = client.get(url).json()

        admin("""
            CREATE FUNCTION scheduling.reject_rest_policy_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='settings.rest_policy_updated' THEN
                    RAISE EXCEPTION 'Synthetic rest audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_rest_policy_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_rest_policy_audit_test();
        """, database)
        before = audit_count()
        require(client.patch(url, headers=headers, json=body(
            840, current["location_revision"],
        )).status_code == 503, "Audit failure not exercised.")
        require(client.get(url).json() == current
                and audit_count() == before, "Audit failure left partial changes.")
        admin(
            "DROP TRIGGER reject_rest_policy_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_rest_policy_audit_test();", database,
        )

        require(client.get(url.replace(location, str(uuid4()), 1))
                .status_code == 404, "Foreign location policy exposed.")

        loc = sql.Literal(location).as_string()
        admin(f"""
            UPDATE scheduling.member_permission SET permission='scheduler'
            WHERE organization_id=(
                SELECT organization_id FROM scheduling.location WHERE id={loc}
            ) AND permission='administrator';
        """, database)
        require(client.get(url).status_code == 403,
                "Scheduler could read administrator rest settings.")
        require(client.patch(url, headers=headers, json=body(
            720, current["location_revision"],
        )).status_code == 403, "Scheduler could change rest policy.")

    print("ADMIN_REST_POLICY_DEFAULTS_UPDATE_AND_PERSISTENCE=PASS")
    print("REST_POLICY_CSRF_VALIDATION_AND_STAFF_ISOLATION=PASS")
    print("REST_POLICY_STALE_AND_CONCURRENT_EDITS=PASS")
    print("REST_POLICY_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
