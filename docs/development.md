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

Application skeleton only. Database, authentication, scheduling and
persistent startup have not been implemented.
