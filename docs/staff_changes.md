# Staff change foundation

Employees, job roles and qualifications now have positive revision numbers.
Editing APIs must require the expected revision, lock the affected record,
reject a mismatch and increment the revision atomically.
The fields alone do not implement stale-edit rejection.

Audit events record business, location, actor, action, entity,
before/after snapshots and timestamp.
The application role can append and read authorized events.
It cannot update or delete audit events.
The actor must match the session used for the database transaction.
Audit snapshots must exclude passwords, hashes and session secrets.

Database write policies use the same business-scoped staff permissions:
administrators and schedulers can manage staff profiles and qualifications.
Employee-only accounts cannot manage staff.
Account creation and permission administration require their own design
and authorization; these grants do not permit either.

Runtime column grants exclude employee IDs, ownership changes,
employee-account linking and audit timestamp manipulation.
Staff records are deactivated rather than deleted.

Editing services must save the staff mutation and audit event in the
same transaction. Database grants alone do not enforce that pairing.
Mutation endpoints, CSRF checks, stale-edit handling, atomic audit
verification and editing forms are the next slice.

## Verification

scripts/verify_staff_write_foundation.sql runs within an owner transaction.
It switches to the runtime role to test permitted staff writes and denial of:
- Cross-business writes and employee ownership changes.
- Employee-only staff writes.
- Invalid revision values.
- Forged audit actors.
- Audit updates and deletions.
- Reads without session context.

All fixtures are rolled back to a savepoint.

The existing independently restored checkpoint remains the recovery point.
It contains migration 0003; later committed migrations can be replayed.
Update the checkpoint tool's baseline and security-object verification
before creating the next checkpoint after these schema changes.
