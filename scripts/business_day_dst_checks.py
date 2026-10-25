"""Actual-date rollover safeguards in disposable PostgreSQL."""

from fastapi.testclient import TestClient

from app.auth_routes import COOKIE_NAME, _csrf
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin


def verify(test_db, token, location, role):
    base = "/api/v1/staff/locations/"+location
    employee = "/api/v1/me/business-planner/"+location
    headers = {"X-CSRF-Token": _csrf(token)}
    revision = 1

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)

        def policy(minute):
            nonlocal revision
            response = client.patch(base+"/business-day-policy", headers=headers, json={
                "business_day_start_minute": minute,
                "expected_location_revision": revision,
            })
            require(response.status_code == 200, "DST policy fixture failed.")
            revision = response.json()["location_revision"]

        def blank(day, expected=201):
            response = client.post(base+"/schedule-weeks", headers=headers, json={
                "week_start_date": day, "expected_location_revision": revision,
            })
            require(response.status_code == expected,
                    "Unexpected DST week creation result: "+str(response.status_code))
            return response.json()

        source = blank("2027-01-04")
        policy(150)
        template_response = client.post(base+"/schedule-templates", headers=headers, json={
            "name": "DST guard fixture", "slots": [],
            "expected_template_revision": 0,
            "expected_location_revision": revision,
        })
        require(template_response.status_code == 201, "DST template fixture failed.")
        template = template_response.json()

        audit_before = admin("SELECT count(*) FROM scheduling.audit_event;", test_db)
        blank("2027-03-08", 422)
        require(client.post(
            base+"/schedule-weeks/"+source["id"]+"/copy",
            headers=headers, json={
                "week_start_date": "2027-03-08",
                "expected_source_revision": source["revision"],
                "expected_location_revision": revision,
            },
        ).status_code == 422, "Copy accepted a nonexistent rollover.")
        require(client.post(
            base+"/schedule-templates/"+template["id"]+"/apply",
            headers=headers, json={
                "week_start_date": "2027-03-08",
                "expected_template_revision": template["revision"],
                "expected_location_revision": revision,
            },
        ).status_code == 422, "Template apply accepted a nonexistent rollover.")
        require(client.get(employee, params={"start": "2027-03-08"})
                .status_code == 422, "Employee planner guessed a missing rollover.")
        require(client.put(employee, headers=headers, json={
            "first_date": "2027-03-08",
            "expected_location_revision": revision,
            "changes": [{"date": "2027-03-08", "expected_revision": 0, "cells": [10]}],
        }).status_code == 422, "Employee save accepted a missing rollover.")
        require(admin("SELECT count(*) FROM scheduling.audit_event;", test_db)
                == audit_before, "Rejected rollover operations retained audit writes.")

        policy(90)
        blank("2027-11-01", 422)
        require(client.get(employee, params={"start": "2027-11-01"})
                .status_code == 422, "Employee planner guessed a repeated rollover.")
        require(client.post(employee+"/bulk/preview", headers=headers, json={
            "first_date": "2027-11-01", "last_date": "2027-11-07",
            "weekly_cells": {"0": [0]},
            "expected_location_revision": revision,
        }).status_code == 422, "Mass editor accepted a repeated rollover.")

        policy(300)
        for first, day, expected in (
            ("2027-03-08", "2027-03-13", 1380),
            ("2027-11-01", "2027-11-06", 1500),
        ):
            week = blank(first)
            response = client.post(
                base+"/schedule-weeks/"+week["id"]+"/shifts",
                headers=headers, json={
                    "role_id": role, "local_date": day,
                    "start_minute": 300, "end_minute": 300,
                    "ends_next_day": True,
                    "expected_week_revision": week["revision"],
                },
            )
            require(response.status_code == 201, "Valid DST-spanning shift failed.")
            slot = response.json()["slots"][0]
            require(slot["duration_minutes"] == expected
                    and slot["meal_estimate_minutes"] == 30
                    and slot["estimated_work_minutes"] == expected-30,
                    "DST elapsed duration or single meal estimate changed.")

        require(client.get(base+"/schedule-weeks/"+source["id"])
                .json()["week"]["business_day_start_minute"] == 300,
                "Policy fixture reinterpreted a saved week.")

    print("MISSING_AND_REPEATED_WEEK_ROLLOVERS=DENIED")
    print("COPY_TEMPLATE_AND_EMPLOYEE_DATE_RANGE_GUARDS=PASS")
    print("REJECTED_DST_REQUESTS_AUDIT_STATE=UNCHANGED")
    print("VALID_05_00_SPRING_AND_FALL_WEEKS=PASS")
    print("POSTGRESQL_23_AND_25_HOUR_SHIFT_ESTIMATES=PASS")
