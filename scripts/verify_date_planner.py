"""Real PostgreSQL date planner checks in the disposable database only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import re
from threading import Barrier

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from app.database import database_url
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location, own, other):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Date planner checks require the disposable database.",
    )
    install("0012_date_planner_storage", test_db)
    literal = lambda value: sql.Literal(value).as_string()
    admin(
        f"UPDATE scheduling.employee SET active=true WHERE id={literal(own)};",
        test_db,
    )
    first = date.today() + timedelta(days=90)
    first += timedelta(days=(-first.weekday()) % 7)

    def call(statement, parameters=(), who="employee_a"):
        with psycopg.connect(database_url()) as conn:
            conn.execute(
                "SELECT set_config('scheduling.session_sha256',%s,true)",
                (identities[who]["digest"],),
            )
            return conn.execute(statement, parameters).fetchone()[0]

    def read(who="employee_a", start=first):
        return call("SELECT scheduling.read_my_planner(%s,%s)",
                    (location, start), who)

    def record(offset, revision, cells):
        return {
            "date": (first+timedelta(days=offset)).isoformat(),
            "expected_revision": revision,
            "cells": cells,
        }

    def save(records, who="employee_a", policy=None):
        if policy is None:
            policy = read(who)["profile"]["location_revision"]
        return call(
            "SELECT scheduling.save_my_planner(%s,%s,%s,%s)",
            (location, first, policy, Jsonb(records)), who,
        )

    def denied(operation, code):
        try:
            operation()
        except psycopg.Error as error:
            require(error.sqlstate == code, "Unexpected planner error code.")
        else:
            raise SystemExit("STOP: Invalid planner operation succeeded.")

    state = read()
    require(len(state["days"]) == 7
            and all(row["blocked_cells"] == [] and row["revision"] == 0
                    for row in state["days"]),
            "New date planner did not start blank.")

    state = save([record(0, 0, [95, 0, 1, 1]), record(1, 0, [0, 1])])
    require(state["days"][0]["blocked_cells"] == [0, 1, 95]
            and state["days"][1]["blocked_cells"] == [0, 1],
            "Atomic multi-date save or canonical cells failed.")
    require(read()["days"][0]["revision"] == 1,
            "Saved date revision did not persist.")

    # One stale date must roll back a different otherwise-valid date.
    before = fingerprint(test_db)
    denied(lambda: save([record(2, 0, [20]), record(0, 0, [30])]), "40001")
    require(fingerprint(test_db) == before,
            "Stale date retained partial multi-date changes.")

    state = save([record(0, 1, [])])
    require(state["days"][0]["blocked_cells"] == []
            and state["days"][0]["revision"] == 2
            and state["days"][1]["blocked_cells"] == [0, 1],
            "Clearing one date changed another date or lost its revision.")
    denied(lambda: save([record(0, 1, [4])]), "40001")

    for cells in ([True], [-1], [96], [1.5], ["1"]):
        denied(lambda cells=cells: save([record(2, 0, cells)]), "22023")
    denied(lambda: save([record(2, 0, [1]), record(2, 0, [2])]), "22023")
    denied(lambda: save([record(7, 0, [1])]), "22023")
    denied(lambda: save([record(2, 0, [1])], policy=0), "40001")

    frozen = first + timedelta(days=3)
    admin(
        "INSERT INTO scheduling.unavailability_day_lock("
        "organization_id,location_id,employee_id,local_date,blocked_windows,"
        "reason,actor_user_id) "
        f"SELECT organization_id,id,{literal(own)},"
        f"{literal(frozen.isoformat())}::date,'[[480,540]]'::jsonb,"
        f"'publication',{literal(identities['employee_a']['user_id'])} "
        f"FROM scheduling.location WHERE id={literal(location)};",
        test_db,
    )
    frozen_state = read()["days"][3]
    require(not frozen_state["editable"]
            and frozen_state["frozen_blocked"] == [[480, 540]],
            "Frozen snapshot was not projected.")
    before = fingerprint(test_db)
    denied(lambda: save([record(2, 0, [20]), record(3, 0, [30])]), "55000")
    require(fingerprint(test_db) == before,
            "Frozen date retained partial multi-date changes.")

    old = date.today() - timedelta(days=14)
    denied(lambda: call(
        "SELECT scheduling.save_my_planner(%s,%s,%s,%s)",
        (location, old, read()["profile"]["location_revision"], Jsonb([{
            "date": old.isoformat(), "expected_revision": 0, "cells": [0],
        }])),
    ), "55000")

    barrier = Barrier(2)
    def concurrent(cell):
        barrier.wait(timeout=10)
        try:
            save([record(4, 0, [cell])])
            return "saved"
        except psycopg.Error as error:
            return error.sqlstate

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(concurrent, (30, 40)))
    require(sorted(outcomes) == ["40001", "saved"],
            "Concurrent planner edits overwrote one another.")

    admin("""
        CREATE FUNCTION scheduling.reject_date_planner_audit_test()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.action='unavailability.date_saved' THEN
                RAISE EXCEPTION 'Synthetic planner audit failure';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER reject_date_planner_audit_test
        BEFORE INSERT ON scheduling.audit_event
        FOR EACH ROW EXECUTE FUNCTION
            scheduling.reject_date_planner_audit_test();
    """, test_db)
    before = fingerprint(test_db)
    denied(lambda: save([record(5, 0, [1]), record(6, 0, [2])]), "P0001")
    require(fingerprint(test_db) == before,
            "Audit failure retained planner changes.")
    admin(
        "DROP TRIGGER reject_date_planner_audit_test ON scheduling.audit_event;"
        "DROP FUNCTION scheduling.reject_date_planner_audit_test();",
        test_db,
    )

    peer = read("scheduler_a")
    require(peer["profile"]["employee_id"] == other
            and all(row["blocked_cells"] == [] for row in peer["days"]),
            "Another employee's date blocks leaked.")
    save([record(1, 0, [60])], who="scheduler_a")
    require(read()["days"][1]["blocked_cells"] == [0, 1],
            "Peer write changed another employee's date.")

    denied(lambda: read("admin_b"), "42501")
    denied(lambda: save([record(2, 0, [1])], who="admin_b", policy=1), "42501")
    denied(lambda: call(
        "SELECT count(*) FROM scheduling.unavailability_date"
    ), "42501")

    private_count = call(
        "SELECT count(*) FROM scheduling.audit_event "
        "WHERE action='unavailability.date_saved' AND entity_id=%s",
        (own,), "scheduler_a",
    )
    require(private_count == 0, "Private planner audit leaked to a coworker.")

    admin(
        f"UPDATE scheduling.employee SET active=false WHERE id={literal(own)};",
        test_db,
    )
    denied(lambda: read(), "42501")
    denied(lambda: save([record(2, 0, [1])], policy=1), "42501")

    from scripts.verify_planner_api import verify as verify_api
    verify_api(test_db, identities, location, own, other)

    print("DATE_PLANNER_BLANK_READ_AND_EXACT_CELLS=PASS")
    print("ATOMIC_MULTI_DATE_SAVE_AND_INDEPENDENT_CLEAR=PASS")
    print("DATE_REVISIONS_AND_CONCURRENT_EDITS=PASS")
    print("CUTOFF_FROZEN_DATE_AND_PARTIAL_WRITE_DENIAL=PASS")
    print("PLANNER_AUDIT_FAILURE_ROLLBACK=PASS")
    print("DATE_PLANNER_EMPLOYEE_PRIVACY_AND_INACTIVE_DENIAL=PASS")
