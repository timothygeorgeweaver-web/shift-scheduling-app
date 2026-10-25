"""Read-only candidate HTTP certification in a disposable database."""

from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql

from app.auth_routes import COOKIE_NAME
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin


def literal(value):
    return sql.Literal(value).as_string()


def verify(database, token, location, role):
    week, slot = str(uuid4()), str(uuid4())
    admin(f"""
        BEGIN;
        SET LOCAL ROLE shift_scheduling_owner;
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
        SELECT {literal(slot)},w.organization_id,w.location_id,w.id,
               {literal(role)},timestamptz '2096-01-02 18:00:00+00',
               timestamptz '2096-01-03 02:00:00+00',w.created_by
        FROM scheduling.schedule_week w WHERE w.id={literal(week)};
        COMMIT;
    """, database)


    import psycopg
    from app.database import database_url
    from app.security import session_token_digest

    employee = admin(
        "SELECT id FROM scheduling.employee WHERE location_id=" +
        literal(location) + ";", database,
    )
    digest = session_token_digest(token)
    statement = (
        "SELECT scheduling.lock_assignment_scope(%s,%s,%s,%s,%s)"
    )
    parameters = (slot, employee, 1, 1, 1)

    def context(conn):
        conn.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)",
            (digest,),
        )

    with psycopg.connect(database_url()) as first:
        context(first)
        before = admin(
            "SELECT count(*) FROM scheduling.audit_event;", database,
        )
        inputs = first.execute(statement, parameters).fetchone()[0]
        require(inputs["employee"]["id"] == employee
                and inputs["slot"]["id"] == slot,
                "Locked assignment input projection failed.")

        with psycopg.connect(database_url()) as second:
            context(second)
            second.execute("SET LOCAL lock_timeout='150ms'")
            try:
                second.execute(statement, parameters)
            except psycopg.Error as error:
                require(error.sqlstate == "55P03",
                        "Unexpected assignment-lock conflict.")
                second.rollback()
            else:
                raise SystemExit("STOP: concurrent assignment scopes did not serialize.")

            first.rollback()
            context(second)
            result = second.execute(statement, parameters).fetchone()[0]
            require(result["slot"]["id"] == slot,
                    "Assignment scope remained locked after rollback.")
            second.rollback()

        context(first)
        try:
            first.execute(statement, (slot, employee, 1, 1, 0))
        except psycopg.Error as error:
            require(error.sqlstate == "40001",
                    "Unexpected stale-scope SQLSTATE.")
            first.rollback()
        else:
            raise SystemExit("STOP: stale policy revision accepted.")

        first.execute(
            "SELECT set_config('scheduling.session_sha256','invalid',true)"
        )
        try:
            first.execute(statement, parameters)
        except psycopg.Error as error:
            require(error.sqlstate == "42501",
                    "Unexpected unauthorized-scope SQLSTATE.")
            first.rollback()
        else:
            raise SystemExit("STOP: unauthorized assignment scope accepted.")

        require(admin(
            "SELECT count(*) FROM scheduling.audit_event;", database,
        ) == before, "Lock-only operation wrote audit history.")

    require(admin("""
        SELECT (SELECT count(*) FROM scheduling.shift_assignment)=0
           AND (SELECT count(*) FROM scheduling.assignment_override)=0;
    """, database) == "t", "Lock certification created assignments.")

    print("ASSIGNMENT_SCOPE_AUTHORIZATION_AND_FRESH_INPUTS=PASS")
    print("TWO_CONNECTION_ASSIGNMENT_SCOPE_SERIALIZATION=PASS")
    print("ROLLBACK_RELEASES_LOCKS_AND_STALE_POLICY_IS_DENIED=PASS")
    print("LOCK_SCOPE_ASSIGNMENT_AND_AUDIT_WRITES=NONE")
