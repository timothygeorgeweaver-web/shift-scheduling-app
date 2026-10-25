# Stage 4: employee assignments and eligibility

Status: COMPLETE.

Verified implementation commit: `76a1668e15cd2e8e6722e18f535368517ba22055`.
Owner browser acceptance: PASS.
Full regression before the recovery-comparison repair: 450 tests passed.
Additional targeted recovery-comparison tests: 37 passed.
Independent restore: PASS.
Checkpoint: `/home/timothygeorgeweaver/shift-scheduling-evidence/checkpoints/20261025T215230373770Z`.
Inventory: 21 tables and 46 reviewed migrations.

## Completed behavior

- Separate Schedule Planner and Assign Shifts views. The assigner displays
  seven business-day columns and only occupied shift-start rows.
- Automatic employee loading, explicit replacement confirmation, immediate
  unassignment and atomic whole-week assignment clearing.
- Fresh qualification, employee/role status, overlap, rest, effective
  availability and daily-hours checks.
- Hard conflicts cannot be overridden. Each availability or daily-hours
  warning requires explicit approval and its own nonblank reason.
- Administrator rest settings default to 12 elapsed hours after shifts ending
  from 20:00 through 06:00 inclusive.
- Assigned-shift edits preview proposed role/timing and newly touched
  availability dates. Save rechecks locked inputs and atomically renews
  the same employee's assignment, preserving prior assignment and approvals.
- Specific automatic warning indicators without a Review assignments button
  or polling loop. Both contributing shifts show daily-hours violations.
  Resolved indicators clear on the next normal view load.
- Relevant dependency changes mark affected draft assignments without
  removing them. Rest-policy changes reassess affected assignments atomically
  with policy and audit updates.
- Removal of shifts and weeks retires affected assignments while preserving
  immutable approvals. Template replacement uses the shared removal trigger.
- Template and copy patterns remain independent of employee assignments.
- Floating confirmations and viewport-constrained editing windows.

## Business-day rules

The administrator rollover defaults to 05:00. New weeks and templates capture
their policy; existing saved policies and actual timestamps remain intact.

Schedule, template and unavailability planners use business-day grouping.
Unavailability retains actual calendar-date storage, exact frozen intervals,
actual-date locks and hidden edge cells through atomic eight-date operations.

Each shift's labor allocation uses its own saved timezone and rollover.
Matching business-date labels combine for daily-hours checks. Actual instants
determine overlap and elapsed rest, including across DST transitions.
Missing or repeated rollover boundaries are rejected.

Shifts longer than six elapsed hours receive one untimed 30-minute meal
estimate, allocated proportionally and exactly across business days.
This estimate does not record when a break was actually taken.

## Validation

Focused PostgreSQL checks throughout development covered authorization,
business isolation, CSRF/origin, private interval protection, fresh revisions,
transaction serialization, immutable history and audit failure rollback.

Final integration checks passed for:
- Assigned-edit preview without writes and atomic save with fresh approvals.
- Two competing edits producing one success and one stale conflict.
- HTTP assignment across different saved business-day policies.
- Assigned-week deletion, retained approvals and final-audit full rollback.
- Daily-hours indicators on both shifts and automatic resolution.

The full regression run passed 450 tests. The subsequent recovery-comparison
repair passed 37 targeted tests. Owner browser checks passed.

## Recovery verification

The fresh checkpoint independently restored and compared data, schema,
constraints, privileges, row security, function definitions and migration
checksums. Source state remained unchanged. The recovery database defaults
to read-only.

The comparison recognizes only the two exact equivalent AND-parenthesis
forms observed for six known quarter-hour constraints. Changes to bounds,
operators or other constraint definitions still fail comparison.

The earlier comparison-failure checkpoint remains preserved for diagnosis.
The verified checkpoint above supersedes it for this handoff.

## Scope boundary

Stage 4 covers draft employee assignments and eligibility. Schedule publication
and employee-facing published schedules remain subsequent work.

Planning, assigning and template editors are mutually exclusive within the
browser workspace. Other tabs and users are protected by server authorization,
database transaction locks and revision checks.

The trusted application server computes eligibility. Database bridges bind
its decisions to fresh locked inputs. Input fingerprints detect changes;
they are not signatures proving rule correctness.

Approved warnings remain visible while their underlying condition exists.
An approval permits an assignment without erasing the condition.
