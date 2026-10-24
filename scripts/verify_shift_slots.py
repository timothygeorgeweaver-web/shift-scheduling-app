"""Controlled shift creation in the disposable database only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
import re
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.database import database_url
from app.shift_timing import calculate_shift_timing
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Shift checks require the disposable database.",
    )
    install("0016_draft_shift_slots", test_db)
    literal = lambda value: sql.Literal(value).as_string()

    settings = json.loads(admin(
        "SELECT json_build_object('revision',revision,'week_start',week_start,"
        "'timezone',timezone) FROM scheduling.location "
        f"WHERE id={literal(location)};", test_db,
    ))
    first = date.today()+timedelta(days=280)
    first += timedelta(days=(settings["week_start"]-first.weekday()) % 7)

    def call(statement, parameters=(), who="scheduler_a"):
        with psycopg.connect(database_url()) as conn:
            conn.execute(
                "SELECT set_config('scheduling.session_sha256',%s,true)",
                (identities[who]["digest"],),
            )
            return conn.execute(statement, parameters).fetchone()[0]

    week = call(
        "SELECT scheduling.create_blank_schedule_week(%s,%s,%s)",
        (location, first, settings["revision"]),
    )
    week_id = week["id"]
    role_id = str(uuid4())
    admin(
        "BEGIN; SET LOCAL ROLE shift_scheduling_owner;"
        "INSERT INTO scheduling.job_role(id,organization_id,location_id,name) "
        f"SELECT {literal(role_id)},organization_id,id,'Shift test role' "
        f"FROM scheduling.location WHERE id={literal(location)}; COMMIT;",
        test_db,
    )

    def read(who="scheduler_a"):
        return call("SELECT scheduling.read_schedule_week(%s)", (week_id,), who)

    def create(start=540, end=1050, *, day=first, next_day=False,
               revision=None, role=role_id, note="", who="scheduler_a"):
        timing = calculate_shift_timing(
            day, start, end, ends_next_day=next_day,
            timezone_name=week["timezone"],
        )
        if revision is None:
            revision = read()["week"]["revision"]
        result = call(
            "SELECT scheduling.create_draft_shift(%s,%s,%s,%s,%s,%s)",
            (
                week_id, role, revision, timing.start_utc,
                timing.end_utc, note,
            ), who,
        )
        return result, timing

    def denied(operation, code):
        try:
            operation()
        except psycopg.Error as error:
            require(error.sqlstate == code, "Unexpected shift denial.")
        else:
            raise SystemExit("STOP: Invalid shift operation succeeded.")

    require(read()["slots"] == [], "New draft did not start empty.")
    result, timing = create(note="First coverage slot")
    slot = result["slots"][0]
    require(
        slot["duration_minutes"] == timing.duration_minutes == 510
        and slot["meal_estimate_minutes"] == timing.meal_estimate_minutes == 30
        and slot["estimated_work_minutes"] == timing.estimated_work_minutes == 480
        and slot["role_id"] == role_id
        and result["week"]["revision"] == 2,
        "Stored shift timing, role or week revision failed.",
    )

    # Identical coverage requirements may represent separate needed workers.
    result, _ = create(note="Second coverage slot")
    require(len(result["slots"]) == 2,
            "Identical role/time coverage slots were incorrectly prohibited.")

    result, timing = create(start=540, end=900, note="Exactly six hours")
    exact = next(row for row in result["slots"] if row["note"] == "Exactly six hours")
    require(exact["meal_estimate_minutes"] == 0
            and exact["estimated_work_minutes"] == 360,
            "Exactly six hours incorrectly received a deduction.")

    before = fingerprint(test_db)
    denied(lambda: create(revision=1), "40001")
    require(fingerprint(test_db) == before, "Stale create retained changes.")
    denied(lambda: create(role=str(uuid4())), "22023")
    denied(lambda: create(note="x"*501), "22023")

    barrier = Barrier(2)
    expected = read()["week"]["revision"]
    def concurrent(number):
        barrier.wait(timeout=10)
        try:
            create(revision=expected, note=f"Concurrent slot {number}")
            return "saved"
        except psycopg.Error as error:
            return error.sqlstate
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(concurrent, (1, 2)))
    require(sorted(outcomes) == ["40001", "saved"],
            "Concurrent shift creation bypassed week revisions.")

    result, timing = create(
        start=1320, end=360, day=first+timedelta(days=6),
        next_day=True, note="Overnight final-day slot",
    )
    overnight = next(row for row in result["slots"]
                     if row["note"] == "Overnight final-day slot")
    require(overnight["duration_minutes"] == timing.duration_minutes
            and overnight["estimated_work_minutes"] == timing.estimated_work_minutes,
            "Overnight shift timing differs from shared calculation.")

    admin("""
        CREATE FUNCTION scheduling.reject_shift_audit_test()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.action='schedule.shift_created' THEN
                RAISE EXCEPTION 'Synthetic shift audit failure';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER reject_shift_audit_test
        BEFORE INSERT ON scheduling.audit_event
        FOR EACH ROW EXECUTE FUNCTION scheduling.reject_shift_audit_test();
    """, test_db)
    before = fingerprint(test_db)
    denied(lambda: create(note="Must roll back"), "P0001")
    require(fingerprint(test_db) == before,
            "Audit failure retained a shift or incremented the week revision.")
    admin(
        "DROP TRIGGER reject_shift_audit_test ON scheduling.audit_event;"
        "DROP FUNCTION scheduling.reject_shift_audit_test();", test_db,
    )

    admin(
        f"UPDATE scheduling.job_role SET active=false WHERE id={literal(role_id)};",
        test_db,
    )
    denied(lambda: create(), "22023")
    require(len(read()["slots"]) == len(result["slots"]),
            "Role deactivation hid historical draft slots.")
    admin(
        f"UPDATE scheduling.job_role SET active=true WHERE id={literal(role_id)};"
        f"UPDATE scheduling.schedule_week SET state='published' WHERE id={literal(week_id)};",
        test_db,
    )
    denied(lambda: create(), "55000")

    denied(lambda: read("employee_a"), "42501")
    denied(lambda: read("admin_b"), "42501")
    denied(lambda: create(who="employee_a", revision=1), "42501")
    denied(lambda: create(who="admin_b", revision=1), "42501")

    try:
        with psycopg.connect(database_url()) as conn:
            conn.execute("SELECT * FROM scheduling.shift_slot")
    except psycopg.Error as error:
        require(error.sqlstate == "42501", "Unexpected raw shift-table denial.")
    else:
        raise SystemExit("STOP: Runtime has direct shift-table access.")

    from scripts.verify_shift_api import verify as verify_api
    verify_api(test_db, identities, location)

    print("ROLE_SPECIFIC_SHIFT_CREATE_AND_READ=PASS")
    print("AUTOMATIC_MEAL_ESTIMATE_MATCHES_SHARED_CALCULATION=PASS")
    print("DUPLICATE_COVERAGE_AND_OVERNIGHT_SHIFT_SUPPORT=PASS")
    print("SHIFT_WEEK_REVISIONS_AND_CONCURRENT_CREATION=PASS")
    print("SHIFT_WEEK_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
    print("INACTIVE_ROLE_NON_DRAFT_AND_UNAUTHORIZED_WRITES=DENIED")
    print("GENERAL_SHIFT_TABLE_ACCESS=DENIED")
