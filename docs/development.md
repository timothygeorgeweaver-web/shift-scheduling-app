# Local development

Use Python 3.13 and a project-specific virtual environment.

## Setup

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt

requirements.in lists direct dependencies.
requirements.txt records the exact installed versions from initial setup.

## Tests

    python -m pytest -q

## Run manually

    python -m uvicorn app.main:app --host 127.0.0.1 --port 8010

Visit http://127.0.0.1:8010/api/v1/health.
Stop the server with Ctrl+C.
The health endpoint checks application liveness, not database connectivity.

## Current scope

The application includes role-scoped administration, business-day
unavailability, schedule weeks, templates and copying, shift assignment,
policy validation, publication, immutable published revisions, and
employee team-roster access.

Stages 1 through 5 are complete. Stage 6 covers operational readiness,
deployment and pilot acceptance. See stage_6_status.md for remaining gates.

The manual command above starts a local development server. Persistent
startup and hosted deployment are not established by that command.

Run the database integration checks with the application stopped:

    python -W error scripts/verify_staff_read_isolation.py

The integration harness creates and removes a disposable database copy.
Run pytest with warnings treated as errors:

    python -W error -m pytest -q
