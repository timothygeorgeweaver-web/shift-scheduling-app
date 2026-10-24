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

## Week-start month picker refinement

The creation form now uses a month calendar. Only the configured week-start
weekday is selectable. Dates whose seven-day range overlaps any existing
schedule are disabled; draft coverage is labeled Draft exists.

Selecting an available date enables the separate Create blank week button.
Successful creation reloads the calendar and disables the covered date.
Coverage queries include the neighboring dates needed for month-boundary
and changed-week-start overlap checks.

A concurrent creation conflict refreshes the calendar instead of leaving
the form in a waiting state. Disabled buttons use a not-allowed cursor;
the schedule section uses a waiting cursor only during an active request.

A future draft-removal operation can make a date selectable again after
refresh. Draft deletion is not introduced by this UI refinement.
Browser verification of the month picker remains pending.

## Month navigation placement

The owner verified valid-date selection, existing-draft blocking, creation,
persistence and clearing of the waiting cursor. The month heading and
previous/next controls now sit directly above the creation calendar.
Buttons explicitly say Previous month and Next month; each moves one month.
Revised JavaScript passed assistant-side syntax checking.

## Shift timing foundation and revised meal estimate

app/shift_timing.py resolves local shift endpoints to UTC, rejects ambiguous
or nonexistent endpoints, and calculates actual elapsed duration.

It returns full duration, an automatic meal estimate of 30 minutes only when
duration exceeds 360 minutes, and estimated working minutes. Shift endpoints
are preserved. Overnight shifts require explicit next-day selection; 24:00
can represent same-day midnight. No manual lunch fields are planned.

Tests cover the six-hour boundary, overnight shifts, DST elapsed duration,
ambiguous/nonexistent local endpoints, and unchanged actual endpoints.

This module is not yet connected to shift storage or browser controls.
Shift-slot persistence and editing are the next Stage 3 slice.

## Role-specific shift storage

Migration 0016 adds shift_slot and controlled read/create functions.
Slots retain role, actual UTC endpoints, note, revision and actor metadata.
Generated values expose elapsed minutes, the approved meal estimate and
estimated working minutes without storing a scheduled lunch interval.

Only live administrators/schedulers can create slots in a Draft week.
The role must be active and belong to the same location. The local start
must belong to the schedule week; the end may be on the following date.
Database checks independently validate time precision, civil-time ordering
and unambiguous local endpoints.

Creating a slot increments its parent week revision. A stale revision,
invalid role, non-Draft state or audit failure leaves the complete operation
unchanged. Multiple identical coverage slots are allowed for multiple workers.
No employee is assigned by shift creation.

The migration creates no real shifts and preserves existing schedules and
unavailability. HTTP shift routes, browser creation/editing, removal controls,
templates and copying remain pending.

## Shift detail and creation HTTP API

GET /api/v1/staff/locations/{location}/schedule-weeks/{week}
returns authorized schedule metadata and active role-specific slots.

POST /api/v1/staff/locations/{location}/schedule-weeks/{week}/shifts
accepts role_id, local_date, start_minute, end_minute, ends_next_day,
expected_week_revision and an optional note. Unknown fields, including
employee assignments or scheduled lunch fields, are rejected.

The API resolves local endpoints using the schedule week's captured timezone.
The controlled database function independently enforces authority, Draft
state, active same-location role, time validity and the expected revision.
Both layers preserve full endpoints and expose the automatic meal estimate.

CSRF/origin checks protect mutations; responses are no-store. Stale changes
return 409, non-Draft writes 423, invalid times/roles 422, and unknown or
inaccessible schedules 404. Employee-only users cannot inspect drafts.

Disposable PostgreSQL HTTP checks cover persistence, role display, automatic
meal estimate, overnight end dates, validation, stale requests, atomic audit
rollback, staff isolation and non-Draft denial.

The browser shift editor and shift change/removal operations remain pending.
This API step changes no production database records.

## Browser shift creation and week overview

Schedule-list entries now offer Open week. The staff-only week view shows
seven day columns, existing role-specific shifts, full local times,
overnight markers, notes and estimated working minutes.

Draft weeks provide an Add shift form with role, one of the week's dates,
start/end times, explicit Ends next day and an optional note. No lunch-entry
fields exist. The server-returned duration and automatic meal estimate appear
on saved shift cards. New slots remain unassigned.

The loaded week revision is sent with creation. Stale or uncertain outcomes
require refresh; invalid input can be corrected. Unsaved shifts prompt before
closing or refreshing. The outer schedule picker is disabled while a week
is open, preventing workplace/month changes from discarding the form.

Sign-out clears the editor, removes its unload handler and ignores late
responses. This browser-only change uses regression and asset checks;
existing PostgreSQL shift API certification remains applicable.
Browser verification remains pending.

## Fifteen-minute shift planner

The draft overview uses seven date columns and 96 starting-time rows.
Each starting cell contains compact shift cards and a separate Add shift
space. Rows expand across all seven days to keep time labels aligned.
Selecting Add shift fills the form's start date and time; saving keeps a
blank Add shift space available for additional coverage at the same time.

Cards group by job role and sort by actual ending instant within the role,
with shift ID breaking exact ties. Alphabetical role order is the current
fallback. Editable role display order remains pending.
Existing shifts starting between quarter-hours remain visible in their
containing quarter-hour cell, with their exact start time displayed.

Shift editing/removal and navigation between open schedule weeks remain
pending. This change does not alter stored shifts or meal calculations.

The signed-in workspace uses the available screen width for seven-day
planners. Narrow screens retain horizontal scrolling.
Shift entry uses validated 24-hour HH:MM text fields, independent of the
browser's regional AM/PM preference.

## Compact cards and page scrolling

Shift cards display only their job role name. Their full stored timing,
notes and meal estimates remain unchanged, and finish-time sorting remains
in effect. Editing and deletion controls are pending.

The schedule grid expands to its full height and uses page scrolling.
The Add shift form appears above the grid when a starting cell is selected.
The day headings remain sticky while scrolling through the schedule.

## Controlled draft shift edits and removal

PATCH on a shift accepts its full role/date/time/note values plus expected
week and shift revisions. POST to its /remove endpoint accepts both
revisions and deactivates the shift while preserving its stored history.

Both operations require live staff authorization and CSRF protection.
They lock the organization, week and target shift, reject non-draft weeks,
and commit shift, week revision and audit changes in one transaction.
Edits retain the shift ID and independently validate local endpoints using
the same database rules as creation. Removal remains possible for shifts
whose role has since become inactive.

Disposable PostgreSQL checks cover persistence, recalculated meal estimates,
overnight edits, concurrent/stale edits, access isolation, removal history
and full rollback on an audit failure. Browser edit/remove controls remain
the next implementation step.
