# Staff editing of employee unavailability

Implementation in progress.

Authorized administrators and schedulers can access a selected employee's
planner through controlled database functions. The employee does not need
a linked sign-in account.

Reads are scoped to the staff member's organization and selected location.
Saves retain date revisions, atomic audit, existing cutoff and publication
locks, and assignment-review triggers. Direct private-table access remains
unavailable to the runtime role.

Staff edits record the acting staff user and the target employee separately.
This feature does not impersonate an employee or alter the employee's session.

Business-day API projection, additional authorization and rollback
certification, and the Team planner interface are next.

## Team planner interface

Each employee now has Edit Unavailability. It opens a clearly named
seven-day business-day planner with date navigation, explicit Save,
Hide/Show and Close controls.

The staff business-day API covers all eight touched calendar dates in
one transaction and preserves cells outside the displayed business week.
Cutoff and publication locks remain enforced. Staff edits retain the
acting user's identity and trigger assignment reassessment.

While this planner is open, Team controls and scheduling controls are
not interactive. Opening it is disabled while a schedule editing window
is already open. These interaction locks apply within the same browser;
server-side authorization, revision checks and date locks remain authoritative.

The initial interface supports individual week editing. Staff mass-edit
controls are not included in this addition.
