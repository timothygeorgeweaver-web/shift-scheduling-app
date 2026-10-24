"""Planner HTTP certification against disposable PostgreSQL data."""

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


def verify(test_db, identities, location, own, other):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Planner API checks require the disposable database.",
    )
    literal = lambda value: sql.Literal(value).as_string()
    admin(
        f"UPDATE scheduling.employee SET active=true WHERE id={literal(own)};",
        test_db,
    )
    first = date.today() + timedelta(days=120)
    first += timedelta(days=(-first.weekday()) % 7)
    url = "/api/v1/me/planner/" + location
    token = identities["employee_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}

    with TestClient(app) as client:
        require(client.get(url, params={"start": first.isoformat()}).status_code
                == 401, "Anonymous planner read accepted.")
        client.cookies.set(COOKIE_NAME, token)

        def read():
            response = client.get(url, params={"start": first.isoformat()})
            require(response.status_code == 200, "Planner API read failed.")
            return response

        state = read().json()
        require(read().headers.get("cache-control") == "no-store",
                "Planner response can be cached.")
        require(state["profile"]["employee_id"] == own
                and all(day["effective_cells"] == [] for day in state["days"]),
                "Planner did not project a private blank week.")
        require(client.get("/api/v1/me/planner/profiles").status_code == 200,
                "Planner profile endpoint failed.")

        def body(changes):
            return {
                "first_date": first.isoformat(),
                "expected_location_revision": state["profile"]["location_revision"],
                "changes": changes,
            }

        def change(offset, revision, cells):
            return {
                "date": (first+timedelta(days=offset)).isoformat(),
                "expected_revision": revision, "cells": cells,
            }

        payload = body([change(0, 0, [0, 1, 95]), change(1, 0, [0, 1])])
        require(client.put(url, json=payload).status_code == 403,
                "Missing planner CSRF accepted.")
        require(client.put(url, headers={
            **headers, "Origin": "https://invalid.example"
        }, json=payload).status_code == 403,
                "Cross-origin planner write accepted.")

        for invalid in (
            {**payload, "employee_id": own},
            body([change(0, 0, [True])]),
            body([change(0, 0, [96])]),
            body([change(0, 0, ["1"])]),
            body([change(0, 0, []), change(0, 0, [2])]),
            body([change(7, 0, [1])]),
            {**payload, "first_date": "9999-12-31"},
        ):
            require(client.put(url, headers=headers, json=invalid).status_code
                    == 422, "Invalid planner HTTP payload accepted.")

        response = client.put(url, headers=headers, json=payload)
        require(response.status_code == 200, "Planner HTTP save failed.")
        saved = response.json()
        require(saved["days"][0]["blocked"] == [[0, 30], [1425, 1440]]
                and saved["days"][1]["blocked"] == [[0, 30]],
                "Planner minute projection was incorrect.")
        require(response.headers.get("cache-control") == "no-store",
                "Planner save response can be cached.")

        before = fingerprint(test_db)
        stale = body([change(2, 0, [5]), change(0, 0, [6])])
        require(client.put(url, headers=headers, json=stale).status_code == 409,
                "Stale planner save accepted.")
        require(fingerprint(test_db) == before,
                "Stale HTTP save retained partial changes.")

        require(client.put(url, headers=headers, json=body([
            change(0, 1, [])
        ])).status_code == 200, "Planner clearing failed.")
        state_after = read().json()
        require(state_after["days"][0]["effective_cells"] == []
                and state_after["days"][0]["revision"] == 2
                and state_after["days"][1]["effective_cells"] == [0, 1],
                "Clearing one planner date affected another.")

        frozen_day = first + timedelta(days=3)
        admin(
            "INSERT INTO scheduling.unavailability_day_lock("
            "organization_id,location_id,employee_id,local_date,blocked_windows,"
            "reason,actor_user_id) "
            f"SELECT organization_id,id,{literal(own)},"
            f"{literal(frozen_day.isoformat())}::date,'[[480,540]]'::jsonb,"
            f"'publication',{literal(identities['employee_a']['user_id'])} "
            f"FROM scheduling.location WHERE id={literal(location)};",
            test_db,
        )
        frozen = read().json()["days"][3]
        require(frozen["source"] == "frozen"
                and frozen["effective_cells"] == list(range(32, 36))
                and frozen["blocked"] == [[480, 540]]
                and not frozen["editable"],
                "Frozen planner HTTP projection failed.")
        before = fingerprint(test_db)
        require(client.put(url, headers=headers, json=body([
            change(2, 0, [8]), change(3, 0, [9])
        ])).status_code == 423, "Frozen planner save accepted.")
        require(fingerprint(test_db) == before,
                "Locked HTTP save retained partial changes.")

        # Even non-quarter-hour historical snapshots must remain exact.
        admin(
            "UPDATE scheduling.unavailability_day_lock "
            "SET blocked_windows='[[481,539]]'::jsonb "
            f"WHERE employee_id={literal(own)} "
            f"AND local_date={literal(frozen_day.isoformat())}::date;",
            test_db,
        )
        frozen = read().json()["days"][3]
        require(frozen["effective_cells"] is None
                and frozen["blocked"] == [[481, 539]]
                and not frozen["editable"],
                "Historical snapshot was silently rounded.")

        require(client.get(
            "/api/v1/me/planner/"+str(uuid4()),
            params={"start": first.isoformat()},
        ).status_code == 404, "Unlinked planner location was exposed.")

        admin("""
            CREATE FUNCTION scheduling.reject_planner_api_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='unavailability.date_saved' THEN
                    RAISE EXCEPTION 'Synthetic planner API audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_planner_api_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION
                scheduling.reject_planner_api_audit_test();
        """, test_db)
        before = fingerprint(test_db)
        require(client.put(url, headers=headers, json=body([
            change(5, 0, [10]), change(6, 0, [11])
        ])).status_code == 503, "Planner API audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Planner API audit failure retained changes.")
        admin(
            "DROP TRIGGER reject_planner_api_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_planner_api_audit_test();",
            test_db,
        )

    for label in ("scheduler_a", "admin_b"):
        with TestClient(app) as peer:
            peer_token = identities[label]["token"]
            peer.cookies.set(COOKIE_NAME, peer_token)
            response = peer.get(url, params={"start": first.isoformat()})
            if label == "admin_b":
                require(response.status_code == 404,
                        "Cross-business planner read accepted.")
                require(peer.put(url, headers={
                    "X-CSRF-Token": _csrf(peer_token)
                }, json=payload).status_code == 404,
                        "Cross-business planner write accepted.")
            else:
                require(response.status_code == 200
                        and response.json()["profile"]["employee_id"] == other
                        and all(day["effective_cells"] == []
                                for day in response.json()["days"]),
                        "Same-location employee planner privacy failed.")

    admin(
        f"UPDATE scheduling.employee SET active=false WHERE id={literal(own)};",
        test_db,
    )
    with TestClient(app) as inactive:
        inactive.cookies.set(COOKIE_NAME, token)
        require(inactive.get(url, params={
            "start": first.isoformat()
        }).status_code == 404, "Inactive employee retained planner reads.")
        require(inactive.put(url, headers=headers, json=payload).status_code
                == 404, "Inactive employee retained planner writes.")

    from scripts.verify_planner_bulk import verify as verify_bulk
    verify_bulk(test_db, identities, location, own, other)

    print("PRIVATE_PLANNER_HTTP_READ_SAVE_AND_CLEAR=PASS")
    print("PLANNER_HTTP_CSRF_ORIGIN_AND_VALIDATION=PASS")
    print("PLANNER_HTTP_STALE_LOCK_AND_AUDIT_ROLLBACK=PASS")
    print("FROZEN_HTTP_PROJECTION_WITHOUT_ROUNDING=PASS")
    print("PLANNER_HTTP_EMPLOYEE_PRIVACY_AND_INACTIVE_DENIAL=PASS")
