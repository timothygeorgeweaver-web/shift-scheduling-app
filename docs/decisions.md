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

## Availability defaults approved October 18, 2026

The owner explicitly approved:
- Scheduling weeks begin Monday.
- Employee availability closes at local 17:00 seven calendar days before
  the scheduled week begins: the preceding Monday at 5 p.m.
- Administrators can adjust these settings.
- The pilot uses America/Los_Angeles, following daylight saving changes.

The cutoff is a local calendar calculation, not a fixed 168-hour subtraction.
The exact cutoff instant is closed to employee edits.
Publication locks the week even when its cutoff has not yet arrived.

Configurable settings persistence and administrator controls will be added
with availability. These defaults are approved; that UI is not yet built.

## Unavailability model approved October 18, 2026

Employees record times they cannot work. No entries means fully available.
This supersedes the former proposal that missing availability means unavailable.

Recurring blocks may contain multiple intervals. Overnight blocks require an
explicit ends-next-day selection.

A date-specific exception replaces all blocked portions on that local date,
including carry-in from the previous day's overnight entry.
- Empty replacement: available all day.
- Full-day replacement: unavailable all day.
- Delete the exception: restore the recurring pattern.
An overnight date exception can carry into the following date unless that
following date has its own replacement.

A shift intersecting effective unavailability requires a scheduler warning
override with a reason. No entered blocks alone never creates that warning.
Qualification, overlap, rest and daily-hour rules remain independent.

Cutoff and publication locks still apply. Later recurring edits must not
retroactively alter locked dates. The current V1 Word plan is version 1.2.
