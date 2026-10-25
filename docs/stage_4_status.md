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

## Rollover save protection and automatic calendar refresh

Save business-day rollover is disabled while a schedule week or template editor
is open. A message beside it says Please close open week before saving.
Closing the editor updates button availability immediately. Form submission
also checks editor state, preventing Enter-key submission while blocked.

Confirmed policy saves automatically refresh the matching schedule calendar.
A request already in progress is allowed to finish before refreshing.
The normal Refresh calendar button is removed; a retry button appears only
when calendar loading is unavailable.

Local syntax and simulated interaction checks cover initially open editors,
opening/closing after form load, blocked submission, confirmed-save refresh,
failed-save retry protection, and reset cleanup. Asset serving passes.

## Unavailability business-day storage mapping

Shared helpers map business-day planner cells onto existing calendar dates.
Seven business-day columns can span eight calendar dates.

Explicit cell edits preserve all untouched stored cells, including portions
outside the displayed business week. Actual calendar-date locks reject changed
cells; unchanged locked cells produce no write. Inputs are never mutated.

Inclusive mass-edit business dates map selected weekday cells onto their actual
calendar dates, including carry into the date after the final business day.
These mapped cells are additions only; persistence must union them with saved
cells and recheck revisions, authorization and locks atomically.

Targeted tests cover all week cells, Sunday carry, boundary-cell preservation,
locks, no-op edits, duplicate/invalid inputs and supported date limits.

This is pure mapping only. Eight-date transactional API support and the
unavailability/main mass-editor display integration remain pending.

## Atomic eight-calendar-date employee API

Migration 0028 includes rollover in the private employee profile.
GET/PUT /api/v1/me/business-planner/{location} provide an eight-calendar-date
view for a seven-business-day planner.

The new save service holds the existing employee/location transaction scope,
checks policy and date revisions, preserves hidden edge cells and checks actual
calendar-date locks. Existing protected save functions execute both batches
inside the same transaction, followed by final gate/session checks.

Focused PostgreSQL certification covers private reads, all-eight-date saves,
edge preservation, stale requests, CSRF/origin, frozen exact intervals and an
audit failure on the eighth date after the first batch has written.

No new employee-table permissions are granted. Existing browser routes and
display are unchanged. Business-day browser and mass-edit integration remain
pending.

## Main unavailability business-day display and saves

The main employee planner now uses the eight-date business-planner API.
It projects seven business-day columns from actual calendar-date records and
orders rows from the location rollover, with Next Day beneath early times.

Each cell retains its actual calendar date and lock state. Mixed columns show
Partially locked. Non-quarter-hour frozen intervals remain exact and read-only;
the view marks intersecting cells without rounding the stored snapshot.

Only changed editable cells are merged into calendar-date save requests.
Hidden boundary cells and unchanged locked cells are preserved.

Local syntax and simulated checks cover projection, hidden edges, mixed locks,
exact frozen interval labels, locked-cell denial and editable-cell toggles.
Asset serving passes.

The mass editor still uses its original calendar-day API/layout pending the
next integration block. Browser acceptance will cover both planners together.

## Business-day additive mass editor connected

The mass editor now uses the location rollover and inclusive business-date
ranges. A final Sunday's early hours can add blocks on the following Monday.

Preview lists actual calendar dates alongside their originating business dates.
The request includes the exact location revision, preventing a pattern drawn
under one rollover from being silently interpreted under another.

Preview performs no writes. Apply unions mapped cells with saved cells, skips
actual locked calendar dates, checks the session-bound preview stamp and
rechecks all selected date gates before committing the complete transaction.

Focused PostgreSQL checks cover inclusive Sunday carry, preservation of
existing blocks, idempotent repetition, stale previews/policies, frozen-date
skips and final-audit failure rollback across multiple weeks.

Both unavailability planners now use business-day layouts. Owner browser
acceptance remains pending. DST boundary safeguards and business-day labor
allocation remain separate remaining work.

## Unavailability page scrolling and visibility toggles

Owner browser acceptance passed for business-day main and mass-edit behavior.

Both unavailability planners now scroll with the page rather than inside a
height-limited grid. Each has a Hide planner/Show planner toggle above its grid.

The toggle changes visibility only: it does not rebuild the grid, clear drafts,
submit requests or discard previews. Main and mass-editor visibility preferences
are separate within the current signed-in view. Save, date-range, preview and
return controls remain outside the hidden grid.

Local syntax and simulated interaction checks cover hiding/showing without
requests or grid reconstruction, preservation of unsaved cells, accessible
expanded state, and mass preview submission while hidden. Asset serving passes.

