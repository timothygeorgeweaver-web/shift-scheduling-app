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
