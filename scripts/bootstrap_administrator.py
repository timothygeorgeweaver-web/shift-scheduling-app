"""Interactive first-administrator setup and real authentication checks."""

import argparse
import getpass
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4
import warnings
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from psycopg import sql
from fastapi.testclient import TestClient

from app.main import app
from app.auth_routes import COOKIE_NAME
from app.security import hash_password, session_token_digest


def admin(statement: str) -> str:
    result = subprocess.run(
        [
            "sudo", "-n", "-u", "postgres", "psql", "-X", "-w",
            "-h", "/var/run/postgresql", "-p", "5432",
            "-d", "shift_scheduling", "-v", "ON_ERROR_STOP=1",
            "-A", "-t", "-q",
        ],
        input=statement,
        text=True,
        capture_output=True,
        env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        timeout=30,
    )
    if result.returncode:
        raise SystemExit(
            "STOP: administrator database operation failed. "
            "Preserve current state and return this output. "
            "Raw SQL errors are hidden because they may contain credentials."
        )
    return result.stdout.strip()


def literal(value: str) -> str:
    return sql.Literal(value).as_string()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("STOP: " + message)


def private_password() -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass("App password (hidden): ")
    except getpass.GetPassWarning:
        raise SystemExit("STOP: hidden password entry is unavailable.")


