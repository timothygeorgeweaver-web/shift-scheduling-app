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

## Schedule index browser implementation

The Schedules section appears for administrator/scheduler accounts and uses
server-authorized location lists. It supports a bounded 90-day list range,
manual refresh, configured week-start/timezone guidance, and explicit blank
draft creation using the current location revision and CSRF token.

Successful creation reloads the list around the new draft. Duplicate,
stale-setting and uncertain outcomes require a list refresh before retrying.
The UI does not expose client-controlled publication state.

Employee-only accounts do not display the schedule index. Backend staff
authorization remains authoritative. Sign-out removes the schedule section;
late responses from a previous account are ignored.

Week selection and shift editing will be added with the draft builder.
Browser verification of this index and creation form remains pending.
