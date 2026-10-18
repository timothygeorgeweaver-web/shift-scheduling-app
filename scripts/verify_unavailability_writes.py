"""Employee writes certified only inside the disposable integration database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
import re
from threading import Barrier

from fastapi.testclient import TestClient
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg import sql
from psycopg.types.json import Jsonb

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location, own, other):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Write certification requires the disposable database.",
    )
    install("0011_employee_unavailability_writes", test_db)
    literal = lambda value: sql.Literal(value).as_string()
    admin(
        f"UPDATE scheduling.employee SET active=true WHERE id={literal(own)};",
        test_db,
    )
    today = date.fromisoformat(admin(
        "SELECT (clock_timestamp() AT TIME ZONE timezone)::date "
        f"FROM scheduling.location WHERE id={literal(location)};", test_db,
    ))
    first = today + timedelta(days=60)
    first += timedelta(days=(-first.weekday()) % 7)
    frozen_day = first + timedelta(days=2)
    race_day = first + timedelta(days=3)
    token = identities["employee_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    url = "/api/v1/me/unavailability/" + location

    admin(
        "INSERT INTO scheduling.unavailability_day_lock("
        "organization_id,location_id,employee_id,local_date,blocked_windows,"
        "reason,actor_user_id) "
        f"SELECT organization_id,id,{literal(own)},"
        f"{literal(frozen_day.isoformat())}::date,"
        "'[[480,540]]'::jsonb,'publication',"
        f"{literal(identities['employee_a']['user_id'])} "
        f"FROM scheduling.location WHERE id={literal(location)};",
        test_db,
    )

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)

        def calendar(day=first, days=7):
            response = client.get(url, params={"start": day.isoformat(), "days": days})
            require(response.status_code == 200, "Calendar read failed.")
            return response.json()

        def payload(operation, day, revision, windows):
            state = calendar(day, 1)
            body = {
                "operation": operation, "date": day.isoformat(),
                "expected_revision": revision,
                "expected_location_revision": state["profile"]["location_revision"],
            }
            body["weekly_windows" if operation == "recurring" else "windows"] = windows
            return body

        def save(body):
            return client.post(url, headers=headers, json=body)

        revision = calendar()["pattern_revision"]
        pattern = {"0": [{"start": 1200, "end": 120, "ends_next_day": True}]}
        body = payload("recurring", first, revision, pattern)
        require(client.post(url, json=body).status_code == 403,
                "Missing CSRF was accepted.")
        for change in (
            {"employee_id": own},
            {"expected_revision": True},
            {"weekly_windows": {"7": []}},
            {"weekly_windows": {"0": [{"start": 1200, "end": 120}]}},
            {"weekly_windows": {"0": [{"start": True, "end": 120}]}},
            {"weekly_windows": {"0": [{"start": 0, "end": 1440,
                                      "ends_next_day": True}]}},
        ):
            require(save({**body, **change}).status_code == 422,
                    "Invalid employee write accepted.")
        require(save(body).status_code == 200, "Recurring save failed.")
        require(save(body).status_code == 409, "Stale pattern overwrote a save.")
        state = calendar()
        require(state["effective_days"][0]["blocked"] == [[1200, 1440]]
                and state["effective_days"][1]["blocked"] == [[0, 120]],
                "Saved recurring overnight pattern did not resolve.")

        tomorrow = first + timedelta(days=1)
        empty = payload("exception", tomorrow, 0, [])
        require(save(empty).status_code == 200, "Empty exception save failed.")
        require(calendar()["effective_days"][1]["blocked"] == [],
                "Empty exception did not clear carry-in.")
        deletion = payload("delete", tomorrow, 1, [])
        require(save(deletion).status_code == 200, "Exception deletion failed.")
        require(calendar()["effective_days"][1]["blocked"] == [[0, 120]],
                "Deletion did not restore recurring carry-in.")
        require(save(empty).status_code == 409,
                "Deletion tombstone failed to prevent a stale save.")

        overnight = payload("exception", tomorrow, 2, [
            {"start": 1320, "end": 180, "ends_next_day": True}
        ])
        require(save(overnight).status_code == 200, "Overnight exception failed.")
        frozen = calendar()["effective_days"][2]
        require(frozen["blocked"] == [[480, 540]]
                and frozen["source"] == "frozen",
                "Overnight exception changed a frozen adjacent date.")
        require(save(payload("exception", frozen_day, 0, [])).status_code == 423,
                "Published date accepted an employee edit.")
        require(save(payload("exception", today-timedelta(days=7), 0, [])).status_code
                == 423, "Past/cutoff date accepted an employee edit.")

        # Later recurring versions preserve earlier origins and frozen days.
        current = calendar()["pattern_revision"]
        replacement = {"2": [{"start": 600, "end": 660}]}
        require(save(payload("recurring", first, current, replacement)).status_code
                == 200, "Recurring replacement failed.")
        require(calendar()["effective_days"][2]["blocked"] == [[480, 540]],
                "Recurring replacement changed a frozen date.")

        stale_policy = payload("exception", race_day, 0, [])
        stale_policy["expected_location_revision"] -= 1
        require(save(stale_policy).status_code in (409, 422),
                "Old policy revision was accepted.")

        # Database validation independently rejects a malformed direct call.
        try:
            with psycopg.connect(database_url()) as conn:
                conn.execute(
                    "SELECT set_config('scheduling.session_sha256',%s,true)",
                    (identities["employee_a"]["digest"],),
                )
                conn.execute(
                    "SELECT scheduling.write_my_unavailability(%s,%s,%s,%s,%s,%s)",
                    (location, "exception", race_day, 0,
                     calendar()["profile"]["location_revision"],
                     Jsonb([{"start": 1200, "end": 120}])),
                )
        except psycopg.Error as error:
            require(error.sqlstate == "22023", "Unexpected direct validation error.")
        else:
            raise SystemExit("STOP: Direct malformed interval was accepted.")

        barrier = Barrier(2)
        race_body = payload("exception", race_day, 0, [])
        def concurrent(start):
            with TestClient(app) as contender:
                contender.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return contender.post(url, headers=headers, json={
                    **race_body, "windows": [{"start": start, "end": start+60}]
                }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(concurrent, (600, 720)))
        require(sorted(outcomes) == [200, 409],
                "Concurrent exception saves overwrote one another.")

        # Failed audit inserts must roll back employee changes too.
        admin("""
            CREATE FUNCTION scheduling.reject_unavailability_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action LIKE 'unavailability.%' THEN
                    RAISE EXCEPTION 'Synthetic unavailability audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_unavailability_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION
                scheduling.reject_unavailability_audit_test();
        """, test_db)
        rollback_body = payload("exception", first+timedelta(days=4), 0, [])
        before = fingerprint(test_db)
        require(save(rollback_body).status_code == 503,
                "Audit failure path was not exercised.")
        require(fingerprint(test_db) == before,
                "Audit failure retained unavailability changes.")
        admin(
            "DROP TRIGGER reject_unavailability_audit_test "
            "ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_unavailability_audit_test();",
            test_db,
        )

        # Exercise DST ambiguity/gap rejection with future transition dates.
        original = json.loads(admin(
            "SELECT json_build_object('week_start',week_start,"
            "'days',availability_cutoff_days,"
            "'time',availability_cutoff_time::text) "
            f"FROM scheduling.location WHERE id={literal(location)};", test_db,
        ))
        for day, clock in ((date(2030, 3, 10), "02:30"),
                           (date(2030, 11, 3), "01:30")):
            admin(
                "UPDATE scheduling.location SET week_start=6,"
                "availability_cutoff_days=0,"
                f"availability_cutoff_time={literal(clock)}::time,"
                "timezone='America/Los_Angeles',revision=revision+1 "
                f"WHERE id={literal(location)};", test_db,
            )
            require(save(payload("exception", day, 0, [])).status_code == 422,
                    "Ambiguous/nonexistent cutoff was silently accepted.")
        admin(
            "UPDATE scheduling.location SET "
            f"week_start={original['week_start']},"
            f"availability_cutoff_days={original['days']},"
            f"availability_cutoff_time={literal(original['time'])}::time,"
            "revision=revision+1 "
            f"WHERE id={literal(location)};", test_db,
        )

    # Shared staff history must not expose this employee's interval history.
    for label in ("scheduler_a", "admin_a"):
        with TestClient(app) as staff:
            staff.cookies.set(COOKIE_NAME, identities[label]["token"])
            response = staff.get(
                "/api/v1/staff/locations/"+location+"/history", params={"limit": 100}
            )
            require(response.status_code == 200, "Staff history regression.")
            require(not any(row["action"].startswith("unavailability.")
                            for row in response.json()),
                    "Private intervals leaked through coworker history.")

    with TestClient(app) as denied:
        denied.cookies.set(COOKIE_NAME, identities["admin_b"]["token"])
        require(denied.post(url, headers={
            "X-CSRF-Token": _csrf(identities["admin_b"]["token"])
        }, json=race_body).status_code == 404,
                "Cross-business employee write accepted.")

    # Same-location scheduler writes affect only their own linked profile.
    with TestClient(app) as peer:
        peer_token = identities["scheduler_a"]["token"]
        peer.cookies.set(COOKIE_NAME, peer_token)
        state = peer.get(url, params={"start": first.isoformat()}).json()
        require(state["profile"]["employee_id"] == other,
                "Peer did not resolve to their own profile.")
        before = admin(
            "SELECT md5(string_agg(to_jsonb(x)::text,'' ORDER BY local_date)) "
            "FROM scheduling.unavailability_exception x "
            f"WHERE employee_id={literal(own)};", test_db,
        )
        require(peer.post(url, headers={"X-CSRF-Token": _csrf(peer_token)}, json={
            **race_body,
            "expected_location_revision": state["profile"]["location_revision"],
            "windows": [{"start": 300, "end": 360}],
        }).status_code == 200, "Peer's own exception save failed.")
        require(admin(
            "SELECT md5(string_agg(to_jsonb(x)::text,'' ORDER BY local_date)) "
            "FROM scheduling.unavailability_exception x "
            f"WHERE employee_id={literal(own)};", test_db,
        ) == before, "Peer changed another employee's exception.")

    admin(f"UPDATE scheduling.employee SET active=false WHERE id={literal(own)};",
          test_db)
    with TestClient(app) as inactive:
        inactive.cookies.set(COOKIE_NAME, token)
        require(inactive.post(url, headers=headers, json=race_body).status_code
                == 404, "Inactive employee retained write access.")

    require(admin(
        "SELECT count(*)>0 AND bool_and(actor_user_id="
        f"{literal(identities['employee_a']['user_id'])}::uuid) "
        "FROM scheduling.audit_event "
        f"WHERE entity_id={literal(own)} AND action LIKE 'unavailability.%';",
        test_db,
    ) == "t", "Employee audit actor/history verification failed.")

    print("EMPLOYEE_RECURRING_EXCEPTION_AND_DELETION_WRITES=PASS")
    print("CSRF_SHAPE_VALIDATION_AND_DATABASE_VALIDATION=PASS")
    print("CUTOFF_PUBLICATION_AND_ADJACENT_FROZEN_DAY_PROTECTION=PASS")
    print("DST_AMBIGUOUS_AND_NONEXISTENT_CUTOFFS=DENIED")
    print("STALE_POLICY_TOMBSTONE_AND_CONCURRENT_EDIT_CHECKS=PASS")
    print("UNAVAILABILITY_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
    print("EMPLOYEE_WRITE_ISOLATION_AND_INACTIVE_PROFILE_DENIAL=PASS")
    print("PRIVATE_INTERVAL_HISTORY_HIDDEN_FROM_COWORKERS=PASS")
