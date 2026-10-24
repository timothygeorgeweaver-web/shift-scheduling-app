"""Current planner certification; legacy routes/functions are retired."""

import re
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Planner certification requires the disposable database.",
    )

    for migration in (
        "0010_private_unavailability_storage",
        "0011_employee_unavailability_writes",
        "0012_date_planner_storage",
        "0013_planner_bulk_scope",
        "0014_retire_legacy_unavailability",
    ):
        install(migration, test_db)

    literal = lambda value: sql.Literal(value).as_string()
    own, other = str(uuid4()), str(uuid4())
    statements = ["BEGIN; SET LOCAL ROLE shift_scheduling_owner;"]

    for employee, label, name in (
        (own, "employee_a", "Planner employee"),
        (other, "scheduler_a", "Other planner employee"),
    ):
        statements.append(
            "INSERT INTO scheduling.employee("
            "id,organization_id,location_id,user_id,display_name) "
            f"SELECT {literal(employee)},organization_id,id,"
            f"{literal(identities[label]['user_id'])},{literal(name)} "
            f"FROM scheduling.location WHERE id={literal(location)};"
        )
    statements.append("COMMIT;")
    admin("\n".join(statements), test_db)

    with TestClient(app) as client:
        require(client.get("/api/v1/me/planner/profiles").status_code == 401,
                "Anonymous planner profiles were exposed.")
        client.cookies.set(COOKIE_NAME, identities["employee_a"]["token"])
        response = client.get("/api/v1/me/planner/profiles")
        require(response.status_code == 200
                and [row["employee_id"] for row in response.json()] == [own],
                "Planner profile list exposed another employee.")

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, identities["admin_a"]["token"])
        require(client.get("/api/v1/me/planner/profiles").json() == [],
                "Administrator inherited another employee's personal profile.")

    # Runs database saves, HTTP saves and additive multi-week certification.
    from scripts.verify_date_planner import verify as verify_planner
    verify_planner(test_db, identities, location, own, other)

    before = fingerprint(test_db)
    token = identities["scheduler_a"]["token"]
    digest = identities["scheduler_a"]["digest"]

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        prefix = "/api/v1/me/unavailability"
        require(client.get(prefix+"/profiles").status_code == 404,
                "Legacy profile HTTP route remains registered.")
        require(client.get(prefix+"/"+location, params={
            "start": "2027-01-04"
        }).status_code == 404, "Legacy calendar HTTP route remains registered.")
        require(client.post(prefix+"/"+location, headers={
            "X-CSRF-Token": _csrf(token)
        }, json={
            "operation": "recurring",
            "date": "2027-01-04",
            "expected_revision": 0,
            "expected_location_revision": 1,
            "weekly_windows": {},
        }).status_code == 404, "Legacy mutation HTTP route remains registered.")

    for statement, parameters in (
        (
            "SELECT scheduling.read_my_unavailability(%s,%s,7)",
            (location, "2027-01-04"),
        ),
        (
            "SELECT scheduling.write_my_unavailability("
            "%s,'recurring',%s,0,1,'{}'::jsonb)",
            (location, "2027-01-04"),
        ),
    ):
        try:
            with psycopg.connect(database_url()) as conn:
                conn.execute(
                    "SELECT set_config('scheduling.session_sha256',%s,true)",
                    (digest,),
                )
                conn.execute(statement, parameters)
        except psycopg.Error as error:
            require(error.sqlstate == "42501",
                    "Unexpected legacy function denial.")
        else:
            raise SystemExit("STOP: Legacy database function remains executable.")

    with psycopg.connect(database_url()) as conn:
        require(conn.execute(
            "SELECT scheduling.my_unavailability_profiles()"
        ).fetchone()[0] == [],
            "Missing session context exposed employee profiles.")

    require(fingerprint(test_db) == before,
            "Legacy denial checks changed database state.")

    print("CURRENT_PLANNER_PROFILE_AUTHORIZATION=PASS")
    print("LEGACY_HTTP_ROUTES=REMOVED")
    print("LEGACY_DATABASE_READ_AND_WRITE_FUNCTIONS=DENIED")
    print("LEGACY_DENIAL_CHECKS_DATABASE_STATE=UNCHANGED")
