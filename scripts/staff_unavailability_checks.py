from datetime import date
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from app.database import database_url
from app.security import session_token_digest
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin
from scripts.publication_commit_checks import literal


def verify(database, token, location, role):
    employee = str(uuid4())
    admin(f"""
        INSERT INTO scheduling.employee(
            id,organization_id,location_id,display_name
        )
        SELECT {literal(employee)},organization_id,id,'Unlinked mock employee'
        FROM scheduling.location WHERE id={literal(location)};
    """, database)
    first = date(2096, 1, 2)

    def connect():
        conn = psycopg.connect(database_url())
        conn.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)",
            (session_token_digest(token),),
        )
        return conn

    with connect() as conn:
        loaded = conn.execute(
            "SELECT scheduling.read_staff_planner(%s,%s,%s)",
            (location, employee, first),
        ).fetchone()[0]
        require(loaded["profile"]["employee_id"] == employee,
                "Staff could not read an unlinked employee planner.")
        revision = loaded["profile"]["location_revision"]
        result = conn.execute(
            "SELECT scheduling.save_staff_planner(%s,%s,%s,%s,%s)",
            (location, employee, first, revision, Jsonb([
                {"date": first.isoformat(), "expected_revision": 0, "cells": [40, 41]}
            ])),
        ).fetchone()[0]
        require(result["days"][0]["blocked_cells"] == [40, 41],
                "Staff planner cells did not save.")

    with connect() as conn:
        saved = conn.execute(
            "SELECT scheduling.read_staff_planner(%s,%s,%s)",
            (location, employee, first),
        ).fetchone()[0]
        require(saved["days"][0]["revision"] == 1,
                "Staff planner did not persist.")
        try:
            with conn.transaction():
                conn.execute(
                    "SELECT scheduling.save_staff_planner(%s,%s,%s,%s,%s)",
                    (location, employee, first, revision, Jsonb([
                        {"date": first.isoformat(), "expected_revision": 0, "cells": []}
                    ])),
                )
        except psycopg.errors.SerializationFailure:
            pass
        else:
            raise SystemExit("STOP: stale staff planner save was accepted.")

        try:
            with conn.transaction():
                conn.execute(
                    "SELECT scheduling.read_staff_planner(%s,%s,%s)",
                    (location, str(uuid4()), first),
                )
        except psycopg.errors.InsufficientPrivilege:
            pass
        else:
            raise SystemExit("STOP: unknown employee planner was readable.")

        locked = conn.execute(
            "SELECT scheduling.read_staff_planner(%s,%s,%s)",
            (location, employee, date(2000, 1, 3)),
        ).fetchone()[0]
        require(not locked["days"][0]["editable"],
                "Staff planner bypassed a past-date lock.")

        try:
            with conn.transaction():
                conn.execute(
                    "SELECT scheduling.save_staff_planner(%s,%s,%s,%s,%s)",
                    (location, employee, date(2000, 1, 3), revision, Jsonb([
                        {"date": "2000-01-03", "expected_revision": 0, "cells": [40]}
                    ])),
                )
        except psycopg.errors.ObjectNotInPrerequisiteState:
            pass
        else:
            raise SystemExit("STOP: staff save bypassed the date lock.")

    require(admin(f"""
        SELECT count(*)=1 FROM scheduling.audit_event
        WHERE action='unavailability.staff_date_saved'
          AND entity_id={literal(employee)};
    """, database) == "t", "Staff availability audit was not retained.")

    with connect() as conn:
        conn.execute(
            "SELECT set_config('scheduling.session_sha256','invalid',true)"
        )
        try:
            with conn.transaction():
                conn.execute(
                    "SELECT scheduling.read_staff_planner(%s,%s,%s)",
                    (location, employee, first),
                )
        except psycopg.errors.InsufficientPrivilege:
            pass
        else:
            raise SystemExit("STOP: invalid session could read staff availability.")

    print("STAFF_UNLINKED_EMPLOYEE_READ_SAVE_AND_PERSISTENCE=PASS")
    print("STALE_DATE_AND_UNKNOWN_EMPLOYEE_GUARDS=PASS")
    print("CUTOFF_LOCK_AND_AUDIT_ACTOR_PATH=PASS")
    print("INVALID_SESSION_PRIVATE_PLANNER_ACCESS=DENIED")
