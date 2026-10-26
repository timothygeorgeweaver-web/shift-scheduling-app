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