def verify(email: str, password: str, expected_user_id: str | None) -> None:
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        require(login.status_code == 200, "Real sign-in failed.")

        token = client.cookies.get(COOKIE_NAME)
        require(bool(token), "Sign-in did not set the session cookie.")

        me = client.get("/api/v1/auth/me")
        require(me.status_code == 200, "Authenticated identity lookup failed.")
        identity = me.json()
        require(identity.get("email") == email, "Identity email mismatch.")
        if expected_user_id:
            require(
                identity.get("user_id") == expected_user_id,
                "Identity account mismatch.",
            )
        require(
            any(
                "administrator" in membership["permissions"]
                and "scheduler" in membership["permissions"]
                for membership in identity["memberships"]
            ),
            "Administrator/scheduler permissions were not returned.",
        )
        require(
            "password_hash" not in identity and "password" not in identity,
            "Unexpected private identity fields.",
        )

        denied = client.post("/api/v1/auth/logout")
        require(denied.status_code == 403, "Missing CSRF token was not denied.")
        require(
            client.get("/api/v1/auth/me").status_code == 200,
            "Denied logout unexpectedly invalidated the session.",
        )

        logout = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": identity["csrf_token"]},
        )
        require(logout.status_code == 204, "Real sign-out failed.")
        require(
            client.get("/api/v1/auth/me").status_code == 401,
            "Signed-out browser was not denied.",
        )

        client.cookies.clear()
        client.cookies.set(COOKIE_NAME, token)
        require(
            client.get("/api/v1/auth/me").status_code == 401,
            "Replayed revoked session was accepted.",
        )
        client.cookies.clear()

        second_login = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        require(second_login.status_code == 200, "Second real sign-in failed.")
        second_me = client.get("/api/v1/auth/me")
        require(second_me.status_code == 200, "Second identity lookup failed.")

        second_token = client.cookies.get(COOKIE_NAME)
        digest = session_token_digest(second_token)

        # Expire only this verification session; do not change the account.
        changed = admin(f"""
            BEGIN;
            SET LOCAL ROLE shift_scheduling_owner;
            UPDATE scheduling.auth_session
            SET created_at = now() - interval '2 hours',
                expires_at = now() - interval '1 hour'
            WHERE token_sha256 = {literal(digest)}
              AND revoked_at IS NULL
            RETURNING token_sha256;
            COMMIT;
        """)
        require(changed == digest, "Verification session expiry update failed.")
        require(
            client.get("/api/v1/auth/me").status_code == 401,
            "Expired session was accepted.",
        )

        cleanup = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": second_me.json()["csrf_token"]},
        )
        require(cleanup.status_code == 204, "Verification session cleanup failed.")

    print("REAL_SIGN_IN=PASS")
    print("REAL_IDENTITY_AND_PERMISSIONS=PASS")
    print("REAL_CSRF_ENFORCEMENT=PASS")
    print("REAL_SIGN_OUT=PASS")
    print("REVOKED_SESSION_REPLAY=DENIED")
    print("EXPIRED_SESSION=DENIED")
    print("VERIFICATION_SESSIONS=REVOKED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    require(sys.stdin.isatty(), "Run this script directly in your terminal.")

    expected_user_id = None
    if not args.verify_only:
        state = admin("""
            BEGIN READ ONLY;
            SELECT
                (SELECT count(*) FROM scheduling.schema_migration)
                || ',' ||
                (SELECT count(*) FROM scheduling.organization)
                || ',' ||
                (SELECT count(*) FROM scheduling.location)
                || ',' ||
                (SELECT count(*) FROM scheduling.app_user);
            COMMIT;
        """)
        require(
            state == "3,0,0,0",
            "Initial setup requires three migrations and no existing business/accounts.",
        )

        business = input("Business name [Development Business]: ").strip()
        business = business or "Development Business"
        location = input("Location name [Main location]: ").strip()
        location = location or "Main location"
        timezone = input("Time zone [America/Los_Angeles]: ").strip()
        timezone = timezone or "America/Los_Angeles"

        require(
            len(business) <= 200 and len(location) <= 200,
            "Business and location names must be at most 200 characters.",
        )
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise SystemExit("STOP: unrecognized time zone.")

    email = input("Administrator email: ").strip().lower()
    require(
        len(email) <= 254
        and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is not None,
        "Enter a valid email address.",
    )

    password = private_password()
    if not args.verify_only:
        require(
            15 <= len(password) <= 1024 and not password.isspace(),
            "Use a passphrase of 15 to 1024 characters.",
        )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                confirmation = getpass.getpass("Confirm app password (hidden): ")
        except getpass.GetPassWarning:
            raise SystemExit("STOP: hidden password entry is unavailable.")
        require(password == confirmation, "Passwords did not match.")
        del confirmation

        organization_id = str(uuid4())
        location_id = str(uuid4())
        expected_user_id = str(uuid4())
        stored_hash = hash_password(password)

        admin(f"""
            BEGIN;
            SET LOCAL lock_timeout = '5s';
            SET LOCAL ROLE shift_scheduling_owner;
            LOCK TABLE scheduling.organization,
                scheduling.location, scheduling.app_user IN EXCLUSIVE MODE;
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM scheduling.organization)
                   OR EXISTS (SELECT FROM scheduling.location)
                   OR EXISTS (SELECT FROM scheduling.app_user) THEN
                    RAISE EXCEPTION 'Initial setup was already performed';
                END IF;
            END
            $$;

            INSERT INTO scheduling.organization(id, name)
            VALUES ({literal(organization_id)}, {literal(business)});

            INSERT INTO scheduling.location(
                id, organization_id, name, timezone
            ) VALUES (
                {literal(location_id)}, {literal(organization_id)},
                {literal(location)}, {literal(timezone)}
            );

            INSERT INTO scheduling.app_user(id, email, password_hash)
            VALUES (
                {literal(expected_user_id)}, {literal(email)},
                {literal(stored_hash)}
            );

            INSERT INTO scheduling.organization_member(organization_id, user_id)
            VALUES ({literal(organization_id)}, {literal(expected_user_id)});

            INSERT INTO scheduling.member_permission(
                organization_id, user_id, permission
            ) VALUES
                ({literal(organization_id)}, {literal(expected_user_id)},
                 'administrator'),
                ({literal(organization_id)}, {literal(expected_user_id)},
                 'scheduler');
            COMMIT;
        """)
        del stored_hash
        print("FIRST_ADMINISTRATOR_CREATED")
        print("ORGANIZATION_ID=" + organization_id)
        print("LOCATION_ID=" + location_id)
        print("ADMINISTRATOR_USER_ID=" + expected_user_id)
        print("If verification fails, these records remain. Do not recreate them.")

    verify(email, password, expected_user_id)
    del password


if __name__ == "__main__":
    main()
