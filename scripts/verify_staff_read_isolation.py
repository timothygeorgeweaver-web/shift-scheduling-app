"""Real staff/API isolation checks in a disposable local database copy."""

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

from app.auth_routes import COOKIE_NAME
from app.database import database_url
from app.main import app
from app.security import new_session_token, session_token_digest
from scripts.create_recovery_checkpoint import admin, fingerprint


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def literal(value):
    return sql.Literal(value).as_string()


def main():
    settings = conninfo_to_dict(database_url())
    require(
        settings.get("dbname") == "shift_scheduling"
        and settings.get("host") == "127.0.0.1"
        and settings.get("user") == "shift_scheduling_app",
        "Unexpected application connection target.",
    )
    require(
        admin("SELECT count(*) FROM pg_stat_activity "
              "WHERE datname = 'shift_scheduling';") == "0",
        "Stop the scheduling server before the integration test.",
    )

    before = fingerprint("shift_scheduling")
    test_db = "shift_sched_test_" + uuid4().hex
    print("INTEGRATION_DATABASE=" + test_db, flush=True)

    admin(
        f'CREATE DATABASE "{test_db}" TEMPLATE shift_scheduling '
        'OWNER shift_scheduling_owner;'
    )
    admin(
        f'REVOKE ALL ON DATABASE "{test_db}" FROM PUBLIC;'
        f'GRANT CONNECT ON DATABASE "{test_db}" TO shift_scheduling_app;'
        f'ALTER DATABASE "{test_db}" SET default_transaction_read_only = off;'
    )
    test_dsn = make_conninfo(**{**settings, "dbname": test_db})

    organizations = {key: str(uuid4()) for key in ("a", "b")}
    locations = {key: str(uuid4()) for key in ("a", "b")}
    employees = {key: str(uuid4()) for key in ("a", "b")}
    roles = {key: str(uuid4()) for key in ("a", "b")}

    statements = ["BEGIN;", "SET LOCAL ROLE shift_scheduling_owner;"]
    for key in ("a", "b"):
        statements += [
            "INSERT INTO scheduling.organization(id,name) VALUES "
            f"({literal(organizations[key])},'Isolation fixture {key}');",
            "INSERT INTO scheduling.location(id,organization_id,name,timezone) "
            f"VALUES ({literal(locations[key])},"
            f"{literal(organizations[key])},'Location {key}',"
            "'America/Los_Angeles');",
            "INSERT INTO scheduling.employee("
            "id,organization_id,location_id,display_name) VALUES "
            f"({literal(employees[key])},{literal(organizations[key])},"
            f"{literal(locations[key])},'Employee {key}');",
            "INSERT INTO scheduling.job_role("
            "id,organization_id,location_id,name) VALUES "
            f"({literal(roles[key])},{literal(organizations[key])},"
            f"{literal(locations[key])},'Role {key}');",
            "INSERT INTO scheduling.employee_qualification("
            "organization_id,location_id,employee_id,job_role_id) VALUES "
            f"({literal(organizations[key])},{literal(locations[key])},"
            f"{literal(employees[key])},{literal(roles[key])});",
        ]

    identities = {}
    for label, business, permission in [
        ("admin_a", "a", "administrator"),
        ("scheduler_a", "a", "scheduler"),
        ("employee_a", "a", "employee"),
        ("admin_b", "b", "administrator"),
    ]:
        user_id = str(uuid4())
        token = new_session_token()
        identities[label] = {
            "user_id": user_id,
            "token": token,
            "digest": session_token_digest(token),
            "business": business,
        }
        statements += [
            "INSERT INTO scheduling.app_user(id,email) VALUES "
            f"({literal(user_id)},{literal(user_id + '@example.invalid')});",
            "INSERT INTO scheduling.organization_member(organization_id,user_id) "
            f"VALUES ({literal(organizations[business])},{literal(user_id)});",
            "INSERT INTO scheduling.member_permission("
            "organization_id,user_id,permission) VALUES "
            f"({literal(organizations[business])},{literal(user_id)},"
            f"{literal(permission)});",
            "INSERT INTO scheduling.auth_session("
            "token_sha256,user_id,expires_at) VALUES "
            f"({literal(identities[label]['digest'])},{literal(user_id)},"
            "now() + interval '8 hours');",
        ]
    statements.append("COMMIT;")
    admin("\n".join(statements), test_db)

    previous = os.environ.get("SHIFT_SCHEDULING_DATABASE_URL")
    os.environ["SHIFT_SCHEDULING_DATABASE_URL"] = test_dsn
    try:
        prefix = "/api/v1/staff/locations"
        with TestClient(app) as client:
            require(client.get(prefix).status_code == 401, "Anonymous staff access.")

        for label in ("admin_a", "scheduler_a", "admin_b"):
            identity = identities[label]
            key = identity["business"]
            other = "b" if key == "a" else "a"
            with TestClient(app) as client:
                client.cookies.set(COOKIE_NAME, identity["token"])
                response = client.get(prefix)
                require(response.status_code == 200, label + " location access failed.")
                require(
                    [row["id"] for row in response.json()] == [locations[key]],
                    label + " could see another business.",
                )

                people = client.get(prefix + "/" + locations[key] + "/employees")
                require(people.status_code == 200, "Employee list failed.")
                require(
                    [row["id"] for row in people.json()] == [employees[key]],
                    "Employee isolation failed.",
                )
                jobs = client.get(prefix + "/" + locations[key] + "/roles")
                require(jobs.status_code == 200, "Role list failed.")
                require(
                    [row["id"] for row in jobs.json()] == [roles[key]],
                    "Role isolation failed.",
                )
                qualified = client.get(
                    prefix + "/" + locations[key] + "/qualifications"
                )
                require(qualified.status_code == 200, "Qualification list failed.")
                require(
                    len(qualified.json()) == 1
                    and qualified.json()[0]["employee_id"] == employees[key]
                    and qualified.json()[0]["job_role_id"] == roles[key],
                    "Qualification isolation failed.",
                )

                for resource in ("employees", "roles", "qualifications"):
                    blocked = client.get(
                        prefix + "/" + locations[other] + "/" + resource
                    )
                    require(blocked.status_code == 404, "Cross-business ID access.")

        with TestClient(app) as client:
            client.cookies.set(COOKIE_NAME, identities["employee_a"]["token"])
            require(
                client.get(prefix).status_code == 403,
                "Employee-only account accessed staff management.",
            )

        # Broad SQL queries must also obey the policies, without API filters.
        with psycopg.connect(test_dsn) as conn:
            conn.read_only = True
            with conn.transaction():
                conn.execute(
                    "SELECT set_config('scheduling.session_sha256', %s, true)",
                    (identities["admin_a"]["digest"],),
                )
                actual = conn.execute(
                    "SELECT id FROM scheduling.employee ORDER BY id"
                ).fetchall()
                require(
                    [str(row[0]) for row in actual] == [employees["a"]],
                    "Database row security failed.",
                )
            require(
                conn.execute(
                    "SELECT count(*) FROM scheduling.employee"
                ).fetchone()[0] == 0,
                "Session context leaked beyond its transaction.",
            )

        # These mutations affect only synthetic identities in the copy.
        admin(f"""
            BEGIN;
            SET LOCAL ROLE shift_scheduling_owner;
            UPDATE scheduling.auth_session
            SET created_at = now() - interval '2 hours',
                expires_at = now() - interval '1 hour'
            WHERE token_sha256 = {literal(identities['admin_a']['digest'])};
            UPDATE scheduling.auth_session SET revoked_at = now()
            WHERE token_sha256 = {literal(identities['scheduler_a']['digest'])};
            UPDATE scheduling.app_user SET active = false
            WHERE id = {literal(identities['admin_b']['user_id'])};
            UPDATE scheduling.organization_member SET active = false
            WHERE user_id = {literal(identities['employee_a']['user_id'])};
            COMMIT;
        """, test_db)

        for label in identities:
            with TestClient(app) as client:
                client.cookies.set(COOKIE_NAME, identities[label]["token"])
                require(
                    client.get(prefix).status_code == 401,
                    "Expired, revoked or deactivated identity accepted: " + label,
                )
    finally:
        if previous is None:
            os.environ.pop("SHIFT_SCHEDULING_DATABASE_URL", None)
        else:
            os.environ["SHIFT_SCHEDULING_DATABASE_URL"] = previous

    require(
        fingerprint("shift_scheduling") == before,
        "Original application data/schema changed during integration testing.",
    )

    # Drop only the uniquely named database created by this invocation.
    admin(f'DROP DATABASE "{test_db}";')

    print("REAL_STAFF_READ_APIS=PASS")
    print("ADMINISTRATOR_AND_SCHEDULER_READS=PASS")
    print("EMPLOYEE_ONLY_STAFF_ACCESS=DENIED")
    print("CROSS_BUSINESS_ID_ACCESS=DENIED")
    print("DATABASE_ROW_SECURITY=PASS")
    print("TRANSACTION_CONTEXT_RESET=PASS")
    print("EXPIRED_REVOKED_AND_DEACTIVATED_ACCESS=DENIED")
    print("SOURCE_DATABASE_UNCHANGED=PASS")
    print("TEMPORARY_DATABASE=REMOVED")


if __name__ == "__main__":
    main()
