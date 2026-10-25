"""Eight-date planner certification in a disposable database."""

from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth_routes import COOKIE_NAME, _csrf
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin


def verify(test_db, token, location, role):
    first = date.today()+timedelta(days=120)
    first += timedelta(days=(-first.weekday()) % 7)
    last = first+timedelta(days=7)
    url = "/api/v1/me/business-planner/"+location
    legacy = "/api/v1/me/planner/"+location
    headers = {"X-CSRF-Token": _csrf(token)}

    with TestClient(app) as client:
        require(client.get(url, params={"start": first.isoformat()}).status_code == 401,
                "Anonymous business planner read accepted.")
        client.cookies.set(COOKIE_NAME, token)

        def read():
            response = client.get(url, params={"start": first.isoformat()})
            require(response.status_code == 200, "Eight-date read failed.")
            require(response.headers.get("cache-control") == "no-store",
                    "Private planner response can be cached.")
            return response.json()

        state = read()
        require(len(state["calendar_days"]) == 8
                and state["profile"]["business_day_start_minute"] == 300,
                "Eight-date profile projection failed.")

        # Seed cells outside the visible business week.
        for day, anchor, cells in (
            (first, first, [0]),
            (last, first+timedelta(days=1), [20]),
        ):
            response = client.put(legacy, headers=headers, json={
                "first_date": anchor.isoformat(),
                "expected_location_revision": state["profile"]["location_revision"],
                "changes": [{
                    "date": day.isoformat(), "expected_revision": 0, "cells": cells,
                }],
            })
            require(response.status_code == 200, "Boundary fixture failed.")

        def payload(state, modifications):
            rows = {row["date"]: row for row in state["calendar_days"]}
            return {
                "first_date": first.isoformat(),
                "expected_location_revision": state["profile"]["location_revision"],
                "changes": [
                    {"date": day.isoformat(), "expected_revision": rows[day.isoformat()]["revision"],
                     "cells": cells}
                    for day, cells in modifications
                ],
            }

        state = read()
        changes = [
            (first+timedelta(days=n), [0, 20] if n == 0 else [8, 20] if n == 7 else [20])
            for n in range(8)
        ]
        body = payload(state, changes)
        require(client.put(url, json=body).status_code == 403,
                "Business planner accepted missing CSRF.")
        require(client.put(url, headers={**headers, "Origin": "https://invalid.example"},
            json=body).status_code == 403, "Foreign-origin save accepted.")

        invalid = payload(state, [(first, [20])])
        require(client.put(url, headers=headers, json=invalid).status_code == 422,
                "Hidden boundary cells could be erased.")
        response = client.put(url, headers=headers, json=body)
        require(response.status_code == 200, "Atomic eight-date save failed.")
        saved = response.json()
        require(saved["calendar_days"][0]["blocked_cells"] == [0, 20]
                and saved["calendar_days"][7]["blocked_cells"] == [8, 20],
                "Eight-date save lost boundary cells.")
        require(client.put(url, headers=headers, json=body).status_code == 409,
                "Stale eight-date save accepted.")

        # Fail only on the eighth date, after the first batch has written.
        admin(f"""
            CREATE FUNCTION scheduling.reject_eighth_date_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='unavailability.date_saved'
                   AND NEW.after_data->>'local_date'='{last.isoformat()}' THEN
                    RAISE EXCEPTION 'Synthetic eighth-date audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_eighth_date_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_eighth_date_test();
        """, test_db)
        audit_before = admin("SELECT count(*) FROM scheduling.audit_event;", test_db)
        failed = payload(saved, [(first, [0, 20, 21]), (last, [8, 9, 20])])
        require(client.put(url, headers=headers, json=failed).status_code == 503,
                "Eighth-date audit failure not exercised.")
        require(read() == saved, "Eighth-date failure retained partial changes.")
        require(admin("SELECT count(*) FROM scheduling.audit_event;", test_db)
                == audit_before, "Failed save retained partial audit events.")
        admin(
            "DROP TRIGGER reject_eighth_date_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_eighth_date_test();", test_db,
        )

        employee = saved["profile"]["employee_id"]
        admin(f"""
            INSERT INTO scheduling.unavailability_day_lock(
                organization_id,location_id,employee_id,local_date,
                blocked_windows,reason,actor_user_id
            )
            SELECT organization_id,location_id,id,'{last.isoformat()}',
                   '[[481,539]]'::jsonb,'publication',user_id
            FROM scheduling.employee WHERE id='{employee}';
        """, test_db)
        frozen = read()
        require(frozen["calendar_days"][7]["effective_cells"] is None
                and frozen["calendar_days"][7]["blocked"] == [[481, 539]],
                "Frozen eighth-date intervals were rounded.")
        locked_body = payload(frozen, [(first, [0, 20, 21]), (last, [8, 9, 20])])
        require(client.put(url, headers=headers, json=locked_body).status_code == 423,
                "Locked eighth date accepted a change.")
        require(read() == frozen, "Locked save retained partial changes.")

        require(client.get("/api/v1/me/business-planner/"+str(uuid4()),
            params={"start": first.isoformat()}).status_code == 404,
            "Unlinked employee location was exposed.")

    print("PRIVATE_EIGHT_DATE_READ_AND_ATOMIC_SAVE=PASS")
    print("HIDDEN_EDGE_CELLS_AND_STALE_REVISIONS=PRESERVED")
    print("EIGHTH_DATE_AUDIT_FAILURE_FULL_ROLLBACK=PASS")
    print("LOCKED_EIGHTH_DATE_AND_EXACT_FROZEN_INTERVALS=PASS")
    print("BUSINESS_PLANNER_CSRF_ORIGIN_AND_PROFILE_ISOLATION=PASS")
