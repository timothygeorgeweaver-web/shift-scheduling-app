# Stage 4: employee assignments and eligibility

Status: started. Shared pure overlap and rest timing checks are implemented.

- Full shift endpoints determine overlap; adjacent shifts do not overlap.
- Default rest policy: an end between local 20:00 and 06:00 inclusive
  requires 12 elapsed hours before the next shift.
- Timing helpers accept configurable rest settings.
- UTC comparisons preserve elapsed time across daylight saving transitions.
- The persistence service must check both chronological neighbors,
  including assignments outside the displayed schedule week.

Not yet implemented: assignment storage, candidate eligibility, qualification
checks, effective unavailability warnings, daily-hour warnings, override reasons,
administrator rest-policy controls, transactional revalidation and browser UI.

Allocation of the untimed meal estimate across local dates remains an open
decision before implementing overnight daily-hour calculations.

This step makes no database changes and exposes no new HTTP routes.

## Business-day foundation

Approved business-day rollover supersedes midnight-based daily grouping.
Shared helpers now map business-day cells to exact calendar datetimes and back,
label actual instants using the location timezone, and resolve UTC day bounds.

The default rollover is 05:00. DST day lengths are measured in actual elapsed
time. Ambiguous/nonexistent boundaries fail explicitly.

This foundation is not connected to live settings, storage, totals or planners.
Next: persist the location setting and protect existing week/template semantics,
then update all planners and business-day labor calculations.

## Rollover storage foundation

Migration 0022 adds constrained business_day_start_minute values:

- Location policy defaults to 300 (05:00).
- Saved schedule weeks and templates retain 0 (00:00).
- Every value must be a non-null quarter-hour clock minute.
- Existing shift endpoints, template patterns and unavailability are unchanged.
- No additional runtime write privileges are granted.

This is staged storage, not activation of the business-day planners.
Week/template creation defaults remain midnight until their functions,
copy/apply paths and editors can handle the new semantics together.

The disposable PostgreSQL certification checks defaults, constraints,
migration rollback and preservation of existing values. The reviewed
installer also checks all existing business records during source migration.

Next: administrator policy controls and explicit rollover capture for new
weeks/templates, followed by planner and unavailability integration.

## Administrator business-day policy API

Migration 0023 adds the controlled edit_business_day_policy function.
GET/PATCH /api/v1/staff/locations/{id}/business-day-policy are administrator-only.

Writes require CSRF, an exact location revision and a quarter-hour rollover.
Organization/location locks serialize competing edits. Policy and audit changes
commit atomically; unchanged values cause no revision or audit changes.
The shared location revision also invalidates stale settings/planner requests.

Certification covers authorization, business isolation, validation, CSRF/origin,
stale/concurrent edits, audit failure rollback and saved-record preservation.
No source location policy is changed by installation.

The API is staged configuration. New-week/template capture, planner activation,
DST boundary integration and the administrator browser control remain pending.

## Saved business-week shift boundaries

Migration 0024 upgrades shift creation/editing to validate the start's business
date using the week's saved rollover. HTTP validation matches database checks.

For a 05:00 Monday week, the following Monday before 05:00 is included and the
first Monday before 05:00 is excluded. Actual calendar timestamps are retained.
Midnight-based saved weeks retain their existing behavior.

Focused disposable-database checks cover create/edit boundaries, direct SQL
denial, unchanged timestamps and midnight compatibility. Targeted pure timing
tests cover rollover, DST and elapsed-time rules.

New-week rollover capture remains staged until the schedule grid can display
these boundaries. Template and unavailability integration remain pending.

## Schedule grid business-day display support

The shared schedule grid now reads the saved week rollover, orders its 96
local-clock rows from that time, groups early-morning starts under the prior
business date, and marks those rows as Next day.

The scheduled-shift editor selects a business day, displays the resulting actual
calendar start date, and submits the actual date to the API. Its next-day flag
continues to mean the calendar day after the actual start date.
Coverage counts use the saved business-day grouping.

Existing midnight-based weeks retain their existing grouping. Generic templates
continue using midnight until template rollover metadata is connected.
New-week rollover capture and employee unavailability integration remain pending.
Browser acceptance of the activated rollover is still required.

## Business-day-aware pattern copying

The shared copy/apply helper now maps each source shift by its starting business
weekday and wall-clock times into the destination's saved rollover.
Its same/next-calendar-day end relationship is preserved.

Source weeks supply their saved rollover. Legacy template snapshots without
rollover metadata continue to mean midnight. Template snapshot rollover
metadata must be connected before non-midnight template creation is activated.

