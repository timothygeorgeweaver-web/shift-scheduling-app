"""Edit/remove certification; never targets the source database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import re
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location, week_id, role_id, first):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Shift change checks require the disposable database.",
    )
    token = identities["scheduler_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    url = f"/api/v1/staff/locations/{location}/schedule-weeks/{week_id}"
    literal = lambda value: sql.Literal(value).as_string()

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)

        def read():
            response = client.get(url)
            require(response.status_code == 200, "Shift change fixture read failed.")
            return response.json()

        def create():
            state = read()
            response = client.post(url+"/shifts", headers=headers, json={
                "role_id": role_id,
                "local_date": first.isoformat(),
                "start_minute": 555,
                "end_minute": 1050,
                "expected_week_revision": state["week"]["revision"],
                "note": "Change certification",
            })
            require(response.status_code == 201, "Shift change fixture failed.")
            return next(s for s in response.json()["slots"]
                        if s["note"] == "Change certification")

        slot = create()
        shift_url = url+"/shifts/"+slot["id"]

        def edit_body(**changes):
            state = read()
            current = next(s for s in state["slots"] if s["id"] == slot["id"])
            return {
                "role_id": role_id,
                "local_date": first.isoformat(),
                "start_minute": 540,
                "end_minute": 900,
                "ends_next_day": False,
                "note": "Edited coverage",
                "expected_week_revision": state["week"]["revision"],
                "expected_shift_revision": current["revision"],
                **changes,
            }

        def removal_body():
            state = read()
            current = next(s for s in state["slots"] if s["id"] == slot["id"])
            return {
                "expected_week_revision": state["week"]["revision"],
                "expected_shift_revision": current["revision"],
            }

        body = edit_body()
        before = fingerprint(test_db)
        require(client.patch(shift_url, json=body).status_code == 403,
                "Shift edit accepted missing CSRF.")
        require(client.post(shift_url+"/remove",
                            json=removal_body()).status_code == 403,
                "Shift removal accepted missing CSRF.")
        require(client.patch(shift_url, headers={
            **headers, "Origin": "https://invalid.example"
        }, json=body).status_code == 403, "Cross-origin edit accepted.")
        require(client.post(shift_url+"/remove", headers={
            **headers, "Origin": "https://invalid.example"
        }, json=removal_body()).status_code == 403,
                "Cross-origin removal accepted.")
        for invalid in (
            edit_body(role_id=str(uuid4())),
            edit_body(expected_shift_revision=True),
            edit_body(local_date=(first+timedelta(days=7)).isoformat()),
            edit_body(start_minute=1000, end_minute=900),
        ):
            require(client.patch(shift_url, headers=headers, json=invalid)
                    .status_code == 422, "Invalid edit accepted.")
        require(fingerprint(test_db) == before,
                "Rejected shift changes retained writes.")

        response = client.patch(shift_url, headers=headers, json=body)
        require(response.status_code == 200, "Shift edit failed.")
        changed = next(s for s in response.json()["slots"] if s["id"] == slot["id"])
        require(changed["revision"] == slot["revision"]+1
                and changed["estimated_work_minutes"] == 360
                and changed["meal_estimate_minutes"] == 0
                and changed["note"] == "Edited coverage",
                "Shift edit lost identity, revision or recalculated hours.")

        before = fingerprint(test_db)
        require(client.patch(shift_url, headers=headers, json=body)
                .status_code == 409, "Stale edit accepted.")
        require(fingerprint(test_db) == before, "Stale edit changed data.")

        overnight = edit_body(
            local_date=(first+timedelta(days=6)).isoformat(),
            start_minute=1320, end_minute=360, ends_next_day=True,
        )
        response = client.patch(shift_url, headers=headers, json=overnight)
        require(response.status_code == 200, "Overnight edit failed.")
        changed = next(s for s in response.json()["slots"] if s["id"] == slot["id"])
        require(changed["local_end"][:10] ==
                (first+timedelta(days=7)).isoformat(),
                "Overnight edit ended on wrong date.")

        body = edit_body()
        barrier = Barrier(2)

        def concurrent(number):
            with TestClient(app) as other:
                other.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return other.patch(shift_url, headers=headers, json={
                    **body, "note": f"Concurrent change {number}",
                }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(concurrent, (1, 2)))
        require(sorted(results) == [200, 409],
                "Concurrent shift edits bypassed revisions.")

        admin("""
            CREATE FUNCTION scheduling.reject_shift_change_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action IN (
                    'schedule.shift_updated','schedule.shift_removed'
                ) THEN
                    RAISE EXCEPTION 'Synthetic shift change audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_shift_change_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_shift_change_test();
        """, test_db)
        before = fingerprint(test_db)
        require(client.patch(shift_url, headers=headers, json=edit_body())
                .status_code == 503, "Edit audit failure not exercised.")
        require(client.post(shift_url+"/remove", headers=headers,
                            json=removal_body()).status_code == 503,
                "Removal audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Audit failure retained a shift or week change.")
        admin(
            "DROP TRIGGER reject_shift_change_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_shift_change_test();", test_db,
        )

        for label, expected in (("employee_a", 403), ("admin_b", 404)):
            with TestClient(app) as denied:
                other = identities[label]["token"]
                denied.cookies.set(COOKIE_NAME, other)
                other_headers = {"X-CSRF-Token": _csrf(other)}
                require(denied.patch(shift_url, headers=other_headers,
                                     json=edit_body()).status_code == expected,
                        "Shift edit isolation failed.")
                require(denied.post(shift_url+"/remove", headers=other_headers,
                                    json=removal_body()).status_code == expected,
                        "Shift removal isolation failed.")

        admin(f"UPDATE scheduling.schedule_week SET state='archived' "
              f"WHERE id={literal(week_id)};", test_db)
        require(client.patch(shift_url, headers=headers, json=edit_body())
                .status_code == 423, "Non-draft edit accepted.")
        require(client.post(shift_url+"/remove", headers=headers,
                            json=removal_body()).status_code == 423,
                "Non-draft removal accepted.")
        admin(f"UPDATE scheduling.schedule_week SET state='draft' "
              f"WHERE id={literal(week_id)};", test_db)

        removal = removal_body()
        response = client.post(shift_url+"/remove", headers=headers, json=removal)
        require(response.status_code == 200, "Shift removal failed.")
        require(all(s["id"] != slot["id"] for s in read()["slots"]),
                "Removed shift remained in planner.")
        stored = json.loads(admin(
            "SELECT to_jsonb(s) FROM scheduling.shift_slot s "
            f"WHERE id={literal(slot['id'])};", test_db,
        ))
        require(stored["active"] is False,
                "Removal destroyed history instead of deactivating the shift.")
        before = fingerprint(test_db)
        require(client.post(shift_url+"/remove", headers=headers, json=removal)
                .status_code == 409, "Repeated removal accepted.")
        require(fingerprint(test_db) == before, "Repeated removal changed data.")

    print("SHIFT_EDIT_PERSISTENCE_TIMING_AND_OVERNIGHT=PASS")
    print("SHIFT_REMOVAL_AND_HISTORY_RETENTION=PASS")
    print("SHIFT_CHANGE_CSRF_AUTHORIZATION_AND_DRAFT_GUARDS=PASS")
    print("SHIFT_CHANGE_STALE_AND_CONCURRENT_EDITS=PASS")
    print("SHIFT_EDIT_REMOVE_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
