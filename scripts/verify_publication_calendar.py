"""Focused business-week HTTP and database checks in a disposable clone."""

import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from fastapi.testclient import TestClient

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from app.security import new_session_token, session_token_digest
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin


def literal(value):
    return sql.Literal(value).as_string()


def main():
    source = conninfo_to_dict(database_url())
    require(
        source.get("dbname") == "shift_scheduling"
        and source.get("user") == "shift_scheduling_app"
        and source.get("host") == "127.0.0.1",
        "Unexpected application database.",
    )
    require(admin(
        "SELECT count(*) FROM pg_stat_activity WHERE datname='shift_scheduling';"
    ) == "0", "Stop the scheduling server before this check.")

    test_db = "shift_sched_test_" + uuid4().hex
    print("TARGETED_TEST_DATABASE=" + test_db, flush=True)
    admin(
        f'CREATE DATABASE "{test_db}" TEMPLATE shift_scheduling '
        'OWNER shift_scheduling_owner;'
    )
    admin(
        f'REVOKE ALL ON DATABASE "{test_db}" FROM PUBLIC;'
        f'GRANT CONNECT ON DATABASE "{test_db}" TO shift_scheduling_app;'
        f'ALTER DATABASE "{test_db}" SET default_transaction_read_only=off;'
    )
    install("0053_schedule_publication_status", test_db, rehearse=True)
    dsn = make_conninfo(**{**source, "dbname": test_db})
    organization, location, user, role = [str(uuid4()) for _ in range(4)]
    token = new_session_token()
    digest = session_token_digest(token)

    admin(f"""
        BEGIN;
        SET LOCAL ROLE shift_scheduling_owner;
        INSERT INTO scheduling.organization(id,name)
        VALUES ({literal(organization)},'Business-week fixture');
        INSERT INTO scheduling.location(id,organization_id,name,timezone)
        VALUES ({literal(location)},{literal(organization)},
                'Boundary fixture','America/Los_Angeles');
        INSERT INTO scheduling.app_user(id,email)
        VALUES ({literal(user)},{literal(user+'@example.invalid')});
        INSERT INTO scheduling.organization_member(organization_id,user_id)
        VALUES ({literal(organization)},{literal(user)});
        INSERT INTO scheduling.member_permission(organization_id,user_id,permission)
        VALUES ({literal(organization)},{literal(user)},'administrator');
        INSERT INTO scheduling.auth_session(token_sha256,user_id,expires_at)
        VALUES ({literal(digest)},{literal(user)},now()+interval '1 hour');
        INSERT INTO scheduling.job_role(id,organization_id,location_id,name)
        VALUES ({literal(role)},{literal(organization)},{literal(location)},'Cook');
        COMMIT;
    """, test_db)

    employee = str(uuid4())
    admin(
        "BEGIN; SET LOCAL ROLE shift_scheduling_owner;"
        "INSERT INTO scheduling.employee("
        "id,organization_id,location_id,display_name,user_id) VALUES "
        f"({literal(employee)},{literal(organization)},{literal(location)},"
        f"'Business planner fixture',{literal(user)}); COMMIT;",
        test_db,
    )

    previous = os.environ.get("SHIFT_SCHEDULING_DATABASE_URL")
    os.environ["SHIFT_SCHEDULING_DATABASE_URL"] = dsn
    try:
        base = "/api/v1/staff/locations/"+location+"/schedule-weeks"
        headers = {"X-CSRF-Token": _csrf(token)}
        from scripts.publication_calendar_checks import verify
        verify(test_db, token, location, role)
    finally:
        if previous is None:
            os.environ.pop("SHIFT_SCHEDULING_DATABASE_URL", None)
        else:
            os.environ["SHIFT_SCHEDULING_DATABASE_URL"] = previous

    admin(f'DROP DATABASE "{test_db}";')
    print("TEMPORARY_DATABASE=REMOVED")
    print("SOURCE_BUSINESS_RECORDS=NOT_WRITTEN")


if __name__ == "__main__":
    main()
