"""Private employee calendar reads in the disposable database only."""

import json
import re
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin


def verify(test_db, identities, location):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Private calendar checks must use the disposable database.",
    )
    install("0010_private_unavailability_storage", test_db)
    literal = lambda value: sql.Literal(value).as_string()
    own, other = str(uuid4()), str(uuid4())
    owner = identities["employee_a"]["user_id"]
    scheduler = identities["scheduler_a"]["user_id"]
    statements = ["BEGIN; SET LOCAL ROLE shift_scheduling_owner;"]

    for employee, user, name in (
        (own, owner, "Private employee"),
        (other, scheduler, "Other private employee"),
    ):
        statements.append(
            "INSERT INTO scheduling.employee("
            "id,organization_id,location_id,user_id,display_name) "
            f"SELECT {literal(employee)},organization_id,id,{literal(user)},"
            f"{literal(name)} FROM scheduling.location WHERE id={literal(location)};"
        )

    def pattern(employee, revision, effective, windows, actor):
        return (
            "INSERT INTO scheduling.unavailability_recurring("
            "organization_id,location_id,employee_id,revision,effective_from,"
            "weekly_windows,actor_user_id) "
            f"SELECT organization_id,id,{literal(employee)},{revision},"
            f"{literal(effective)}::date,{literal(json.dumps(windows))}::jsonb,"
            f"{literal(actor)} FROM scheduling.location WHERE id={literal(location)};"
        )

    statements += [
        pattern(own, 1, "2026-10-26", {
            "0": [{"start": 1200, "end": 120, "ends_next_day": True}]
        }, owner),
        pattern(own, 2, "2026-11-02", {
            "0": [{"start": 600, "end": 660}]
        }, owner),
        pattern(other, 1, "2026-10-26", {
            "0": [{"start": 300, "end": 360}]
        }, scheduler),
    ]
    for day, windows in (
        ("2026-10-27", []),
        ("2026-10-28", [{"start": 0, "end": 1440}]),
    ):
        statements.append(
            "INSERT INTO scheduling.unavailability_exception("
            "organization_id,location_id,employee_id,local_date,revision,windows,"
            "actor_user_id) "
            f"SELECT organization_id,id,{literal(own)},{literal(day)}::date,1,"
            f"{literal(json.dumps(windows))}::jsonb,{literal(owner)} "
            f"FROM scheduling.location WHERE id={literal(location)};"
        )
    statements.append(
        "INSERT INTO scheduling.unavailability_day_lock("
        "organization_id,location_id,employee_id,local_date,blocked_windows,"
        "reason,actor_user_id) "
        f"SELECT organization_id,id,{literal(own)},date '2026-10-29',"
        f"'[[480,540]]'::jsonb,'publication',{literal(owner)} "
        f"FROM scheduling.location WHERE id={literal(location)};"
    )
    statements.append("COMMIT;")
    admin("\n".join(statements), test_db)

    prefix = "/api/v1/me/unavailability"
    url = prefix + "/" + location
    with TestClient(app) as client:
        require(client.get(prefix+"/profiles").status_code == 401,
                "Anonymous employee calendar access.")
        client.cookies.set(COOKIE_NAME, identities["employee_a"]["token"])
        profiles = client.get(prefix+"/profiles")
        require(profiles.status_code == 200
                and [p["employee_id"] for p in profiles.json()] == [own],
                "Employee profiles exposed another employee.")
        response = client.get(url, params={"start": "2026-10-26"})
        require(response.status_code == 200, "Private calendar read failed.")
        require(response.headers.get("cache-control") == "no-store",
                "Private calendar can be cached.")
        data = response.json()
        effective = data["effective_days"]
        require(effective[0]["blocked"] == [[1200, 1440]],
                "Stored overnight block was not resolved.")
        require(effective[1]["blocked"] == []
                and effective[1]["source"] == "exception",
                "Empty date replacement did not clear overnight carry.")
        require(effective[2]["available"] == [], "All-day block was not resolved.")
        require(effective[3]["blocked"] == [[480, 540]]
                and not effective[3]["editable"]
                and effective[3]["lock_reason"] == "published_week",
                "Frozen day did not take precedence.")
        require(effective[4]["available"] == [[0, 1440]],
                "Missing entries did not mean fully available.")
        require(other not in response.text, "Another employee ID leaked.")

        later = client.get(url, params={"start": "2026-11-02"}).json()
        require(later["effective_days"][0]["blocked"] == [[600, 660]],
                "Effective-dated recurring version was not selected.")
        require(client.get(url, params={
            "start": "2026-10-26", "days": 36
        }).status_code == 422, "Unbounded calendar range accepted.")
        require(client.get(prefix+"/"+str(uuid4()), params={
            "start": "2026-10-26"
        }).status_code == 404, "Unlinked location was accessible.")

        admin(pattern(own, 3, "2026-10-26", {
            "3": [{"start": 600, "end": 660}]
        }, owner), test_db)
        frozen = client.get(url, params={"start": "2026-10-29", "days": 1}).json()
        require(frozen["effective_days"][0]["blocked"] == [[480, 540]],
                "Later recurring data changed a frozen day.")

        admin(
            f"UPDATE scheduling.employee SET active=false WHERE id={literal(own)};",
            test_db,
        )
        require(client.get(prefix+"/profiles").json() == [],
                "Inactive employee retained private profile access.")
        require(client.get(url, params={"start": "2026-10-26"}).status_code == 404,
                "Inactive employee retained calendar access.")

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, identities["scheduler_a"]["token"])
        data = client.get(url, params={"start": "2026-10-26"}).json()
        require(data["profile"]["employee_id"] == other
                and data["effective_days"][0]["blocked"] == [[300, 360]],
                "Same-business users shared private unavailability.")
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, identities["admin_a"]["token"])
        require(client.get(prefix+"/profiles").json() == [],
                "Administrator received another employee's personal profiles.")

    try:
        with psycopg.connect(database_url()) as conn:
            conn.execute("SELECT * FROM scheduling.unavailability_recurring")
    except psycopg.Error as error:
        require(error.sqlstate == "42501", "Unexpected raw table denial.")
    else:
        raise SystemExit("STOP: Runtime has broad private table access.")

    with psycopg.connect(database_url()) as conn:
        require(conn.execute(
            "SELECT scheduling.my_unavailability_profiles()"
        ).fetchone()[0] == [], "Missing session context exposed employee profiles.")

    print("PRIVATE_EMPLOYEE_PROFILES_AND_CALENDAR_READS=PASS")
    print("STORED_RECURRENCE_DATE_REPLACEMENTS_AND_OVERNIGHT_RESOLUTION=PASS")
    print("FROZEN_DAY_PRECEDENCE_AND_EFFECTIVE_VERSIONS=PASS")
    print("SAME_BUSINESS_EMPLOYEE_PRIVACY_AND_INACTIVE_PROFILE_DENIAL=PASS")
    print("BROAD_PRIVATE_TABLE_ACCESS=DENIED")
