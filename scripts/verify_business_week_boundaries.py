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
    install("0024_business_week_shift_boundaries", test_db, rehearse=True)
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

            def week(first):
                response = client.post(base, headers=headers, json={
                    "week_start_date": first,
                    "expected_location_revision": 1,
                })
                require(response.status_code == 201, "Fixture week failed.")
                return response.json()

            saved = week("2027-01-04")
            week_id = saved["id"]
            # Only the synthetic fixture is activated before UI integration.
            admin(
                "BEGIN; SET LOCAL ROLE shift_scheduling_owner;"
                "UPDATE scheduling.schedule_week "
                "SET business_day_start_minute=300 "
                f"WHERE id={literal(week_id)}; COMMIT;", test_db,
            )
            url = base+"/"+week_id
            def body(day, start=240, end=270):
                current = client.get(url).json()["week"]
                return {
                    "role_id": role, "local_date": day,
                    "start_minute": start, "end_minute": end,
                    "ends_next_day": False,
                    "expected_week_revision": current["revision"],
                    "note": "Boundary fixture",
                }

            require(client.post(url+"/shifts", headers=headers,
                json=body("2027-01-04")).status_code == 422,
                "Monday before rollover entered the wrong week.")
            response = client.post(url+"/shifts", headers=headers,
                json=body("2027-01-11"))
            require(response.status_code == 201,
                    "Following Monday before rollover was rejected.")
            shift = response.json()["slots"][0]
            require(shift["local_start"].startswith("2027-01-11T04:00"),
                    "Stored calendar time was shifted.")

            edit = {
                **body("2027-01-11", 300, 360),
                "expected_shift_revision": shift["revision"],
            }
            require(client.patch(url+"/shifts/"+shift["id"],
                headers=headers, json=edit).status_code == 422,
                "Edit at the next week's rollover was accepted.")

            edit = {
                **body("2027-01-11", 255, 285),
                "expected_shift_revision": shift["revision"],
            }
            require(client.patch(url+"/shifts/"+shift["id"],
                headers=headers, json=edit).status_code == 200,
                "Valid business-week shift edit failed.")

            # The database must independently reject the out-of-week start.
            current = client.get(url).json()["week"]
            try:
                with psycopg.connect(dsn) as conn:
                    conn.execute(
                        "SELECT set_config('scheduling.session_sha256',%s,true)",
                        (digest,),
                    )
                    conn.execute(
                        "SELECT scheduling.create_draft_shift("
                        "%s,%s,%s,%s::timestamptz,%s::timestamptz,%s)",
                        (week_id, role, current["revision"],
                         "2027-01-11 05:00:00-08",
                         "2027-01-11 06:00:00-08", ""),
                    )
            except psycopg.Error as error:
                require(error.sqlstate == "22023", "Unexpected boundary denial.")
            else:
                raise SystemExit("STOP: Direct SQL bypassed the week boundary.")

            legacy = week("2027-02-01")
            admin("BEGIN; SET LOCAL ROLE shift_scheduling_owner;"
                  "UPDATE scheduling.schedule_week "
                  "SET business_day_start_minute=0 "
                  f"WHERE id={literal(legacy['id'])}; COMMIT;", test_db)
            legacy["business_day_start_minute"] = 0
            legacy_url = base+"/"+legacy["id"]
            require(legacy["business_day_start_minute"] == 0,
                    "Staged creation changed before UI integration.")
            response = client.post(legacy_url+"/shifts", headers=headers, json={
                "role_id": role, "local_date": "2027-02-01",
                "start_minute": 0, "end_minute": 60,
                "expected_week_revision": legacy["revision"],
            })
            require(response.status_code == 201,
                    "Existing midnight-based week behavior changed.")

        print("SAVED_ROLLOVER_HTTP_CREATE_AND_EDIT=PASS")
        print("FOLLOWING_MONDAY_EARLY_HOURS_AND_EXACT_BOUNDARY=PASS")
        print("DIRECT_DATABASE_BOUNDARY_ENFORCEMENT=PASS")
        print("MIDNIGHT_WEEK_COMPATIBILITY=PASS")
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
