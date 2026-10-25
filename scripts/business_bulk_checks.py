"""Focused business-day additive mass-edit certification."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.auth_routes import COOKIE_NAME, _csrf
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin


def verify(test_db, token, location, role):
    monday = date.today()+timedelta(days=120)
    monday += timedelta(days=(-monday.weekday()) % 7)
    sunday = monday+timedelta(days=6)
    following = sunday+timedelta(days=1)
    base = "/api/v1/me/business-planner/"+location
    legacy = "/api/v1/me/planner/"+location
    headers = {"X-CSRF-Token": _csrf(token)}

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)

        def read(first=monday):
            response = client.get(base, params={"start": first.isoformat()})
            require(response.status_code == 200, "Mass-edit fixture read failed.")
            return response.json()

        state = read()
        revision = state["profile"]["location_revision"]
        for day, cells in ((sunday, [0]), (following, [40])):
            response = client.put(legacy, headers=headers, json={
                "first_date": sunday.isoformat(),
                "expected_location_revision": revision,
                "changes": [{
                    "date": day.isoformat(), "expected_revision": 0, "cells": cells,
                }],
            })
            require(response.status_code == 200, "Mass-edit seed failed.")

        body = {
            "first_date": sunday.isoformat(), "last_date": sunday.isoformat(),
            "weekly_cells": {"6": [0, 84]},
            "expected_location_revision": revision,
        }

        def preview(payload=body):
            response = client.post(base+"/bulk/preview", headers=headers, json=payload)
            require(response.status_code == 200, "Business mass preview failed.")
            return response.json()

        def apply(plan, payload=body):
            return client.post(base+"/bulk/apply", headers=headers, json={
                **payload, "expected_preview": plan["preview_id"],
            })

        require(client.post(base+"/bulk/preview", json=body).status_code == 403,
                "Missing bulk CSRF accepted.")
        require(client.post(base+"/bulk/preview", headers={
            **headers, "Origin": "https://invalid.example",
        }, json=body).status_code == 403, "Foreign-origin bulk preview accepted.")
        require(client.post(base+"/bulk/preview", headers=headers, json={
            **body, "weekly_cells": {"6": [True]},
        }).status_code == 422, "Boolean business cell accepted.")
        require(client.post(base+"/bulk/preview", headers=headers, json={
            **body, "expected_location_revision": revision+1,
        }).status_code == 409, "Stale rollover policy accepted.")

        before = read()
        audit_before = admin("SELECT count(*) FROM scheduling.audit_event;", test_db)
        plan = preview()
        require(read() == before and admin(
            "SELECT count(*) FROM scheduling.audit_event;", test_db,
        ) == audit_before, "Preview wrote records.")
        require([row["date"] for row in plan["changed_dates"]] ==
                [sunday.isoformat(), following.isoformat()]
                and all(row["business_dates"] == [sunday.isoformat()]
                        for row in plan["changed_dates"]),
                "Inclusive Sunday carry mapping failed.")
        require(apply(plan).status_code == 200, "Business mass apply failed.")
        rows = {row["date"]: row for row in read()["calendar_days"]}
        require(rows[sunday.isoformat()]["blocked_cells"] == [0, 20]
                and rows[following.isoformat()]["blocked_cells"] == [8, 40],
                "Mass edit cleared existing blocks or misplaced early hours.")
        require(apply(plan).status_code == 409, "Stale preview was reused.")
        require(preview()["added_cell_count"] == 0,
                "Repeating an additive pattern created additional blocks.")

        # Fail at the end of a later, multi-week request.
        late_first = sunday+timedelta(days=14)
        late_last = late_first+timedelta(days=7)
        late = {
            **body, "first_date": late_first.isoformat(),
            "last_date": late_last.isoformat(),
        }
        late_plan = preview(late)
        require(len(late_plan["changed_dates"]) == 4,
                "Multi-week inclusive mapping failed.")
        before_late = (read(late_first), read(late_last))
        audit_before = admin("SELECT count(*) FROM scheduling.audit_event;", test_db)
        failure_day = late_last+timedelta(days=1)
        admin(f"""
            CREATE FUNCTION scheduling.reject_business_bulk_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='unavailability.date_saved'
                   AND NEW.after_data->>'local_date'='{failure_day.isoformat()}' THEN
                    RAISE EXCEPTION 'Synthetic final business bulk failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_business_bulk_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_business_bulk_test();
        """, test_db)
        require(apply(late_plan, late).status_code == 503,
                "Final multi-week audit failure not exercised.")
        require((read(late_first), read(late_last)) == before_late
                and admin("SELECT count(*) FROM scheduling.audit_event;", test_db)
                == audit_before, "Failed multi-week apply retained partial changes.")
        admin(
            "DROP TRIGGER reject_business_bulk_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_business_bulk_test();", test_db,
        )

        employee = state["profile"]["employee_id"]
        admin(f"""
            INSERT INTO scheduling.unavailability_day_lock(
                organization_id,location_id,employee_id,local_date,
                blocked_windows,reason,actor_user_id
            )
            SELECT organization_id,location_id,id,'{following.isoformat()}',
                   '[[121,179]]'::jsonb,'publication',user_id
            FROM scheduling.employee WHERE id='{employee}';
        """, test_db)
        locked = preview()
        require(len(locked["skipped_locked_dates"]) == 1
                and locked["skipped_locked_dates"][0]["date"] == following.isoformat(),
                "Actual locked carry date was not skipped.")
        frozen = read()["calendar_days"][7]
        require(apply(locked).status_code == 200
                and read()["calendar_days"][7] == frozen,
                "Mass edit changed a frozen calendar date.")

    print("BUSINESS_DATE_RANGE_AND_SUNDAY_CARRY=PASS")
    print("ADDITIVE_BLOCK_PRESERVATION_AND_REPEAT_IDEMPOTENCE=PASS")
    print("PREVIEW_NO_WRITES_AND_STALE_POLICY_PROTECTION=PASS")
    print("MULTI_WEEK_FINAL_AUDIT_FAILURE_FULL_ROLLBACK=PASS")
    print("ACTUAL_CALENDAR_LOCKS_AND_FROZEN_TIMES=PRESERVED")