## Exact business-day labor allocation

Shared labor calculation now splits actual elapsed shift time at the configured
local business-day rollover. Overnight hours before rollover stay in the prior
business day. A shift ending exactly at rollover creates no empty next-day row.

The approved single 30-minute estimate applies only when the full shift exceeds
six elapsed hours. Its allocation across business days is proportional to each
day's elapsed portion. Exact fractions avoid intermediate rounding errors.

Tests cover midnight compatibility, configurable rollover, overnight grouping,
exact-boundary ends, fractional conservation, six-hour thresholds, 23/25-hour
DST days and explicit rejection of ambiguous/nonexistent rollover boundaries.

This is pure calculation only. It does not alter shifts, schedule lunch times,
record meals or make payroll deductions. Eligibility aggregation and browser
labor totals are not connected yet. Overlap/rest retain full shift endpoints.

## Actual-date DST rollover safeguards

Migration 0029 validates all live saved weeks without changing them, then
requires eight unique, advancing rollover boundaries before creating a week.
Blank creation, copying and template application share this guard.

Employee business-week reads/saves and mass-edit ranges also validate their
actual local business-day boundaries. Missing or repeated rollover instants
are rejected explicitly. Configuration remains quarter-hour based; its
suitability is checked against the actual dates and installed timezone rules.

The unavailability UI now displays the server's validation explanation.

Focused PostgreSQL checks cover missing spring and repeated fall rollover
times, rejected copy/template/private-planner operations, unchanged audit
state after rejection, and valid 05:00 weeks with 23/25 elapsed-hour shifts.
Existing saved policies and shift endpoints remain unchanged.

Employee eligibility and assignment persistence are next.

## Shared employee eligibility rules

The pure eligibility engine classifies candidates as eligible,
override_required or blocked.

Hard reasons include inactive employee/role, missing qualification, overlap and
insufficient rest before/after the candidate. Both chronological neighbors are
checked using actual instants, including across week and DST boundaries.

Warnings include any intersection with effective unavailability over the full
shift and more than eight estimated working hours on a business day.
Labor totals include the candidate and supplied existing assignments across
roles, with exact proportional meal allocation and no intermediate rounding.

The evaluator accepts an explicit timezone/rollover/rest policy. Persistence
must supply fresh authorized assignments, qualification and effective blocked
intervals, exclude a replaced assignment, and revalidate all affected records
under their applicable policies before committing.

No candidate endpoint or assignment write is enabled by this block.
Assignment persistence, override records, policy controls and UI remain pending.

## Assignment storage foundation

Added private assignment storage with one active assignee per shift and
tenant-scoped employee/shift foreign keys. Retired assignment records remain
available for history.

Separate immutable approval records identify the assignment revision,
employee, violated soft rule, reason, approving actor, policy snapshot and
rule details. Only unavailability and daily-hours warnings can be recorded
as overrides; hard rules cannot be approved through this table.

The application has no direct read or write privileges on either new table.
No assignment mutation endpoint or browser assignment control is enabled.
Fresh candidate retrieval, protected commit-time eligibility checks,
assignment audit writes, and integration with shift edits, removals and
template replacement remain pending before assignment writes are enabled.

Focused disposable-database checks cover active-assignee uniqueness,
replacement history, immutable approvals, invalid override rejection,
scoped foreign keys, runtime access denial and fixture rollback.
The source migration creates two empty tables and changes no saved shifts.
Recovery inventory now includes twenty tables and thirty migrations.

## Assignment availability intersections

Added exact actual-time intersection of a proposed shift with authorized
effective calendar-date minute ranges. The reader must supply every touched
date and select frozen intervals ahead of editable date cells.

The conversion preserves non-quarter-hour frozen minutes, checks the full
overnight shift, blocks both occurrences of repeated DST clock minutes, and
does not invent elapsed time for nonexistent clock minutes. Missing dates
and subminute endpoints fail explicitly instead of being rounded or
silently treated as available.

This is a pure conversion helper. Fresh candidate database reads and
protected assignment mutations remain pending. No database writes occur.

## Authorized assignment inputs

Added an internal staff-authorized database reader for one draft shift and
one same-location employee. Each call reads current active state,
qualification, role state, assignment revisions, the slot's current assignee,
and other live assignments across week boundaries.

Availability inputs cover every actual calendar date touched by the full
shift. Frozen blocks remain separate from editable cells so the service can
apply frozen precedence without rounding.

The reader exposes no HTTP endpoint and grants no direct table access.
Candidate evaluation must use one consistent transaction snapshot; saving
must lock and re-read inputs rather than trust an earlier candidate result.
Protected assignment writes, configurable rest policy, and browser candidate
controls remain pending.

