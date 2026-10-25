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
