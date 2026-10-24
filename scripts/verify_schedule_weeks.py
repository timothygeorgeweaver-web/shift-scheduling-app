"""Draft week HTTP and database checks in the disposable database only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
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
        "Schedule-week checks require the disposable database.",
    )
    install("0015_draft_schedule_weeks", test_db)
    url = "/api/v1/staff/locations/"+location+"/schedule-weeks"
    token = identities["scheduler_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    anchor = date.today()+timedelta(days=210)

    with TestClient(app) as client:
        require(client.get(url, params={
            "start": anchor.isoformat(), "end": (anchor+timedelta(days=90)).isoformat()
        }).status_code == 401, "Anonymous draft listing accepted.")
        client.cookies.set(COOKIE_NAME, token)

        def listing():
            response = client.get(url, params={
                "start": anchor.isoformat(),
                "end": (anchor+timedelta(days=90)).isoformat(),
            })
            require(response.status_code == 200, "Draft week listing failed.")
            require(response.headers.get("cache-control") == "no-store",
                    "Draft schedule response can be cached.")
            return response.json()

        state = listing()
        first = anchor+timedelta(
            days=(state["location"]["week_start_day"]-anchor.weekday()) % 7
        )
        require(state["weeks"] == [], "Migration seeded schedule weeks.")

        def payload(day):
            return {
                "week_start_date": day.isoformat(),
                "expected_location_revision": state["location"]["revision"],
            }

        body = payload(first)
        require(client.post(url, json=body).status_code == 403,
                "Missing schedule creation CSRF accepted.")
        require(client.post(url, headers=headers, json={
            **body, "state": "published"
        }).status_code == 422, "Client could set schedule publication state.")
        require(client.post(url, headers=headers, json=payload(
            first+timedelta(days=1)
        )).status_code == 422, "Incorrect week-start date accepted.")
        require(client.post(url, headers=headers, json={
            **body, "expected_location_revision": state["location"]["revision"]+1
        }).status_code == 409, "Stale location revision accepted.")

        created = client.post(url, headers=headers, json=body)
        require(created.status_code == 201
                and created.json()["state"] == "draft"
                and created.json()["revision"] == 1
                and created.json()["created_by"] == identities["scheduler_a"]["user_id"],
                "Blank draft creation failed.")
        require(client.post(url, headers=headers, json=body).status_code == 409,
                "Duplicate schedule week accepted.")
        require(len(listing()["weeks"]) == 1, "Saved week did not persist.")

        for label, status in (("employee_a", 403), ("admin_b", 404)):
            with TestClient(app) as denied:
                other_token = identities[label]["token"]
                denied.cookies.set(COOKIE_NAME, other_token)
                require(denied.get(url, params={
                    "start": anchor.isoformat(),
                    "end": (anchor+timedelta(days=90)).isoformat(),
                }).status_code == status, "Draft week read isolation failed.")
                require(denied.post(url, headers={
                    "X-CSRF-Token": _csrf(other_token)
                }, json=payload(first+timedelta(days=7))).status_code == status,
                        "Draft week write isolation failed.")

        barrier = Barrier(2)
        race_body = payload(first+timedelta(days=7))
        def race(_):
            with TestClient(app) as contender:
                contender.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return contender.post(
                    url, headers=headers, json=race_body
                ).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(race, (0, 1)))
        require(sorted(outcomes) == [201, 409],
                "Concurrent requests created duplicate weeks.")

        admin("""
            CREATE FUNCTION scheduling.reject_week_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='schedule.week_created' THEN
                    RAISE EXCEPTION 'Synthetic week audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_week_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_week_audit_test();
        """, test_db)
        before = fingerprint(test_db)
        require(client.post(url, headers=headers, json=payload(
            first+timedelta(days=14)
        )).status_code == 503, "Schedule audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Audit failure retained a draft week.")
        admin(
            "DROP TRIGGER reject_week_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_week_audit_test();", test_db,
        )

    # The database independently denies unauthorized function calls.
    try:
        with psycopg.connect(database_url()) as conn:
            conn.execute(
                "SELECT set_config('scheduling.session_sha256',%s,true)",
                (identities["employee_a"]["digest"],),
            )
            conn.execute(
                "SELECT scheduling.create_blank_schedule_week(%s,%s,%s)",
                (location, first+timedelta(days=21), state["location"]["revision"]),
            )
    except psycopg.Error as error:
        require(error.sqlstate == "42501", "Unexpected direct creation denial.")
    else:
        raise SystemExit("STOP: Employee created a draft through the database.")

    try:
        with psycopg.connect(database_url()) as conn:
            conn.execute("SELECT * FROM scheduling.schedule_week")
    except psycopg.Error as error:
        require(error.sqlstate == "42501", "Unexpected raw schedule-table denial.")
    else:
        raise SystemExit("STOP: Runtime has direct draft-table access.")

    print("BLANK_DRAFT_WEEK_CREATE_LIST_AND_PERSISTENCE=PASS")
    print("DRAFT_WEEK_STAFF_AUTHORIZATION_CSRF_AND_ISOLATION=PASS")
    print("WEEK_START_LOCATION_REVISION_AND_DUPLICATE_CHECKS=PASS")
    print("CONCURRENT_WEEK_CREATION=PASS")
    print("DRAFT_WEEK_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
    print("GENERAL_SCHEDULE_TABLE_ACCESS=DENIED")
