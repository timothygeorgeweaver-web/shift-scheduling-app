# Stage 3 schedule weeks

## Implemented first slice

Migration 0015 adds schedule_week with location, local start date,
week-start setting, timezone, Draft state, revision and creator metadata.
Location settings are captured when the week is created.

Staff APIs:
- GET /api/v1/staff/locations/{location}/schedule-weeks?start=YYYY-MM-DD&end=YYYY-MM-DD
- POST /api/v1/staff/locations/{location}/schedule-weeks

Creation accepts week_start_date and expected_location_revision.
The date must match the location's configured week start. A location cannot
have duplicate or overlapping schedule-week ranges. Concurrent creation
is serialized. Creation and its location audit event commit atomically.
The audit payload includes the newly created schedule-week identifier.

Only live administrators/schedulers can create or list drafts. Employees
cannot inspect draft weeks. Runtime has no direct schedule-table access
and no arbitrary state-change grants.

Production migration creates an empty table and functions. It creates no
real schedules or shifts and changes no employee unavailability.

## Remaining Stage 3 work

- Schedule index and blank-week creation in the browser.
- Role-specific shift slots and draft week grid.
- Explicit unpaid break intervals and overnight/DST validation.
- Reusable shift templates and copying prior-week structure.
- Copies start with unassigned slots and do not alter their source.

Assignment eligibility and commit rules belong to Stage 4.
Publication, employee roster visibility and publication-triggered
availability snapshots belong to Stage 5.
