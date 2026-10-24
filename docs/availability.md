# Stage 2 employee unavailability

## Approved input model

Employees mark times they cannot work. No entries means fully available.
The interface will use My Unavailability. Availability is derived as the
complement of the effective blocked intervals.

Date exceptions fully replace that date's blocked times, including overnight
carry-in. Empty replacement means available all day; a full-day block means
unavailable all day. Deleting the exception restores the recurring pattern.

Overnight entries require explicit ends next day. A date exception with an
overnight block can carry into tomorrow, unless tomorrow has its own exception.

## Implemented foundations

The shared calendar uses the approved Monday week start and preceding-Monday
17:00 cutoff. The policy is persisted and administrator-editable.
The owner confirmed the policy browser tests passed.

The unavailability resolver works in local wall-clock minutes. It splits
overnight entries at midnight, resolves date replacements, merges overlapping
blocked portions, derives available intervals, and checks intersections.
Intervals are half-open: a shift ending when a block begins does not overlap.

The resolver accepts a date-aware recurring-pattern loader so future storage
can choose the correct effective-dated version for each origin date.
It does not select or persist versions itself.

These are local calendar calculations, not the final timezone-aware shift
eligibility service. Actual-instant conversion and DST shift cases remain
part of the scheduling implementation.

## Remaining implementation

- Live, active signed-in employee profile selection and privacy enforcement.
- Effective-dated recurring unavailability storage.
- Date exception creation, editing, deletion and history.
- Transactional cutoff/publication lock enforcement.
- Preservation of locked dates and adjacent overnight portions.
- Employee editing and effective-calendar browser screens.
- Real PostgreSQL privacy, audit and concurrency certification.

The V1 Word plan version 1.2 and decisions.md supersede earlier conflicting
availability-input wording. Existing account and staff data are unchanged
by the resolver implementation.

## Private storage and read API implementation

Migration 0010 adds recurring versions, date replacement state and frozen
day snapshots. Private employee profile/calendar read APIs are implemented
and tested with real PostgreSQL data in a disposable database.
Employee mutation endpoints, transactional lock enforcement and the browser
editor remain next. No real employee unavailability records were seeded.

## Controlled employee mutation APIs

Migration 0011 adds employee-owned recurring and date-exception writes,
cutoff/frozen-target denial, revision checks, private audit history and
PostgreSQL concurrency/rollback certification.
The browser editor and publication/snapshot creation remain pending.

## Employee browser editor

My Unavailability is connected to signed-in account lifecycle and private APIs.
It lists only linked active employee profiles, displays seven effective local
dates, and supports recurring-pattern edits and date replacements.

Date replacement choices are custom blocked times, available all day,
unavailable all day, and removal of the replacement to restore recurrence.
Overnight intervals require explicit Ends next day. End time 24:00 denotes
same-day midnight. The UI displays location timezone and server cutoff/lock
state. No entries display as Available all day.

Recurring edits start at the calendar's selected first date. The editor loads
a complete seven-day recurring version. If an effective-dated version boundary
falls inside the selected range, the user must select seven dates within one
version before editing; the editor does not combine different saved patterns.

Forms retain the loaded pattern/exception and location revisions. Conflicts,
locked dates and uncertain save outcomes require closing and refreshing.
Navigation is disabled during editing. Sign-out removes the private view;
late responses from a prior account are ignored. No browser storage is used.

Automated regression and asset-serving checks accompany this change.
Real browser verification remains pending. Publication/snapshot creation
and scheduler-facing availability integration remain pending.

## Planner redesign foundation

The approved planner redesign uses exact 15-minute local-day cells:
0 through 95, covering 00:00 through 24:00.

app/planner_logic.py implements:
- Strict cell validation and exact conversion to merged minute intervals.
- Reversible single-cell toggles.
- Blank weekly mass-edit templates.
- Inclusive date-range additive previews.
- Preservation of existing blocks and independent date edits.
- Reporting locked matching dates without changing them.
- Idempotent effective results when the same additions are applied again.

Non-quarter-hour input is rejected rather than silently rounded.
Overnight portions belong to their actual dates and cannot spill outside
the selected mass-edit range.

This is pure domain logic, not an authorized save operation. The database
service must enforce live identity, cutoff/publication state, revisions,
preview-to-commit consistency, atomic multi-date writes and audit history.
A client-provided preview is never sufficient authority to write.

The existing form editor and legacy persistence remain connected for now.
The new planner screen and date-based transaction service are pending.
No database records are changed by this foundation step.

## Date-specific planner storage and atomic saves

Migration 0012 adds unavailability_date with local-date blocked cells,
optimistic revisions, actor and timestamps. Missing rows mean fully available.
Cleared dates retain their revision with an empty blocked-cell list.

Controlled database functions:
- read_my_planner(location, first_date) returns seven dates, revisions,
  blocked cells, location policy and cutoff/frozen-day state.
- save_my_planner(location, first_date, expected_location_revision, changes)
  atomically replaces blocked cells for one through seven changed dates
  inside the displayed seven-day range.

The database derives the employee from the live session and active account,
membership and employee link. No arbitrary employee ID is accepted.
Runtime has no direct date-table grants. Writes serialize with location
policy and employee changes, reject stale revisions and locked dates, and
recheck deadlines before returning. Every effective date change is audited
in the same transaction. Existing restrictive audit privacy applies.

Frozen snapshots remain authoritative: read responses include frozen_blocked
for the future HTTP projection to use instead of ordinary date cells.
Database checks cover exact cells, whole-save rollback, independent date
clearing, concurrent saves, frozen/cutoff denial and employee privacy.

This step adds the new database path alongside the legacy path. It does not
switch the existing form editor or retire legacy write functions yet.
Keep the app stopped during the remaining transition; the UI switch must
verify legacy tables remain empty before retiring the old path.

HTTP planner routes, additive bulk preview/commit, and the visual planner
remain pending. No real employee planner blocks are seeded by migration 0012.

## Private planner HTTP API

GET /api/v1/me/planner/profiles returns the caller's active linked profiles.
GET /api/v1/me/planner/{location}?start=YYYY-MM-DD returns exactly seven dates.
PUT /api/v1/me/planner/{location} accepts first_date,
expected_location_revision and one through seven changed-date records:
date, expected_revision and cells.

Reads and writes derive the employee from the live session. Writes require
same-origin CSRF validation and use the atomic database save function.
Responses are no-store. Stale revisions return 409, locked changed dates 423,
invalid inputs 422, and unavailable employee profiles 404.

Each date includes effective_cells and exact blocked minute intervals.
Frozen snapshots take precedence. If a frozen historical interval is not on
15-minute boundaries, effective_cells is null and the exact locked interval
remains available for display; no rounding or editable approximation occurs.

Disposable PostgreSQL HTTP checks cover persistence, independent clearing,
CSRF/origin rejection, input validation, stale and locked whole-save rollback,
audit failure rollback, snapshot projection and employee privacy.

Production database state is unchanged by this API step.
The old form editor remains connected pending the visual planner replacement.
Additive bulk preview/commit and the visual planner remain pending.
