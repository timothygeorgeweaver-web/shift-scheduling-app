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
