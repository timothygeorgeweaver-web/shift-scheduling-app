# Local PostgreSQL setup

Database: shift_scheduling
Local server: 127.0.0.1 port 5432
Schema: scheduling

shift_scheduling_owner owns database objects and cannot log in.
shift_scheduling_app is the restricted application login.
It has database CONNECT and schema USAGE, without schema CREATE.
Table permissions will be added with the relevant migrations.

## Initial setup

From the project root, with the virtual environment active:

    python scripts/bootstrap_local_database.py

The script uses the existing passwordless sudo permission for
PostgreSQL administration. It stops instead of prompting for a password.

This is a one-time bootstrap, not a migration runner.
It refuses existing target roles, database or .env.
If setup fails partway, preserve .env and inspect the partial state.
Do not blindly rerun, delete objects or regenerate credentials.

## Credentials

The bootstrap creates .env with owner-only permissions.
SHIFT_SCHEDULING_DATABASE_URL contains the application connection URL.
Never commit or paste .env or its contents.

## Current status

Database and schema foundation only; no business tables yet.
Application connectivity is implemented; migrations are pending.


## Connectivity checks

/api/v1/health reports application liveness.
/api/v1/ready checks PostgreSQL using a read-only SELECT.
Readiness currently verifies connectivity, not business tables or migrations.
A database failure returns HTTP 503 with a generic message.

The application reads SHIFT_SCHEDULING_DATABASE_URL from the process
environment first, then the project's .env file.
Database passwords and raw driver errors are excluded from API responses.
