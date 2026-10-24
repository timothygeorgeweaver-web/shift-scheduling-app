"""Direct generic pattern writes in disposable PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import re
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
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
        "Direct template tests require the disposable database.",
    )
    install("0021_direct_template_patterns", test_db)
    token = identities["scheduler_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    base = "/api/v1/staff/locations/"+location

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        role = next(r for r in client.get(base+"/roles").json() if r["active"])
        anchor = date.today()+timedelta(days=650)
        settings = client.get(base+"/schedule-weeks", params={
            "start": anchor.isoformat(),
            "end": (anchor+timedelta(days=14)).isoformat(),
        }).json()["location"]
        url = base+"/schedule-templates"
        blank = {
            "name": "Direct blank template",
            "slots": [],
            "expected_template_revision": 0,
            "expected_location_revision": settings["revision"],
        }
        require(client.post(url, json=blank).status_code == 403,
                "Direct save accepted missing CSRF.")
        before = fingerprint(test_db)
        response = client.post(url, headers=headers, json=blank)
        require(response.status_code == 201, "Blank template creation failed.")
        saved = response.json()
        pattern_url = url+"/"+saved["id"]+"/pattern"
        require(client.get(pattern_url).json()["slots"] == [],
                "Blank template did not remain blank.")
        after = fingerprint(test_db)
        for table in ("schedule_week", "shift_slot"):
            require(after["data"][table] == before["data"][table],
                    "Direct template creation made intermediate schedule records.")

        slot = {
            "role_id": role["id"], "weekday": 0,
            "start_minute": 540, "end_minute": 1020,
            "ends_next_day": False, "note": "Generic coverage",
        }
        body = {
            **blank, "expected_template_revision": saved["revision"],
            "slots": [slot, {**slot, "note": "Second worker"}, {
                **slot, "weekday": 6, "start_minute": 1320,
                "end_minute": 360, "ends_next_day": True,
            }],
        }
        for invalid in (
            {**body, "slots": [{**slot, "start_minute": 541}]},
            {**body, "slots": [{**slot, "role_id": str(uuid4())}]},
            {**body, "slots": [{**slot, "weekday": True}]},
        ):
            require(client.put(pattern_url, headers=headers, json=invalid)
                    .status_code == 422, "Invalid generic pattern accepted.")

        response = client.put(pattern_url, headers=headers, json=body)
        require(response.status_code == 200, "Whole-pattern save failed.")
        actual = client.get(pattern_url).json()
        require(actual["slots"] == body["slots"],
                "Pattern lost weekdays, duplicates or overnight values.")
        require(client.put(pattern_url, headers=headers, json=body)
                .status_code == 409, "Stale whole-pattern save accepted.")

        barrier = Barrier(2)
        raced = {**body, "expected_template_revision": actual["revision"]}

        def race(number):
            with TestClient(app) as other:
                other.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return other.put(pattern_url, headers=headers, json={
                    **raced, "name": f"Direct race {number}",
                }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(race, (1, 2)))
        require(sorted(outcomes) == [200, 409],
                "Concurrent pattern saves bypassed revisions.")

        current = client.get(pattern_url).json()
        admin("""
            CREATE FUNCTION scheduling.reject_direct_pattern_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='schedule.template_pattern_saved' THEN
                    RAISE EXCEPTION 'Synthetic direct pattern audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_direct_pattern_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_direct_pattern_test();
        """, test_db)
        before = fingerprint(test_db)
        require(client.put(pattern_url, headers=headers, json={
            **body, "name": current["name"],
            "expected_template_revision": current["revision"],
            "slots": [],
        }).status_code == 503, "Direct pattern audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Failed pattern save partially replaced the template.")
        admin(
            "DROP TRIGGER reject_direct_pattern_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_direct_pattern_test();", test_db,
        )

        first = anchor+timedelta(
            days=(settings["week_start_day"]-anchor.weekday()) % 7
        )
        applied = client.post(url+"/"+saved["id"]+"/apply", headers=headers, json={
            "week_start_date": first.isoformat(),
            "expected_template_revision": current["revision"],
            "expected_location_revision": settings["revision"],
        })
        require(applied.status_code == 201
                and len(applied.json()["slots"]) == 3,
                "Direct pattern was incompatible with template application.")

        for label, expected in (("employee_a", 403), ("admin_b", 404)):
            with TestClient(app) as denied:
                other = identities[label]["token"]
                denied.cookies.set(COOKIE_NAME, other)
                require(denied.get(pattern_url).status_code == expected,
                        "Generic pattern read isolation failed.")
                require(denied.put(pattern_url,
                    headers={"X-CSRF-Token": _csrf(other)}, json={
                        **body, "expected_template_revision": current["revision"],
                    }).status_code == expected,
                    "Generic pattern write isolation failed.")

    print("DIRECT_BLANK_TEMPLATE_AND_WHOLE_PATTERN_SAVE=PASS")
    print("GENERIC_WEEK_DUPLICATES_OVERNIGHT_AND_APPLY=PASS")
    print("NO_INTERMEDIATE_SCHEDULE_RECORDS=PASS")
    print("DIRECT_PATTERN_VALIDATION_PRIVACY_AND_CONCURRENCY=PASS")
    print("WHOLE_TEMPLATE_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
