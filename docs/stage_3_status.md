# Stage 3 completion and Stage 4 handoff

Status: COMPLETE — verified local development milestone.
Implementation commit: 9650f999aea334b4d6d1cdf6628021eae8ef4839
Owner browser verification: all final generic-template planner checks passed.

This closes the schedule-building stage, not the entire V1 application.
The current functional record is this file together with decisions.md.
Older pending statements in schedule_weeks.md describe implementation history.

## Completed scope

- Blank draft weeks with configured week-start dates and overlap prevention.
- Month picker with occupied weeks disabled.
- Seven-day, fifteen-minute schedule grid with expanding starting cells.
- Multiple independent shifts at the same time and for the same job type.
- Role-only cards and configurable job-type ordering through wider arrow controls.
- Floating shift creation, editing and removal with quarter-hour time dropdowns.
- Same-day and explicit overnight shifts with timezone/DST validation.
- Automatic 30-minute meal estimate only for shifts over six elapsed hours.
- Weekly estimated working hours and shift counts by starting day and job type.
- Independent week copying, retaining structure and notes but no assignments.
- Saving an existing schedule week as an independent named template.
- Create New Template and Edit Template using a generic Monday–Sunday planner.
- Whole-template explicit saves, local draft changes and discard protection.
- Template naming, active-list archiving and independent generated weeks.
- Staff authorization, CSRF, business isolation, revisions and atomic audit history.

## Acceptance evidence

FR-020: blank draft creation and persistence verified.
FR-021 / AT-13: template creation produces independent unassigned shifts.
FR-022 / AT-13: copied weeks preserve source structure and source independence.
FR-023: duplicate role/time coverage slots remain distinct.
FR-024 / AT-17: draft shift create/edit/remove, interval validation and overnight behavior.
Template administration: direct generic pattern creation/editing and archive verified.
AT-07: the approved automatic meal estimate supersedes scheduled break intervals;
threshold and elapsed-time calculations are covered by timing tests.

Automated regression: 179 tests passed.
The latest backend changes passed the full disposable PostgreSQL certification.
Subsequent browser-only changes passed regression, asset delivery and assistant
JavaScript syntax checks. The owner verified the final browser workflows.

## Verified recovery checkpoint

Checkpoint: ~/shift-scheduling-evidence/checkpoints/20261024T232721883403Z
Recovery database: shift_scheduling_restore_20261024t232721883403z
Captured implementation commit: 9650f999aea334b4d6d1cdf6628021eae8ef4839
Tables: 18
Reviewed migrations: 21
Independent restore: PASS
Data, schema, privileges, row security and function grants: PASS
Source database unchanged: PASS
Code bundle integrity: PASS
Recovery database defaults to read-only.

The checkpoint captures the complete tested implementation. This closing
documentation commit follows it and changes documentation only.

## Stage 4 scope and open calculation decision

Next: employee assignment storage and eligibility, candidate lists,
assign/unassign transactions, overlap/qualification/rest enforcement,
warning overrides and concurrent assignment protection.

Before finalizing employee daily-hour calculations, decide how the untimed
30-minute meal estimate is allocated across local dates for overnight shifts.
Full shift endpoints remain authoritative for overlap, availability and rest.

Assignment counts and unresolved assignment warnings become meaningful in
Stage 4. Publication, immutable published revisions and employee team rosters
remain Stage 5. Hosted deployment, operational recovery mechanisms and the
full pilot remain later work.

## Tracked limits

- Copying and template patterns are bounded to 500 shifts.
- Existing off-grid template times are rejected rather than silently rounded.
- Archived template names remain reserved.
- Whole schedule-week deletion is not implemented; individual draft shifts
  can be removed. Reopening a week-start date after whole-week deletion
  remains a separate convenience feature.
- The local checkpoint is access-restricted; encrypted off-device recovery
  and hosted operations are not certified by this milestone.
