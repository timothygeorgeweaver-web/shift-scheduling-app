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
    install("0026_remove_draft_schedule_weeks", test_db, rehearse=True)
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

    previous = os.environ.get("SHIFT_SCHEDULING_DATABASE_URL")
    os.environ["SHIFT_SCHEDULING_DATABASE_URL"] = dsn
    try:
        base = "/api/v1/staff/locations/"+location+"/schedule-weeks"
        headers = {"X-CSRF-Token": _csrf(token)}
        with TestClient(app) as client:
            client.cookies.set(COOKIE_NAME, token)
            response = client.post(base, headers=headers, json={
                "week_start_date": "2027-01-04", "expected_location_revision": 1,
            })
            require(response.status_code == 201, "Fixture week failed.")
            week = response.json()
            url = base+"/"+week["id"]
            response = client.post(url+"/shifts", headers=headers, json={
                "role_id": role, "local_date": "2027-01-04",
                "start_minute": 540, "end_minute": 1020,
                "expected_week_revision": week["revision"],
            })
            require(response.status_code == 201, "Fixture shift failed.")
            saved = response.json()
            revision = saved["week"]["revision"]
            payload = {"expected_week_revision": revision}

            require(client.post(url+"/remove", json=payload).status_code == 403,
                    "Week removal accepted missing CSRF.")
            require(client.post(url+"/remove", headers=headers, json={
                "expected_week_revision": revision-1,
            }).status_code == 409, "Stale week removal accepted.")

            admin("""
                CREATE FUNCTION scheduling.reject_week_delete_test()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.action='schedule.week_deleted' THEN
                        RAISE EXCEPTION 'Synthetic removal audit failure';
                    END IF;
                    RETURN NEW;
                END $$;
                CREATE TRIGGER reject_week_delete_test
                BEFORE INSERT ON scheduling.audit_event
                FOR EACH ROW EXECUTE FUNCTION scheduling.reject_week_delete_test();
            """, test_db)
            require(client.post(url+"/remove", headers=headers, json=payload)
                    .status_code == 503, "Removal audit failure not exercised.")
            require(client.get(url).json() == saved,
                    "Failed deletion changed the week or its shifts.")
            admin(
                "DROP TRIGGER reject_week_delete_test ON scheduling.audit_event;"
                "DROP FUNCTION scheduling.reject_week_delete_test();", test_db,
            )

            response = client.post(url+"/remove", headers=headers, json=payload)
            require(response.status_code == 200 and response.json()["deleted"],
                    "Whole-week removal failed.")
            require(client.get(url).status_code == 404,
                    "Deleted week remains accessible.")
            listing = client.get(base, params={
                "start": "2027-01-01", "end": "2027-01-31",
            }).json()
            require(listing["weeks"] == [], "Deleted week remains listed.")
            require(client.post(url+"/remove", headers=headers, json=payload)
                    .status_code == 404, "Repeated deletion accepted.")

            recreated = client.post(base, headers=headers, json={
                "week_start_date": "2027-01-04", "expected_location_revision": 1,
            })
            require(recreated.status_code == 201
                    and recreated.json()["id"] != week["id"],
                    "Deleted Monday could not be recreated.")
            require(client.get(base+"/"+recreated.json()["id"]).json()["slots"] == [],
                    "Fresh week inherited deleted shifts.")
            require(admin(
                "SELECT count(*) FROM scheduling.shift_slot "
                f"WHERE schedule_week_id='{week['id']}' AND NOT active;",
                test_db,
            ) == "1", "Removed shift history was not retained.")

            # A non-draft week cannot be removed through this operation.
            new_id = recreated.json()["id"]
            admin(
                "BEGIN; SET LOCAL ROLE shift_scheduling_owner;"
                "UPDATE scheduling.schedule_week SET state='published' "
                f"WHERE id='{new_id}'; COMMIT;", test_db,
            )
            require(client.post(base+"/"+new_id+"/remove",
                headers=headers, json={"expected_week_revision": 1})
                .status_code == 423, "Non-draft deletion accepted.")

        print("WHOLE_DRAFT_WEEK_REMOVAL_AND_FRESH_RECREATION=PASS")
        print("REMOVAL_CSRF_STALE_AND_NON_DRAFT_GUARDS=PASS")
        print("WEEK_SHIFT_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
        print("REMOVED_WEEK_AND_SHIFT_HISTORY=PRESERVED")
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
