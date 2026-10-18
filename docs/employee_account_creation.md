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