Focused disposable checks cover authorized projection, current assignment
identity, fresh employee state, invalid employee/session denial, storage
constraints and rollback. No source employee assignments are created.
Recovery inventory includes twenty tables and thirty-one migrations.

## Candidate evaluation service

Connected authorized assignment snapshots to the shared eligibility engine.
Frozen availability takes precedence, including explicitly empty frozen
snapshots. Exact availability intersections cover the full shift.

Results contain employee identity, revisions, eligibility reasons and exact
business-day working-minute totals. Raw personal availability and neighboring
shift intervals are excluded from the candidate result.

The internal reader uses its caller's transaction and fetches inputs again
on every call. No assignment mutation or HTTP endpoint is enabled.

Rest policy must be supplied explicitly. Persistent administrator rest
settings remain pending. Mixed saved timezone/rollover policies currently
fail explicitly; reconciliation is required before enabling assignment
writes for those cases. Matching-policy daily totals use the proposed
shift's saved business-day policy.

Focused tests cover hard rules, soft warnings, frozen precedence, adjacent
week rest, combined hours, privacy projection and fresh repeated reads.

## Persistent rest policy foundation

Location storage now includes quarter-hour rest window endpoints and required
rest minutes. Defaults are 20:00 through 06:00 inclusive and 720 elapsed
minutes of rest. Required rest supports 0 through 1440 minutes.

The authorized candidate reader returns the current rest policy and shared
location revision with its other fresh inputs. No direct runtime policy
write grants or administrator editing endpoint are introduced here.

Focused disposable checks cover default projection, valid policy storage,
invalid ranges, quarter-hour and null constraints, authorization, assignment
storage protection and rollback. Source shifts and assignments remain
unchanged. Recovery inventory includes twenty tables and thirty-two
migrations.

Administrator policy editing, policy-change review of existing assignments,
and protected assignment commits remain pending.

## Read-only candidate API

Added a staff-only draft-shift candidate endpoint grouping same-location
employees as eligible, override required, or blocked. Each request uses a
read-only repeatable-read transaction and current saved location rest policy.

Responses include classification reasons and relevant revisions, with
Cache-Control no-store. Raw personal blocked intervals and neighboring
assignment intervals remain internal.

Focused PostgreSQL HTTP checks cover fresh qualification and availability
changes, inactive employees, policy revisions, unknown location/shift denial,
employee-only denial, privacy projection, and absence of assignment writes.

No browser assignment control or assignment mutation is enabled yet.
Administrator rest-policy editing, mixed-policy reconciliation, protected
assignment commits and affected-assignment review remain pending.

## Administrator rest-policy API

Added administrator-only rest-policy reads and CSRF-protected updates.
Whole-quarter-hour inputs are validated in both HTTP and database layers.
Updates serialize through the organization lock, check the shared location
revision, and atomically record policy and audit changes. Unchanged requests
do not create a new revision or audit event.

During the assignment implementation transition, changed policy requests
are denied if live assignments exist. This guard must be replaced by
audited affected-assignment review before policy changes over existing
assignments are enabled.

Focused disposable HTTP checks cover validation, administrator versus
scheduler access, stale and concurrent changes, no-op behavior and audit
failure rollback. No source rest-policy values are changed by certification.

Administrator browser controls, affected-assignment review, mixed-policy
reconciliation and protected assignment commits remain pending.
Recovery inventory includes twenty tables and thirty-three migrations.

## Assignment transaction locking

Added an authorized assignment scope that locks the organization, location,
week, shift, employee, role and live actor authorization records before
re-reading current inputs and checking expected week, shift and policy
revisions. Locks remain held until the caller's transaction finishes.

This scope is intended for read-committed assignment mutation transactions.
Candidate browsing continues to use read-only repeatable-read snapshots.
The scope performs no assignment writes and does not authorize a save
without fresh eligibility validation.

Focused PostgreSQL checks use two independent runtime connections to prove
serialization and lock release after rollback. Invalid sessions and stale
policy revisions are rejected. No assignments or audit events are created.

Protected assignment persistence, per-rule approvals, affected-assignment
review and browser assignment controls remain pending.
Recovery inventory includes twenty tables and thirty-four migrations.

## Assignment save request validation

Added strict assignment request models and validation over freshly locked
eligibility inputs. Save requests identify the expected week, shift, location
policy and current assignment revisions.

Hard blocks cannot be overridden. Soft-rule approvals must match the exact
current warning set, with one nonblank reason per rule. Replacing another
employee requires explicit confirmation and the current assignment identity
and revision. Unexpected new assignees are stale conflicts.