Target shift timing still resolves actual elapsed duration and rejects invalid
DST endpoints. Source records are not changed.

Targeted tests cover all seven weekdays and all 96 clock cells across several
rollover combinations, legacy Sunday early hours, overnight shifts, the
following Monday's early hours and invalid source intervals.
New-week rollover activation and end-to-end copy/apply checks remain pending.

## Template rollover conversion and editor support

Generic weekday conversion now maps business weekdays to actual local calendar
timestamps using a saved rollover. Snapshot metadata records the rollover;
legacy snapshots without it retain midnight semantics. Conflicting metadata is
rejected rather than silently interpreted.

The pattern API returns the saved template rollover. The generic editor uses it
for grid grouping, quarter-hour choices and business weekday selection.
The end-next-day flag remains relative to the actual calendar start date.

Targeted tests cover all weekdays/week-start settings, several rollovers,
following-Monday early hours, legacy snapshots and invalid metadata.
Database capture for new templates and scheduled-week snapshots is still pending.
New direct templates remain midnight-based until that capture is activated.

## New week and template rollover capture activated

Migration 0025 captures the location rollover for new blank weeks and direct
templates. Saving a week as a template captures that week's saved rollover.
Replacing a template from a week captures the replacement week's rollover;
renaming, archiving and direct editing preserve its existing rollover.

Template snapshot metadata carries the captured value. SQL validates direct
pattern starts against business-week boundaries and checks rollover metadata.
Existing saved records are not migrated or reinterpreted.

New generic template editors receive the current location rollover.
Scheduled and generic grids already support their saved rollover.

Focused disposable checks cover new creation, following-Monday early shifts,
save/edit/apply/copy, legacy-week copying, stale policy denial and preservation
after a later location-policy change.

Browser acceptance, administrator controls, employee unavailability integration,
DST rollover-boundary safeguards and daily labor allocation remain pending.

## Whole-week removal foundation

Migration 0026 adds audited draft-week removal and a deleted_at marker.
Live week listing, detail, overlap checks and shift writes exclude deleted weeks.
A partial unique index permits a new week on a deleted week's original date.

Removal retires active shifts and preserves historical rows. The operation
requires staff authorization, CSRF and the exact week revision; non-draft
weeks are denied. Targeted checks cover rollback on audit failure and fresh
recreation without inherited shifts.

The calendar/action-panel redesign and Append/Replace template application
remain pending. The week-removal browser button is not connected yet.
Month navigation width and Next Day line layout have been adjusted.

## Atomic template Append/Replace API

Template application now accepts mode append/replace and an optional exact
destination week ID/revision. Without an existing destination it creates a
new draft. With one, migration 0027 locks and authorizes the existing draft
and verifies both week and location revisions before modifying shifts.

Append preserves existing shifts. Replace retires active shifts and inserts
the pattern using the destination's saved rollover. All removals, insertions
and audit events share one transaction. Historical shift rows remain intact.

Focused certification covers preservation, replacement, fresh destinations,
business-day placement, CSRF, stale/concurrent requests, non-draft denial
and complete rollback following a final audit failure.

The combined selected-week browser controls and mode dialog remain pending.

## Combined selected-week browser controls

The calendar now selects existing and unused week-start dates. A single action
panel below it provides Create blank week, Open week, Save as Template,
Copy this week, Delete week and Apply Template with state-dependent availability.

Copying selects a source first, then an unused destination date.
Apply Template chooses a saved pattern and Append/Replace mode. Replacing an
existing week's shifts requires explicit confirmation. Deletion also confirms
the whole week and preserves independent saved templates.

The separate Schedule weeks list is removed. Closing the scheduled-shift editor
refreshes the listing revision before subsequent selected-week actions.
Month controls are constrained to the calendar area.

Local JavaScript syntax and simulated action-flow checks cover button states,
creation/deletion, copying, Append/Replace payloads, editor-close refresh and
dialog cleanup. Asset serving passes. Owner browser acceptance remains pending.

## Administrator rollover browser control

Team now includes an administrator-only Business-day rollover section.
It reads the selected location's saved policy and offers all 96 quarter-hour
clock times with a business-day preview.

Saving submits CSRF and the exact location revision to the audited policy API.
A failed or uncertain save blocks repeat submission until Team is refreshed.
Existing saved weeks/templates keep their captured rollover.

Local syntax and simulated interaction checks cover administrator visibility,
scheduler exclusion, dropdown values, CSRF/revision payloads and failure retry
protection. Asset serving passes. Owner browser acceptance remains pending.

Employee unavailability/mass-edit rollover integration, business-day labor
allocation and DST rollover-boundary safeguards remain pending.
