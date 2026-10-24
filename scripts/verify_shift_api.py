"""Draft shift HTTP certification in disposable PostgreSQL only."""

from datetime import date, timedelta
import re
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Shift API checks require the disposable database.",
    )
    token = identities["scheduler_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    location_url = "/api/v1/staff/locations/"+location
    anchor = date.today()+timedelta(days=350)

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        listing = client.get(location_url+"/schedule-weeks", params={
            "start": anchor.isoformat(),
            "end": (anchor+timedelta(days=28)).isoformat(),
        })
        require(listing.status_code == 200, "Shift API fixture settings failed.")
        settings = listing.json()["location"]
        first = anchor+timedelta(
            days=(settings["week_start_day"]-anchor.weekday()) % 7
        )
        created_week = client.post(
            location_url+"/schedule-weeks", headers=headers, json={
                "week_start_date": first.isoformat(),
                "expected_location_revision": settings["revision"],
            },
        )
        require(created_week.status_code == 201, "Shift API fixture week failed.")
        week_id = created_week.json()["id"]
        url = location_url+"/schedule-weeks/"+week_id

        role_response = client.post(location_url+"/roles", headers=headers, json={
            "name": "Shift API test role",
        })
        require(role_response.status_code == 201, "Shift API fixture role failed.")
        role_id = role_response.json()["id"]

        def read():
            response = client.get(url)
            require(response.status_code == 200, "Schedule detail HTTP read failed.")
            return response

        state = read().json()
        require(state["slots"] == [], "New schedule detail was not empty.")
        require(read().headers.get("cache-control") == "no-store",
                "Draft detail can be cached.")

        def payload(**changes):
            body = {
                "role_id": role_id,
                "local_date": first.isoformat(),
                "start_minute": 540,
                "end_minute": 1050,
                "ends_next_day": False,
                "expected_week_revision": read().json()["week"]["revision"],
                "note": "Front counter",
            }
            return {**body, **changes}

        body = payload()
        require(client.post(url+"/shifts", json=body).status_code == 403,
                "Shift creation accepted missing CSRF.")
        require(client.post(url+"/shifts", headers={
            **headers, "Origin": "https://invalid.example"
        }, json=body).status_code == 403, "Cross-origin shift creation accepted.")

        for invalid in (
            payload(employee_id=str(uuid4())),
            payload(lunch_start="12:00"),
            payload(start_minute=True),
            payload(ends_next_day=1),
            payload(start_minute=1200, end_minute=120),
            payload(local_date=(first+timedelta(days=7)).isoformat()),
            payload(role_id=str(uuid4())),
            payload(note="x"*501),
        ):
            require(client.post(
                url+"/shifts", headers=headers, json=invalid,
            ).status_code == 422, "Invalid shift HTTP payload accepted.")

        response = client.post(url+"/shifts", headers=headers, json=body)
        require(response.status_code == 201, "Shift HTTP creation failed.")
        slot = response.json()["slots"][0]
        require(slot["role_id"] == role_id
                and slot["role_name"] == "Shift API test role"
                and slot["duration_minutes"] == 510
                and slot["meal_estimate_minutes"] == 30
                and slot["estimated_work_minutes"] == 480,
                "Shift HTTP meal estimate or role projection failed.")
        require(read().json()["slots"][0]["id"] == slot["id"],
                "HTTP-created shift did not persist.")

        before = fingerprint(test_db)
        require(client.post(url+"/shifts", headers=headers, json=body).status_code
                == 409, "Stale HTTP shift request accepted.")
        require(fingerprint(test_db) == before,
                "Stale HTTP request changed the schedule.")

        response = client.post(url+"/shifts", headers=headers, json=payload(
            local_date=(first+timedelta(days=6)).isoformat(),
            start_minute=1320, end_minute=360, ends_next_day=True,
            note="Overnight coverage",
        ))
        require(response.status_code == 201, "Overnight HTTP shift failed.")
        overnight = next(
            row for row in response.json()["slots"]
            if row["note"] == "Overnight coverage"
        )
        require(overnight["local_end"][:10] ==
                (first+timedelta(days=7)).isoformat(),
                "Final-day overnight shift did not end on the next date.")

        admin("""
            CREATE FUNCTION scheduling.reject_shift_api_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='schedule.shift_created' THEN
                    RAISE EXCEPTION 'Synthetic shift API audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_shift_api_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_shift_api_audit_test();
        """, test_db)
        rollback_body = payload(note="Must not persist")
        before = fingerprint(test_db)
        require(client.post(
            url+"/shifts", headers=headers, json=rollback_body,
        ).status_code == 503, "Shift API audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Shift API audit failure retained a slot or week revision.")
        admin(
            "DROP TRIGGER reject_shift_api_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_shift_api_audit_test();", test_db,
        )

        from scripts.verify_shift_changes import verify as verify_changes
        verify_changes(test_db, identities, location, week_id, role_id, first)

        require(client.get(location_url+"/schedule-weeks/"+str(uuid4())).status_code
                == 404, "Unknown schedule detail was exposed.")

        literal = lambda value: sql.Literal(value).as_string()
        admin(
            f"UPDATE scheduling.schedule_week SET state='archived' "
            f"WHERE id={literal(week_id)};", test_db,
        )
        require(client.post(
            url+"/shifts", headers=headers, json=payload(),
        ).status_code == 423, "Non-Draft HTTP shift creation accepted.")

    with TestClient(app) as anonymous:
        require(anonymous.get(url).status_code == 401,
                "Anonymous schedule detail accepted.")

    for label, status in (("employee_a", 403), ("admin_b", 404)):
        with TestClient(app) as denied:
            other_token = identities[label]["token"]
            denied.cookies.set(COOKIE_NAME, other_token)
            require(denied.get(url).status_code == status,
                    "Draft shift read isolation failed.")
            require(denied.post(url+"/shifts", headers={
                "X-CSRF-Token": _csrf(other_token)
            }, json=body).status_code == status,
                    "Draft shift write isolation failed.")

    print("SHIFT_HTTP_CREATE_DETAIL_AND_PERSISTENCE=PASS")
    print("SHIFT_HTTP_MEAL_ESTIMATE_AND_OVERNIGHT_PROJECTION=PASS")
    print("SHIFT_HTTP_CSRF_ORIGIN_AND_INPUT_VALIDATION=PASS")
    print("SHIFT_HTTP_STALE_AND_AUDIT_FAILURE_ROLLBACK=PASS")
    print("SHIFT_HTTP_STAFF_ISOLATION_AND_NON_DRAFT_DENIAL=PASS")
