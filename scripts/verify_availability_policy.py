"""Saved availability policy checks in the disposable database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
import re
from threading import Barrier

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.availability_calendar import CalendarPolicy, cutoff_for_week
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Availability policy checks must use the disposable database.",
    )
    install("0009_availability_policy", test_db)
    url = "/api/v1/staff/locations/" + location + "/availability-policy"
    token = identities["admin_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}

    with TestClient(app) as client:
        require(client.get(url).status_code == 401, "Anonymous policy access.")
        client.cookies.set(COOKIE_NAME, token)
        response = client.get(url)
        require(response.status_code == 200, "Policy read failed.")
        state = response.json()
        require(
            (state["week_start"], state["cutoff_days_before"], state["cutoff_time"])
            == (0, 7, "17:00"),
            "Approved defaults were not persisted.",
        )

        def body(current):
            return {
                "expected_revision": current["revision"],
                "week_start": 6,
                "cutoff_days_before": 3,
                "cutoff_time": "12:30",
            }

        payload = body(state)
        require(client.patch(url, json=payload).status_code == 403,
                "Policy mutation accepted missing CSRF.")
        for change in (
            {"week_start": True}, {"week_start": 7},
            {"cutoff_days_before": -1}, {"cutoff_days_before": 366},
            {"cutoff_time": "24:00"}, {"cutoff_time": "17:00:30"},
            {"organization_id": location},
        ):
            require(client.patch(
                url, headers=headers, json={**payload, **change}
            ).status_code == 422, "Invalid policy input accepted.")

        for label, status in (
            ("scheduler_a", 403), ("employee_a", 403), ("admin_b", 404)
        ):
            with TestClient(app) as denied:
                other = identities[label]["token"]
                denied.cookies.set(COOKIE_NAME, other)
                require(denied.get(url).status_code == status,
                        "Policy read isolation failed.")
                require(denied.patch(url, json=payload,
                    headers={"X-CSRF-Token": _csrf(other)}
                ).status_code == status, "Unauthorized policy save accepted.")

        saved = client.patch(url, headers=headers, json=payload)
        require(saved.status_code == 200, "Policy save failed.")
        state = client.get(url).json()
        require(
            (state["week_start"], state["cutoff_days_before"], state["cutoff_time"])
            == (6, 3, "12:30"),
            "Policy did not persist.",
        )
        policy = CalendarPolicy(
            timezone=state["timezone"], week_start=state["week_start"],
            cutoff_days_before=state["cutoff_days_before"],
            cutoff_time=time.fromisoformat(state["cutoff_time"]),
        )
        require(
            cutoff_for_week(date(2026, 10, 25), policy).isoformat()
            == "2026-10-22T19:30:00+00:00",
            "Saved policy does not drive the shared calendar correctly.",
        )
        require(client.patch(url, headers=headers, json=payload).status_code == 409,
                "Stale policy save accepted.")

        settings_url = url.removesuffix("availability-policy") + "settings"
        settings = client.get(settings_url).json()
        require(client.patch(settings_url, headers=headers, json={
            "expected_organization_revision": settings["organization_revision"],
            "expected_location_revision": settings["location_revision"],
            "business_name": settings["business_name"],
            "location_name": settings["location_name"],
            "timezone": settings["timezone"],
        }).status_code == 200, "Shared location revision check setup failed.")
        require(client.patch(
            url, headers=headers, json=body(state)
        ).status_code == 409, "Location edit did not invalidate an old policy form.")
        state = client.get(url).json()

    # Database authorization is independently enforced.
    try:
        with psycopg.connect(database_url()) as conn:
            conn.execute(
                "SELECT set_config('scheduling.session_sha256',%s,true)",
                (identities["scheduler_a"]["digest"],),
            )
            conn.execute(
                "SELECT scheduling.edit_availability_policy(%s,%s,0,7,'17:00')",
                (location, state["revision"]),
            )
    except psycopg.Error as error:
        require(error.sqlstate == "42501", "Unexpected direct policy denial.")
    else:
        raise SystemExit("STOP: Direct unauthorized policy update accepted.")

    barrier = Barrier(2)
    payload = body(state)

    def concurrent(days):
        with TestClient(app) as client:
            client.cookies.set(COOKIE_NAME, token)
            barrier.wait(timeout=10)
            return client.patch(
                url, headers=headers, json={**payload, "cutoff_days_before": days}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(concurrent, (4, 5)))
    require(sorted(outcomes) == [200, 409], "Concurrent policy updates overwrote changes.")

    admin("""
        CREATE FUNCTION scheduling.reject_policy_audit_test()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.action='availability.policy_updated' THEN
            RAISE EXCEPTION 'Synthetic policy audit failure';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER reject_policy_audit_test
        BEFORE INSERT ON scheduling.audit_event
        FOR EACH ROW EXECUTE FUNCTION scheduling.reject_policy_audit_test();
    """, test_db)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        state = client.get(url).json()
        before = fingerprint(test_db)
        require(client.patch(
            url, headers=headers, json=body(state)
        ).status_code == 503, "Policy audit failure path was not exercised.")
        require(fingerprint(test_db) == before, "Policy audit failure retained changes.")
    admin(
        "DROP TRIGGER reject_policy_audit_test ON scheduling.audit_event;"
        "DROP FUNCTION scheduling.reject_policy_audit_test();", test_db,
    )
    print("SAVED_AVAILABILITY_POLICY_AND_CALENDAR_INTEGRATION=PASS")
    print("POLICY_AUTHORIZATION_CSRF_AND_VALIDATION=PASS")
    print("POLICY_SHARED_REVISION_AND_CONCURRENT_EDITS=PASS")
    print("POLICY_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