The validator is pure and performs no writes. It must be called after actor
authorization, transaction locking and fresh eligibility evaluation.
Atomic assignment/approval/audit persistence and mutation integration remain
pending; no assignment save endpoint is enabled by this change.

## Internal atomic assignment recorder

Added an owner-only, security-invoker persistence primitive for a validated
assignment decision. It reacquires the assignment scope, verifies revisions,
active qualification, expected current assignment and replacement
confirmation, then writes assignment, per-rule approval records, shift/week
revisions and audit history in the caller's transaction.

This primitive deliberately trusts a decision from a future protected
eligibility service. It is not a public authorization boundary and must not
be granted to the application role. PUBLIC and runtime execution are revoked.

Focused disposable checks prove assignment/approval persistence and complete
rollback when the final audit insert fails. Synthetic assignments are rolled
back. Source assignment records remain empty and runtime assignment writes
remain disabled.

Trusted commit integration and assignment-aware shift edits, removals and
template replacement are required before enabling runtime assignment saves.
Recovery inventory includes twenty tables and thirty-five migrations.

## Assignment-aware shift lifecycle

Shift deactivation now retires any live assignment in the same transaction
and records assignment retirement audit history. Immutable approval records
remain intact. Existing shift removal, whole-week removal and template
replacement paths deactivate shifts and therefore pass through this guard.

Changes to role or timing on an assigned active shift are temporarily denied
until the protected edit-and-revalidate service is connected. Revision-only
updates and changes to unassigned shifts retain existing behavior.

Focused disposable checks cover assignment retirement, retained approvals,
assigned timing-change denial, and rollback of both shift and assignment
changes when retirement audit insertion fails. These checks exercise the
shared database trigger; end-to-end week/template scenarios remain part of
the later assignment integration certification.

Runtime assignment creation remains disabled.
Recovery inventory includes twenty tables and thirty-six migrations.

## Protected application assignment commits

Added a CSRF-protected staff assignment endpoint. HTTP requests contain
employee, expected revisions, replacement confirmation and per-rule reasons;
client eligibility decisions are rejected.

The trusted application server locks current inputs, evaluates eligibility
using saved policy, validates hard blocks and exact warning approvals, and
commits assignment, private approvals, revisions and audit history in one
read-committed transaction.

A database bridge rechecks authorization, revisions and the evaluated input
fingerprint before invoking the owner-only recorder. The fingerprint detects
changed inputs; it is not a signature proving rule correctness. The application
service is trusted to compute eligibility, while PUBLIC and direct runtime
recorder/table access remain denied.

Focused disposable HTTP checks cover eligible saves, stale requests, fresh
overlap rejection, required warning reasons, approval persistence, CSRF/origin
and staff authorization, and full rollback on final audit failure.

No source employee assignments are created by certification.
Browser assignment controls, explicit unassignment, assigned shift editing,
affected-assignment review and mixed-policy reconciliation remain pending.
Recovery inventory includes twenty tables and thirty-seven migrations.

## Current assignee display and explicit unassignment

Week-detail HTTP responses now include a separate current-assignment summary
with employee identity and assignment revision. Private approval reasons and
rule details are excluded. The underlying shift-pattern read remains
unchanged, so template and copy snapshots do not acquire assignee details.

Added CSRF-protected explicit unassignment with expected assignment, shift,
week and location revisions. Unassignment retires the assignment, advances
schedule revisions and writes audit history atomically. The shift and
immutable approval records are retained.

Focused disposable HTTP checks cover current-assignee projection, stale
unassignment denial, required CSRF, successful removal and full rollback
when the final unassignment audit insert fails.

Browser assignment controls and the remaining assignment review/edit
integration are pending. No source assignments are changed by certification.
Recovery inventory includes twenty tables and thirty-eight migrations.

## Browser assignment controls

Saved shift windows now include employee candidate loading, grouped
eligibility, blocked-rule explanations, per-warning confirmation and reason
fields, explicit replacement confirmation, and unassignment.

Controls use the protected candidate/save/unassign APIs and refresh the week
after confirmed writes. Assignment changes participate in unsaved-change
prompts. Raw personal blocked intervals are not displayed.

Week summaries distinguish total and assigned shift counts. Shift cards
remain job-type-only. Existing pattern/template reads remain separate from
assignee summaries.

Static asset checks passed. Owner browser acceptance remains pending.

## Separate shift planning and assignment design

