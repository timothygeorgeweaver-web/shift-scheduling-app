"""Proposed input reads must preserve stored shifts and assignment history."""

from uuid import uuid4

import psycopg

from app.database import database_url
from app.security import session_token_digest
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin
from scripts.assignment_scope_checks import literal


def verify(database, token, location, role):
    week, slot, assignment, new_role = [str(uuid4()) for _ in range(4)]
    admin(f"""
        BEGIN;
        SET LOCAL ROLE shift_scheduling_owner;
        INSERT INTO scheduling.job_role(id,organization_id,location_id,name)
        SELECT {literal(new_role)},organization_id,id,'Proposed role'
        FROM scheduling.location WHERE id={literal(location)};

        INSERT INTO scheduling.schedule_week(
            id,organization_id,location_id,week_start_date,
            week_start_day,timezone,created_by,business_day_start_minute
        )
        SELECT {literal(week)},l.organization_id,l.id,date '2096-01-02',
               0,l.timezone,e.user_id,l.business_day_start_minute
        FROM scheduling.location l
        JOIN scheduling.employee e ON e.location_id=l.id
        WHERE l.id={literal(location)} AND e.user_id IS NOT NULL;

        INSERT INTO scheduling.shift_slot(
            id,organization_id,location_id,schedule_week_id,
            role_id,start_at,end_at,created_by
        )
        SELECT {literal(slot)},organization_id,location_id,id,
               {literal(role)},timestamptz '2096-01-02 18:00+00',
               timestamptz '2096-01-03 02:00+00',created_by
        FROM scheduling.schedule_week WHERE id={literal(week)};

        INSERT INTO scheduling.shift_assignment(
            id,organization_id,location_id,shift_slot_id,employee_id,
            created_by,updated_by
        )
        SELECT {literal(assignment)},s.organization_id,s.location_id,s.id,e.id,
               s.created_by,s.created_by
        FROM scheduling.shift_slot s
        JOIN scheduling.employee e ON e.location_id=s.location_id
        WHERE s.id={literal(slot)} AND e.user_id IS NOT NULL;
        COMMIT;
    """, database)

    employee = admin(
        "SELECT employee_id FROM scheduling.shift_assignment WHERE id=" +
        literal(assignment), database,
    )
    counts = admin("""
        SELECT json_build_array(
            (SELECT count(*) FROM scheduling.audit_event),
            (SELECT count(*) FROM scheduling.assignment_review_event),
            (SELECT count(*) FROM scheduling.shift_assignment)
        );
    """, database)
    statement = (
        "SELECT scheduling.read_assigned_shift_proposal("
        "%s,%s,%s,%s,%s,%s,%s,%s)"
    )
    parameters = [
        slot, employee, 1, 1, 1, new_role,
        "2096-01-03T07:00:00+00:00",
        "2096-01-03T11:00:00+00:00",
    ]

    with psycopg.connect(database_url()) as conn:
        def context():
            conn.execute(
                "SELECT set_config('scheduling.session_sha256',%s,true)",
                (session_token_digest(token),),
            )

        context()
        result = conn.execute(statement, parameters).fetchone()[0]
        require(result["slot"]["role_id"] == new_role,
                "Proposed role was not used.")
        require([row["date"] for row in result["calendar_days"]] ==
                ["2096-01-02", "2096-01-03"],
                "Proposed dates did not receive fresh availability.")
        require(result["qualified"] is False,
                "Old role qualifications were reused.")
        require(result["current_assignment"]["id"] == assignment,
                "Current assignment identity was lost.")
        require(result["assigned"] == [],
                "The edited shift was counted as its own neighbor.")
        conn.rollback()

        for index, value, expected in [
            (4, 0, "40001"),
            (1, str(uuid4()), "42501"),
            (5, str(uuid4()), "42501"),
            (7, parameters[6], "22023"),
        ]:
            context()
            bad = parameters.copy()
            bad[index] = value
            try:
                conn.execute(statement, bad)
            except psycopg.Error as error:
                require(error.sqlstate == expected,
                        "Unexpected proposed-input rejection.")
                conn.rollback()
            else:
                raise SystemExit("STOP: invalid proposed inputs accepted.")

    require(admin("""
        SELECT json_build_array(
            (SELECT count(*) FROM scheduling.audit_event),
            (SELECT count(*) FROM scheduling.assignment_review_event),
            (SELECT count(*) FROM scheduling.shift_assignment)
        );
    """, database) == counts, "Proposed read changed history.")
    require(admin(
        "SELECT role_id=" + literal(role) +
        " AND start_at=timestamptz '2096-01-02 18:00+00'"
        " AND end_at=timestamptz '2096-01-03 02:00+00'"
        " AND revision=1 FROM scheduling.shift_slot WHERE id=" +
        literal(slot), database,
    ) == "t", "Proposed read changed the stored shift.")

    print("PROPOSED_ROLE_AND_NEW_DATE_AVAILABILITY=PASS")
    print("CURRENT_ASSIGNMENT_AND_SELF_EXCLUSION=PASS")
    print("STALE_UNAUTHORIZED_AND_INVALID_PROPOSALS=DENIED")
    print("PROPOSED_READ_SHIFT_AND_HISTORY_WRITES=NONE")
