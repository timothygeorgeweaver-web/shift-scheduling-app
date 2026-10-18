"""One-time setup for the Chromebook's local PostgreSQL cluster."""

import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
CLEAN_ENV = {"PATH": os.defpath, "LANG": "C.UTF-8"}


def admin(sql: str, database: str = "postgres") -> str:
    result = subprocess.run(
        [
            "sudo", "-n", "-u", "postgres",
            "psql", "-X", "-w",
            "-h", "/var/run/postgresql", "-p", "5432",
            "-d", database, "-v", "ON_ERROR_STOP=1",
            "-A", "-t", "-q",
        ],
        input=sql,
        text=True,
        capture_output=True,
        env=CLEAN_ENV,
        timeout=30,
    )
    if result.returncode:
        # SQL may contain credentials; do not print raw errors.
        raise SystemExit(
            "STOP: database setup command failed. Partial state may exist. "
            "Keep .env and return this output; do not rerun or delete anything."
        )
    return result.stdout.strip()


def main() -> None:
    if ENV_FILE.exists():
        raise SystemExit("STOP: .env already exists; refusing to overwrite.")

    existing = admin("""
        SELECT
          (SELECT count(*) FROM pg_database
           WHERE datname = 'shift_scheduling')
          +
          (SELECT count(*) FROM pg_roles
           WHERE rolname IN (
             'shift_scheduling_owner', 'shift_scheduling_app'
           ));
    """)
    if existing != "0":
        raise SystemExit("STOP: target database or roles already exist.")

    password = secrets.token_hex(32)
    url = (
        "postgresql://shift_scheduling_app:"
        + password
        + "@127.0.0.1:5432/shift_scheduling"
    )
    fd = os.open(
        ENV_FILE,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(fd, "w") as stream:
        stream.write("SHIFT_SCHEDULING_DATABASE_URL=" + url + "\n")

    print("LOCAL_CREDENTIALS_SAVED_WITHOUT_DISPLAY", flush=True)

    admin(f"""
        BEGIN;
        SET LOCAL password_encryption = 'scram-sha-256';
        CREATE ROLE shift_scheduling_owner
          NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
          NOREPLICATION NOBYPASSRLS;
        CREATE ROLE shift_scheduling_app
          LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
          NOREPLICATION NOBYPASSRLS
          PASSWORD '{password}';
        COMMIT;
    """)
    print("DATABASE_ROLES_CREATED", flush=True)

    admin("""
        CREATE DATABASE shift_scheduling
          OWNER shift_scheduling_owner
          TEMPLATE template0;
    """)
    print("DATABASE_CREATED", flush=True)

    admin("""
        BEGIN;
        REVOKE ALL ON DATABASE shift_scheduling FROM PUBLIC;
        GRANT CONNECT ON DATABASE shift_scheduling
          TO shift_scheduling_app;
        REVOKE ALL ON SCHEMA public FROM PUBLIC;
        CREATE SCHEMA scheduling
          AUTHORIZATION shift_scheduling_owner;
        GRANT USAGE ON SCHEMA scheduling TO shift_scheduling_app;
        COMMIT;
    """, database="shift_scheduling")
    print("EMPTY_SCHEMA_CREATED", flush=True)

    result = subprocess.run(
        [
            "psql", "-X", "-w",
            "-h", "127.0.0.1", "-p", "5432",
            "-U", "shift_scheduling_app",
            "-d", "shift_scheduling",
            "-v", "ON_ERROR_STOP=1", "-A", "-t", "-q",
        ],
        input="""
            BEGIN READ ONLY;
            SELECT
              current_database() = 'shift_scheduling'
              AND current_user = 'shift_scheduling_app'
              AND NOT rolsuper
              AND NOT rolcreatedb
              AND NOT rolcreaterole
              AND NOT rolbypassrls
              AND NOT pg_has_role(
                current_user, 'shift_scheduling_owner', 'MEMBER'
              )
              AND has_schema_privilege(
                current_user, 'scheduling', 'USAGE'
              )
              AND NOT has_schema_privilege(
                current_user, 'scheduling', 'CREATE'
              )
              AND NOT has_schema_privilege(
                current_user, 'public', 'CREATE'
              )
            FROM pg_roles WHERE rolname = current_user;
            COMMIT;
        """,
        text=True,
        capture_output=True,
        env={**CLEAN_ENV, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "5"},
        timeout=15,
    )
    if result.returncode or result.stdout.strip() != "t":
        raise SystemExit(
            "STOP: application login/permissions verification failed. "
            "Database objects and .env remain; return this output."
        )

    if ENV_FILE.stat().st_mode & 0o077:
        raise SystemExit("STOP: .env permissions are too broad.")

    print("APPLICATION_DATABASE_LOGIN=PASS")
    print("APPLICATION_ROLE_PERMISSIONS=PASS")
    print("ENV_FILE_PERMISSIONS=PASS")


if __name__ == "__main__":
    main()
