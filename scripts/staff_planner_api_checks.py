from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.auth_routes import COOKIE_NAME, _csrf
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin
from scripts.publication_commit_checks import literal
from scripts.staff_unavailability_checks import verify as verify_foundation


def verify(database, token, location, role):
    verify_foundation(database, token, location, role)
    employee = admin(
        "SELECT id FROM scheduling.employee WHERE location_id=" +
        literal(location) + " AND display_name='Unlinked mock employee'",
        database,
    )
    base = (
        f"/api/v1/staff/locations/{location}/employees/"
        f"{employee}/business-planner"
    )
    first = date(2096, 2, 6)
    last = first + timedelta(days=7)
    headers = {"X-CSRF-Token": _csrf(token)}
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        loaded = client.get(base, params={"start": first.isoformat()})
        require(loaded.status_code == 200, "Staff business-day read failed.")
        data = loaded.json()
        require(len(data["calendar_days"]) == 8,
                "Staff business-day range omitted an edge date.")
        require(loaded.headers.get("cache-control") == "no-store",
                "Staff availability response was cacheable.")
        body = {
            "first_date": first.isoformat(),
            "expected_location_revision": data["profile"]["location_revision"],
            "changes": [
                {"date": first.isoformat(), "expected_revision": 0, "cells": [40]},
                {"date": last.isoformat(), "expected_revision": 0, "cells": [4]},
            ],
        }
        require(client.put(base, json=body).status_code == 403,
                "Staff availability save accepted missing CSRF.")
        require(client.put(base, headers={
            **headers, "Origin": "https://invalid.example",
        }, json=body).status_code == 403,
                "Staff availability accepted foreign origin.")

        admin("""
            CREATE FUNCTION scheduling.reject_staff_availability_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='unavailability.staff_date_saved'
                   AND (NEW.after_data->>'local_date')::date=date '2096-02-13'
                THEN
                    RAISE EXCEPTION 'Synthetic last-date audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_staff_availability_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION
                scheduling.reject_staff_availability_test();
        """, database)
        require(client.put(base, headers=headers, json=body).status_code == 503,
                "Staff eighth-date audit failure was not exercised.")
        require(admin(f"""
            SELECT count(*)=0 FROM scheduling.unavailability_date
            WHERE employee_id={literal(employee)}
              AND local_date BETWEEN date '2096-02-06' AND date '2096-02-13';
        """, database) == "t", "Staff eight-date save retained partial writes.")
        admin("""
            DROP TRIGGER reject_staff_availability_test ON scheduling.audit_event;
            DROP FUNCTION scheduling.reject_staff_availability_test();
        """, database)

        saved = client.put(base, headers=headers, json=body)
        require(saved.status_code == 200, "Staff eight-date save failed.")
        require(client.put(base, headers=headers, json=body).status_code == 409,
                "Stale staff business-day save was accepted.")
        outside = {
            **body, "changes": [
                {"date": first.isoformat(), "expected_revision": 1, "cells": [4, 40]}
            ],
        }
        require(client.put(base, headers=headers, json=outside).status_code == 422,
                "Staff edit changed cells outside the business week.")

        admin(f"""
            DELETE FROM scheduling.member_permission
            WHERE organization_id=(
                SELECT organization_id FROM scheduling.location
                WHERE id={literal(location)}
            ) AND permission IN ('administrator','scheduler');
        """, database)
        require(client.get(base, params={
            "start": first.isoformat()
        }).status_code == 404, "Employee-only account read another employee planner.")

    print("STAFF_EIGHT_DATE_HTTP_SAVE_AND_EDGE_PRESERVATION=PASS")
    print("EIGHTH_DATE_AUDIT_FAILURE_FULL_ROLLBACK=PASS")
    print("STAFF_PLANNER_CSRF_ORIGIN_STALE_AND_ROLE_GUARDS=PASS")
