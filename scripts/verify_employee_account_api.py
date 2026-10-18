"""Real account API checks inside the disposable database harness."""

from pathlib import Path
import re
import secrets
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME
from app.database import database_url
from app.main import app
from scripts.create_recovery_checkpoint import admin, fingerprint


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def verify(test_db, identities, location, audit_employee):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Account API checks must use the disposable database.",
    )

    def literal(value):
        return sql.Literal(value).as_string()

    employee = str(uuid4())
    other = str(uuid4())
    for employee_id in (employee, other):
        admin(
            "INSERT INTO scheduling.employee("
            "id,organization_id,location_id,display_name) "
            f"SELECT {literal(employee_id)},organization_id,id,"
            "'Account API fixture' FROM scheduling.location "
            f"WHERE id={literal(location)};",
            test_db,
        )

    prefix = "/api/v1/staff/locations/" + location + "/employees/"
    url = prefix + employee + "/account"
    password = secrets.token_urlsafe(24)
    email = uuid4().hex + "@example.invalid"
    body = {"email": email, "password": password, "expected_revision": 1}

    with TestClient(app) as client:
        require(client.post(url, json=body).status_code == 401,
                "Anonymous account creation was accepted.")

    for label, expected in (
        ("scheduler_a", 403), ("employee_a", 403), ("admin_b", 404)
    ):
        with TestClient(app) as client:
            client.cookies.set(COOKIE_NAME, identities[label]["token"])
            me = client.get("/api/v1/auth/me")
            require(me.status_code == 200, "Fixture identity failed.")
            response = client.post(
                url, json=body,
                headers={"X-CSRF-Token": me.json()["csrf_token"]},
            )
            require(response.status_code == expected,
                    "Account API authorization boundary failed.")

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, identities["admin_a"]["token"])
        me = client.get("/api/v1/auth/me")
        require(me.status_code == 200, "Administrator identity failed.")
        headers = {"X-CSRF-Token": me.json()["csrf_token"]}

        require(client.post(url, json=body).status_code == 403,
                "Account API accepted missing CSRF.")
        require(client.post(
            url, json=body,
            headers={**headers, "Origin": "https://example.invalid"},
        ).status_code == 403, "Account API accepted a foreign origin.")

        for change in (
            {"password": "short"},
            {"password": "x" * 1025},
            {"email": "invalid"},
            {"expected_revision": True},
            {"permission": "administrator"},
        ):
            response = client.post(url, json={**body, **change}, headers=headers)
            require(response.status_code == 422, "Invalid account input accepted.")
            require(
                response.json() == {"detail": "Invalid request."},
                "Account validation response exposed submitted values.",
            )

        require(client.post(
            url, json={**body, "expected_revision": 999}, headers=headers,
        ).status_code == 409, "Stale account API request was accepted.")

        created = client.post(url, json=body, headers=headers)
        require(created.status_code == 201, "Account API creation failed.")
        require(
            set(created.json()) == {"employee_id", "user_id", "email", "revision"}
            and created.json()["employee_id"] == employee
            and created.json()["email"] == email,
            "Unexpected account API response fields.",
        )
        require(created.headers.get("cache-control") == "no-store",
                "Account response can be cached.")

        records = client.get(prefix.rstrip("/")).json()
        record = next(row for row in records if row["id"] == employee)
        require(record["has_account"] is True and record["revision"] == 2,
                "Linked account status was not returned to Team.")

        before = fingerprint(test_db)
        require(client.post(
            url, json={**body, "expected_revision": 2}, headers=headers,
        ).status_code == 409, "Already-linked account API request accepted.")
        require(client.post(
            prefix + other + "/account", json=body, headers=headers,
        ).status_code == 409, "Duplicate email account API request accepted.")
        require(fingerprint(test_db) == before,
                "Rejected account API calls changed data.")

        before = fingerprint(test_db)
        response = client.post(
            prefix + audit_employee + "/account",
            json={**body, "email": uuid4().hex + "@example.invalid"},
            headers=headers,
        )
        require(response.status_code == 503, "Audit failure API path failed.")
        require(fingerprint(test_db) == before,
                "Failed account API call left partial changes.")

    with TestClient(app) as employee_client:
        signed_in = employee_client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        require(signed_in.status_code == 200, "API-created employee sign-in failed.")
        me = employee_client.get("/api/v1/auth/me")
        require(
            me.status_code == 200
            and me.json()["memberships"][0]["permissions"] == ["employee"],
            "API-created employee received incorrect permissions.",
        )
        require(employee_client.get(
            "/api/v1/staff/locations"
        ).status_code == 403, "New employee accessed staff management.")
        require(employee_client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": me.json()["csrf_token"]},
        ).status_code == 204, "API-created employee sign-out failed.")
        require(employee_client.get(
            "/api/v1/auth/me"
        ).status_code == 401, "Employee session survived sign-out.")

    from scripts.verify_account_management import verify as verify_management
    verify_management(test_db, identities, location, employee, email, password)

    print("REAL_EMPLOYEE_ACCOUNT_API=PASS")
    print("ACCOUNT_API_CSRF_VALIDATION_AND_BUSINESS_ISOLATION=PASS")
    print("ACCOUNT_API_ATOMIC_ROLLBACK_AND_CONFLICTS=PASS")
    print("API_CREATED_EMPLOYEE_SIGN_IN_ACCESS_AND_SIGN_OUT=PASS")