Approved separating Schedule Planner from Assign Shifts. The assigner will
show seven aligned business-day columns and only start-time rows containing
existing shifts anywhere in that week. Cards show job type and employee
name, or Unassigned, with job-type then finish-time sorting.

The two editors will be mutually exclusive in the browser. Assignment
controls will move out of the shift-definition editor. Existing business-day
rollover and Next day labels remain applicable.

Added the backend for Clear All Assignments on a selected draft week.
Clearing preserves all shifts, retires current assignments and retains
immutable approval history. Individual and summary audit writes share one
transaction; a final summary failure rolls back the entire operation.

Focused disposable checks cover two-assignment clearing, stale revisions,
CSRF, retained shifts/history, final-audit rollback and an empty no-op.
No source assignments are cleared by certification.

Separate assigner UI, editor exclusivity and confirmation controls are next.
Recovery inventory includes twenty tables and thirty-nine migrations.

## Separate assignment browser view

Added Assign Shifts beside Schedule Planner for the selected draft week.
The assigner uses seven aligned business-day columns and only start-time
rows present among the week's existing shifts. Cards show job type and
employee name or Unassigned, sorted by configured job order and finish time.
Early-morning rows retain Next Day labels and saved rollover ordering.

Assignment controls were removed from the shift-definition window and
moved to the assigner's shift cards. The manager disables outer week/template
actions while any editor is open, and explicit guards prevent opening the
other editor. Closing an editor refreshes calendar revisions.

Added a confirmed Clear All Assignments action that calls the atomic clear
API and preserves the shift pattern. Assignment drafts participate in
unsaved-change prompts. Static asset checks passed; owner browser acceptance
is pending.

## Assignment window simplification

Opening an assignment card now loads candidate employees automatically.
Removed manual Load employees and Reload employees buttons.

Week-detail responses include the current location revision so an existing
assignment can be unassigned immediately without depending on candidate-list
loading. Unassign is outside the candidate-loading fieldset. Save and
unassign requests retain server-side authorization and revision checks.

Candidate-load failures offer reopening the shift to retry. Existing
viewport observation accommodates changes in window content.

## Shared floating confirmations

Replaced native in-app confirmation prompts with a shared asynchronous
dialog positioned beside the active control and constrained to the visible
viewport. This covers assignment replacement/unassignment, schedule and
template actions, shift deletion, job-order discard and planner discard.

Cancel receives initial focus; Escape cancels. Confirmations opened within
another dialog use a separate modal layer. Removing the underlying view
cancels the pending confirmation. User text is rendered with textContent.

All affected JavaScript modules passed assistant-side syntax checks.
A focused simulation covered screen-edge positioning, acceptance,
cancellation, duplicate prevention and owner-removal cleanup.
Owner browser acceptance remains pending.

Browser-controlled tab-close/reload warnings remain unchanged.

## Administrator rest-policy browser controls

Added Rest between shifts to administrator Team settings. Ending-time window
and elapsed rest duration use fifteen-minute dropdowns, with a live preview
and the approved defaults.

Saving uses the protected rest-policy API and shared location revision.
Save is disabled while a scheduling, assigning or template editor is open.
Successful saves refresh Team and the schedule calendar.

The server still requires assignment review before changing policy over
live assignments. No policy values are changed by this update.
Browser acceptance remains pending.

## Preserving assignment reassessment

Added pure reassessment of existing assignments under fresh availability,
qualification, active-state and neighboring-assignment inputs, optionally
using a proposed rest policy.

Results identify hard conflicts, soft warnings requiring approval review,
and mixed saved business-day policies needing reconciliation. Existing
assignments remain intact. Previous approvals are not silently extended
to changed policy or availability circumstances.

Focused tests cover stricter rest before and after a shift, changed
availability and qualifications, frozen precedence, combined hours,
mixed-policy flags, stable batches and preservation of input records.

Persistent review records, policy-change integration and review display
remain pending. This change performs no database writes.

## Persistent assignment recheck markers

Added private immutable assignment-review history. Dependency changes queue
pending rechecks for affected live draft assignments: effective availability,
qualifications, employee/role active state, rest policy and neighboring
assignment changes.

Availability markers use the actual calendar dates touched by a shift.
Existing assignments and immutable approvals remain intact. Review markers
share the dependency mutation transaction, so failed changes cannot leave
orphaned review events.

Focused disposable checks cover qualification, employee status, rest-policy
and availability markers, preserved assignment values, rollback, immutable
history and runtime table access denial. Further end-to-end integration
will cover the remaining trigger paths.

No source assignments or policies are modified by certification.
Review evaluation persistence and display are next.
Recovery inventory includes twenty-one tables and forty migrations.
