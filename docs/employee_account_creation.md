# Employee account creation — prepared, not applied

Migration 0006 prepares a narrowly scoped database function for creating
a new employee sign-in and linking it to an existing employee profile.

The function independently verifies the live session and administrator
permission for the employee's business. It requires an active, unlinked
employee and the expected employee revision.

The transaction creates the account, active business membership, employee
permission, employee link and audit event together. Any failure rolls back
all of these changes. Existing global accounts are not reused or modified.
Concurrent linking attempts serialize on the employee row.

The runtime role receives execution permission on this function, rather
than general write access to accounts, memberships or permissions.
The function has a fixed search path and explicitly qualified table names.

Only an Argon2id hash is accepted by the database function. The forthcoming
API must validate the password and hash it before calling the function.
Passwords and hashes must never appear in responses or audit records.

This operation cannot create administrators or schedulers, reset passwords,
or disable existing accounts. Those require separate controlled operations.

Before application: verify success, authorization denial, stale revisions,
duplicate emails, concurrent linking and atomic rollback in a disposable
database. Then rehearse and apply the migration, add the API, and verify
real employee sign-in before adding browser controls.

## Application status

Migration 0006 has now been rehearsed with rollback and applied locally.
The committed function owner, fixed search path, runtime execution grant,
and absence of PUBLIC execution are checked during application.
Business records and table structure are compared before and after.

The integration harness supports both installing this migration into a
disposable pre-migration copy and testing an already-migrated copy.
Account API endpoints and administrator browser controls remain pending.

## Account creation API and Team form

POST /api/v1/staff/locations/{location}/employees/{employee}/account
accepts email, password and expected_revision. It requires same-origin
CSRF validation and live administrator access for the target business.

The API normalizes the email, requires a 12–1024 character password,
hashes it with Argon2id, and calls the atomic database function.
It returns employee_id, user_id, email and revision without password data.
Validation responses do not echo submitted values.

Administrators see Create employee sign-in inside active, unlinked employee
details. The form confirms the password and clears both password inputs
when submitted. Successful creation refreshes Team and shows the linked
status. Schedulers do not receive account creation controls.

A linked sign-in is distinct from employee active status. Deactivating a
staff profile currently does not disable its account. Account suspension,
password changes and scheduler/administrator permission management remain
separate work.

Real disposable-database API checks cover authorization, CSRF, origin,
invalid input, conflicts, audit rollback, linked status, and employee
sign-in, restricted access and sign-out. Browser verification is pending.
