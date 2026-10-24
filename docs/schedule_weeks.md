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

## Browser shift editing and deletion

Draft shift cards are keyboard-accessible buttons displaying only the job
role. Opening a card fills the date, exact 24-hour times, next-day flag,
role and note from the saved shift. Save changes uses PATCH and both saved
revisions; Cancel preserves the saved shift.

Delete shift asks for confirmation identifying the saved role and times,
then calls the audited removal endpoint. It remains available when the
saved role is inactive. Saving an edit requires an active role.
Uncertain requests and stale revisions require a refresh before retrying.
Refreshing closes the form so stale saved values cannot be reused.
The planner retains page scrolling and job/finish-time sorting.

Shift start and end controls now use 24-hour dropdowns with 96 quarter-hour
choices, from 00:00 through 23:45. Midnight finishes use 00:00 together
with Ends next day. Existing off-grid times are not silently rounded:
editing displays a placeholder requiring a quarter-hour selection before
saving. Cancel and deletion remain available without changing those times.

## Floating shift editor

Add and Edit shift open in a compact dialog positioned beside the clicked
cell or card. It uses the right side when space permits, otherwise the
left, with a centered fallback for narrow screens. Position is clamped to
the viewport and recalculated on resize.

The dialog retains quarter-hour dropdowns, revision checks, deletion
confirmation and unsaved-change protection. Close, Escape and clicking
outside the panel use the same discard protection. Request errors appear
inside the dialog as well as in the week status.

The page has responsive side margins up to 48 CSS pixels.
The schedule itself continues to use page scrolling.

## Editable job-type display order

Job-role creation and editing accept display_order, a strict integer from
0 through 9999. Existing roles default to 0. Lower numbers appear first;
matching values use name and role ID as deterministic tie-breakers.

Team forms expose Display order. The schedule grid uses the stored value
to group shifts, then sorts shifts of the same role by actual ending
instant and shift ID. Refresh an already-open week after changing job
order to load the new order.

Order-only edits retain staff revision locking and atomic audit writes.
Disposable PostgreSQL checks cover validation, stale/concurrent edits,
business isolation and audit rollback.

## Drag-and-drop job-type arrangement

Arrange job types opens a floating window listing all roles, including
inactive roles with a label. Dragging or Move up/down changes a local
preview. Save order submits all role IDs and expected revisions in one
transaction. Cancel/Escape checks for unsaved changes.

The API locks existing role rows in ID order, rejects duplicate IDs,
changed list membership and stale revisions, and audits each changed rank.
An audit failure rolls back the entire arrangement. A newly created role
after the transaction's list read appears on the next refresh and can then
be arranged. Individual numeric ordering fields are hidden in Team forms.

Refresh an already-open schedule week to load the saved role arrangement.
Within each role, shifts still sort by earliest actual finish.

Job-type arrangement now uses wider up/down arrow buttons exclusively.
Drag-and-drop and grab handles have been removed following browser feedback.
Atomic saving and unsaved-order cancellation remain unchanged.

## Atomic copying into a new draft

POST /api/v1/staff/locations/{location}/schedule-weeks/{source}/copy accepts
week_start_date, expected_source_revision and expected_location_revision.
The destination must be an unused configured week-start date in the same
location. Source and destination must use the same week-start weekday.

The operation creates a new draft and copies all active coverage slots
with new IDs and no employee assignments. It preserves local weekday,
start/end wall times, overnight endings, role and note. It recalculates
UTC endpoints and working-hour estimates in the destination timezone.
Ambiguous/nonexistent destination times or inactive roles reject the
whole copy. Removed shifts are excluded. Copies are limited to 500 slots.

Creation obtains the organization lock shared by controlled shift writes.
The source is re-read and its revision checked under that lock. Destination,
shifts and audit events commit together. A copy summary audit records the
source revision and destination ID. Failures roll back the entire operation.
The source remains unchanged. No new migration or privileges are required.

Disposable PostgreSQL checks cover persistence, structure/overnight times,
source preservation, authorization, CSRF, stale settings/source, inactive
roles, duplicate/concurrent destinations and complete audit rollback.
The browser copy controls are the next implementation step.

## Browser copy workflow

Each listed schedule offers Copy this week. Selecting it switches the
creation calendar into copy mode and identifies the source dates.
Month navigation retains the chosen source. Changing workplace or
cancelling copy mode clears it.

Only unused configured week-start dates are selectable. Copy week asks
for confirmation of source and destination dates, then sends both source
and location revisions. Success reloads the destination calendar and
returns to blank-week mode. Conflicts refresh the calendar and require
source/destination selection again. Uncertain outcomes require refresh.
The source is never modified by the browser copy workflow.

## Independent named schedule templates

Migration 0019 adds private schedule_template storage and controlled
save/list/read functions. Saving captures active shift structure, role IDs,
local endpoints and notes as a snapshot, with the source's week-start day.
It does not retain employee assignments or depend on later source edits.

Names are trimmed, limited to 100 characters and unique ignoring case
within a location. Templates are limited to 500 slots. Saving checks the
source revision under the organization lock and writes its audit event in
the same transaction. Runtime has no direct template-table access.

Staff APIs list templates, read their snapshots, and save from an existing
week. Disposable PostgreSQL checks verify persistence, independence from
source edits, authorization, CSRF, stale source/name checks and audit rollback.

Applying templates and browser controls are the next implementation step.
No real templates or shifts are created by the production migration.
