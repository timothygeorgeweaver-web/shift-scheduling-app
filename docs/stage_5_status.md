# Stage 5: publication, employee rosters and amendments

Status: started; employee-safe snapshot projection implemented.

The snapshot explicitly includes shift/role identity and names, saved timezone
and rollover, actual and local endpoints, estimated labor, and employee identity
and display name or an explicit unassigned value.

Private availability, approval reasons, internal shift notes and arbitrary
working-record fields are excluded. Names and policy values are copied so
subsequent working changes cannot alter the snapshot representation.

This pure projection does not authorize or publish anything. Empty weeks and
unassigned shifts can be represented; publication eligibility is a separate
policy gate.

Next:
- Immutable sequential publication storage and protected staff history.
- Fresh whole-week publication review, hard-conflict rejection and current
  per-rule approval checks.
- Atomic publication, availability locking, revision and audit writes.
- Employee-safe full-team published roster access.
- Staged amendments and explicit republishing with prior versions retained.
- Browser acceptance, security/integration certification and recovery.

The owner approved publication with unassigned shifts after a clear warning
and explicit confirmation. Published rosters must display those slots as Unfilled.


## Immutable publication storage

Migration 0047 adds tenant-scoped publication records with sequential version
uniqueness per week, saved source revisions, actor/time and roster snapshots.
Updates and deletion are rejected by an immutable-history trigger.

Staff history reads reuse existing authenticated week authorization and expose
version metadata. Employee-only accounts and invalid sessions are denied.
The runtime has no direct publication-table privileges.

Disposable certification covers history order, duplicate-version denial,
immutability and staff authorization. Installation creates an empty table;
it does not publish any real schedule.

Publication validation/commit, availability locks, employee-safe roster reads
and amendment integration remain pending.
Recovery inventory: 22 tables and 47 reviewed migrations.

## Fresh publication review and confirmation rules

Added a pure whole-week review that requires fresh inputs for every assigned
shift, independently of any stored clear/warning badge.

Hard conflicts block publication. Each current soft warning requires a
matching assignment-revision approval, nonblank reason, matching saved policy,
and explicit evidence of no relevant dependency changes after approval.
Daily-hours approval must also match the newly computed exact daily totals.
Private reasons and raw availability are excluded from the review result.

Publication requests require exact week, location and prior publication
revisions. Publication confirmation is mandatory; Unfilled coverage requires
a separate explicit confirmation.

The future protected database reader must supply approval freshness evidence
and complete assignment inputs under appropriate transaction locks. No HTTP
publication endpoint or database publication writer is enabled by this step.

## Locked publication review API

Added a CSRF-protected staff publication-review endpoint backed by organization,
location, week, shift, assignment, employee, role and actor authorization locks.
It reads all current assignment inputs, private approvals, saved role order and
the latest publication number before applying the shared review rules.

Availability approvals are invalidated by relevant later availability events.
Daily-hours approvals must match exact current totals; replacing a neighboring
assignment with unchanged hours does not create circular approval invalidation.
Fresh hard checks and saved-policy matching remain mandatory.

Inactive job types block publication even on unassigned coverage slots.
HTTP results omit private approval reasons and personal availability intervals.
Reviews publish nothing and write no audit or review history.

Focused certification covers current/stale approvals, changed qualifications,
privacy, CSRF/origin, revision and role guards, and two-connection serialization.
Publication commit, availability freezing and employee roster delivery are next.
Recovery inventory: 22 tables and 48 reviewed migrations.

## Exact business-week availability lock plan

Publication locks must cover the saved business-week interval, rather than
freezing all eight touched calendar dates in their entirety.

At a 05:00 rollover, the first calendar date is locked from 05:00 onward,
the six middle dates are fully locked, and the final calendar date is locked
only before 05:00. Adjacent business-week hours remain outside that publication
lock, subject to their independent cutoff or other existing locks.

Added a pure plan containing exact calendar minute windows and UTC boundaries.
It covers all 672 local quarter-hour cells while retaining 167/168/169-hour
elapsed weeks across DST. Missing or repeated rollover boundaries fail closed.

Partial-date persistence, changed-cell enforcement, planner lock indicators
and atomic publication integration remain pending. No live lock is created
by this helper.

## Publication cell-lock enforcement

Publication records define immutable saved business-week windows. Planner reads
now expose their locked calendar-minute portions. Fully covered dates are
read-only; partially covered dates retain editable adjacent-week hours.

A database trigger serializes with publication and rejects raw date-cell
changes inside published portions, including writes through the older calendar
API. Existing independent cutoff/frozen-date restrictions still apply.

The business-day browser projection uses per-cell publication locks. Mass-edit
previews skip a date when its requested additions would change published cells;
already-present blocked cells do not count as changes. Lock windows participate
in preview and save gate comparisons.

Focused disposable checks cover both edges, full-date locks, adjacent-week
edits, older API bypass denial, multi-date rollback and mass-edit preview.
No real publication is created by installation.

Private effective-availability snapshots, frozen-interval integration and the
atomic publication writer remain pending.
Recovery inventory: 22 tables and 49 reviewed migrations.

## Private publication availability evidence

Every new publication automatically captures private effective availability
for all existing location employees, including inactive profiles, across its
exact calendar-date portions. Existing frozen intervals take precedence over
editable cells. Off-quarter minutes are clipped exactly without rounding.

Snapshot rows are tenant-scoped, immutable and inaccessible directly to the
runtime. Employee-safe roster snapshots remain separate from this evidence.

A shared database guard prevents later frozen-date insert/update/delete from
changing published effective minutes. Changes confined to adjacent-week
portions remain possible under the other existing authorization/cutoff rules.

Publication and snapshot inserts share one transaction. A failure on the final
snapshot date rolls back the publication and all preceding snapshot rows.

Focused disposable certification covers exact clipping, both employee states,
history immutability, frozen-interval protection, adjacent portions, private
access and complete failure rollback. No real publication is created.
Recovery inventory: 23 tables and 50 reviewed migrations.

The next step is the protected publication writer, combining fresh review,
explicit confirmations, immutable roster/snapshot creation and final audit.
