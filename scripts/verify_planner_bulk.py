"""Additive mass-edit HTTP checks in disposable PostgreSQL only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import re
from threading import Barrier

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location, own, other):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Bulk checks require the disposable database.",
    )
    install("0013_planner_bulk_scope", test_db)
    literal = lambda value: sql.Literal(value).as_string()
    admin(
        f"UPDATE scheduling.employee SET active=true WHERE id={literal(own)};",
        test_db,
    )
    first = date.today()+timedelta(days=180)
    first += timedelta(days=(-first.weekday()) % 7)
    last = first+timedelta(days=14)
    base = "/api/v1/me/planner/"+location
    token = identities["employee_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)

        def read(start=first):
            response = client.get(base, params={"start": start.isoformat()})
            require(response.status_code == 200, "Bulk fixture read failed.")
            return response.json()

        def save_one(day, revision, cells):
            return client.put(base, headers=headers, json={
                "first_date": day.isoformat(),
                "expected_location_revision": read(day)["profile"]["location_revision"],
                "changes": [{
                    "date": day.isoformat(), "expected_revision": revision,
                    "cells": cells,
                }],
            })

        def preview(payload):
            response = client.post(base+"/bulk/preview", headers=headers, json=payload)
            require(response.status_code == 200, "Bulk preview failed.")
            require(response.headers.get("cache-control") == "no-store",
                    "Bulk preview can be cached.")
            return response.json()

        def apply(payload, stamp):
            return client.post(base+"/bulk/apply", headers=headers, json={
                **payload, "expected_preview": stamp,
            })

        require(save_one(first, 0, [4]).status_code == 200,
                "Pre-existing Monday fixture failed.")
        friday = first+timedelta(days=4)
        require(save_one(friday, 0, [60]).status_code == 200,
                "Pre-existing Friday fixture failed.")

        payload = {
            "first_date": first.isoformat(), "last_date": last.isoformat(),
            "weekly_cells": {"0": [5, 6]},
        }
        require(client.post(base+"/bulk/preview", json=payload).status_code == 403,
                "Bulk preview accepted missing CSRF.")
        for bad in (
            {**payload, "employee_id": own},
            {**payload, "weekly_cells": {"7": []}},
            {**payload, "weekly_cells": {"0": [True]}},
            {**payload, "last_date": (first+timedelta(days=366)).isoformat()},
            {**payload, "last_date": (first-timedelta(days=1)).isoformat()},
        ):
            require(client.post(
                base+"/bulk/preview", headers=headers, json=bad,
            ).status_code == 422, "Invalid bulk input accepted.")

        before = fingerprint(test_db)
        shown = preview(payload)
        require(fingerprint(test_db) == before, "Preview wrote persistent data.")
        require([row["date"] for row in shown["changed_dates"]] == [
            first.isoformat(), (first+timedelta(days=7)).isoformat(),
            last.isoformat(),
        ], "Inclusive matching weekday range failed.")

        result = apply(payload, shown["preview_id"])
        require(result.status_code == 200
                and result.json()["changed_date_count"] == 3,
                "Additive bulk submission failed.")
        require(read()["days"][0]["effective_cells"] == [4, 5, 6]
                and read()["days"][4]["effective_cells"] == [60],
                "Bulk edit cleared an existing block.")
        require(read(last)["days"][0]["effective_cells"] == [5, 6]
                and read(last+timedelta(days=7))["days"][0]["effective_cells"] == [],
                "Bulk edit excluded the last date or extended past the range.")

        repeated = preview(payload)
        require(repeated["changed_dates"] == [], "Repeated additions duplicated blocks.")
        before = fingerprint(test_db)
        require(apply(payload, repeated["preview_id"]).status_code == 200,
                "No-change bulk application failed.")
        require(fingerprint(test_db) == before, "No-change apply changed history or rows.")

        # Individual correction remains local to one date.
        require(save_one(first, 2, [4, 6]).status_code == 200,
                "Independent post-bulk date correction failed.")
        require(read(first+timedelta(days=7))["days"][0]["effective_cells"] == [5, 6],
                "Individual correction cascaded to another date.")

        # A changed revision invalidates the previous preview.
        fresh = preview(payload)
        require(save_one(first, 3, [4]).status_code == 200,
                "Stale-preview fixture failed.")
        before = fingerprint(test_db)
        require(apply(payload, fresh["preview_id"]).status_code == 409,
                "Stale mass-edit preview was accepted.")
        require(fingerprint(test_db) == before, "Stale apply retained changes.")

        # A new publication lock changes the confirmed scope.
        fresh = preview(payload)
        admin(
            "INSERT INTO scheduling.unavailability_day_lock("
            "organization_id,location_id,employee_id,local_date,blocked_windows,"
            "reason,actor_user_id) "
            f"SELECT organization_id,id,{literal(own)},"
            f"{literal(last.isoformat())}::date,'[[480,540]]'::jsonb,"
            f"'publication',{literal(identities['employee_a']['user_id'])} "
            f"FROM scheduling.location WHERE id={literal(location)};",
            test_db,
        )
        before = fingerprint(test_db)
        require(apply(payload, fresh["preview_id"]).status_code == 409,
                "New lock did not invalidate the preview.")
        require(fingerprint(test_db) == before, "Lock conflict retained changes.")
        fresh = preview(payload)
        require(fresh["skipped_locked_dates"] == [{
            "date": last.isoformat(), "reason": "published_week",
        }], "Locked dates were not listed before submission.")
        require(apply(payload, fresh["preview_id"]).status_code == 200,
                "Confirmed editable subset could not be applied.")
        require(read(last)["days"][0]["blocked"] == [[480, 540]],
                "Mass edit changed a frozen date.")

        # Fail in the SECOND week: first-week writes must roll back too.
        start2 = first+timedelta(days=35)
        end2 = start2+timedelta(days=7)
        rollback_payload = {
            "first_date": start2.isoformat(), "last_date": end2.isoformat(),
            "weekly_cells": {"0": [10]},
        }
        stamp = preview(rollback_payload)["preview_id"]
        admin(
            "CREATE FUNCTION scheduling.reject_bulk_audit_test() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "IF NEW.action='unavailability.date_saved' "
            f"AND NEW.after_data->>'local_date'={literal(end2.isoformat())} THEN "
            "RAISE EXCEPTION 'Synthetic second-week audit failure'; "
            "END IF; RETURN NEW; END $$;"
            "CREATE TRIGGER reject_bulk_audit_test "
            "BEFORE INSERT ON scheduling.audit_event FOR EACH ROW "
            "EXECUTE FUNCTION scheduling.reject_bulk_audit_test();",
            test_db,
        )
        before = fingerprint(test_db)
        require(apply(rollback_payload, stamp).status_code == 503,
                "Second-week audit failure was not exercised.")
        require(fingerprint(test_db) == before,
                "Second-week failure retained first-week changes.")
        admin(
            "DROP TRIGGER reject_bulk_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_bulk_audit_test();", test_db,
        )

        race_payload = {
            "first_date": (first+timedelta(days=56)).isoformat(),
            "last_date": (first+timedelta(days=63)).isoformat(),
            "weekly_cells": {"0": [20]},
        }
        race_stamp = preview(race_payload)["preview_id"]
        barrier = Barrier(2)
        def race(_):
            with TestClient(app) as contender:
                contender.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return contender.post(base+"/bulk/apply", headers=headers, json={
                    **race_payload, "expected_preview": race_stamp,
                }).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(race, (0, 1)))
        require(sorted(outcomes) == [200, 409],
                "Concurrent mass edits bypassed preview conflict checks.")

        altered = {**race_payload, "weekly_cells": {"0": [21]}}
        require(apply(altered, race_stamp).status_code == 409,
                "Changed template reused an unrelated preview.")

    with TestClient(app) as peer:
        peer_token = identities["scheduler_a"]["token"]
        peer.cookies.set(COOKIE_NAME, peer_token)
        response = peer.post(base+"/bulk/preview", headers={
            "X-CSRF-Token": _csrf(peer_token)
        }, json=payload)
        require(response.status_code == 200
                and all(row["before"] == [] for row in response.json()["changed_dates"]),
                "Peer preview exposed another employee's blocks.")

    with TestClient(app) as denied:
        other_token = identities["admin_b"]["token"]
        denied.cookies.set(COOKIE_NAME, other_token)
        require(denied.post(base+"/bulk/preview", headers={
            "X-CSRF-Token": _csrf(other_token)
        }, json=payload).status_code == 404,
                "Cross-business bulk preview accepted.")

    admin(
        f"UPDATE scheduling.employee SET active=false WHERE id={literal(own)};",
        test_db,
    )
    with TestClient(app) as inactive:
        inactive.cookies.set(COOKIE_NAME, token)
        require(inactive.post(base+"/bulk/preview", headers=headers,
                             json=payload).status_code == 404,
                "Inactive employee retained bulk access.")

    print("ADDITIVE_INCLUSIVE_BULK_PREVIEW_AND_APPLY=PASS")
    print("EXISTING_BLOCKS_AND_INDEPENDENT_DATE_EDITS=PRESERVED")
    print("PREVIEW_NO_WRITES_AND_REPEAT_IDEMPOTENCE=PASS")
    print("STALE_PREVIEW_NEW_LOCK_AND_CONCURRENT_APPLY_DENIAL=PASS")
    print("MULTI_WEEK_AUDIT_FAILURE_FULL_ROLLBACK=PASS")
    print("BULK_CSRF_VALIDATION_AND_EMPLOYEE_PRIVACY=PASS")
