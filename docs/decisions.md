# Project decisions

## Workflow

Timothy runs guarded terminal blocks for edits, tests, commits and pushes.
The assistant inspects GitHub and reviews returned evidence.
Use numbered implementation stages and report approximate progress.

## Rest policy confirmed October 18 2026

Default: a shift ending between 20:00 and 06:00 inclusive in the
location's local time requires 12 elapsed hours before the next shift.
Administrators can adjust both window endpoints and required rest hours.
Individual assignments cannot override the configured rule.
Evaluate the actual end time, including after midnight.
Check both chronological neighbors across schedule-week boundaries.
Audit policy changes and revalidate affected schedules.

This replaces conflicting rest-rule wording in the original specifications.
Boundary inclusion and administrator control are the documented
implementation interpretations and remain subject to owner correction.

## Initial technical implementation

Python with FastAPI and PostgreSQL.
The health endpoint reports liveness; readiness separately checks the database.
Administrators manage accounts and business permissions. Schedulers manage
staff and scheduling but cannot administer sign-ins or account permissions.
Employee profile active status and business sign-in access are separate.
Local development uses port 8010; this is not a production deployment.
