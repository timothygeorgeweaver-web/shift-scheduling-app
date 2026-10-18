# Database migrations

SQL migrations are ordered, immutable changes.
Keep migration files in Git and record their SHA256 in schema_migration.
Do not edit an applied migration; add another migration.

## First migration

0001_organizations_locations creates:
- schema_migration: applied version, checksum, timestamp and operator.
- organization: business ownership boundary.
- location: organization membership, name and time zone.

No business records or scheduling-policy defaults are inserted.
The application role has no access to these tables yet.
Authorized APIs and tenant isolation must accompany future access grants.
Location input must validate the time zone before creating records.

## Execution

Run using the database administrator, with SET LOCAL ROLE
shift_scheduling_owner inside a transaction.
Supply migration_sha256 as a psql variable.
Rehearse with ROLLBACK before applying with COMMIT.
This first migration expects an empty scheduling schema and must run once.
A general pending-migration runner will follow as migrations grow.

The initial empty-schema preflight and transactional rehearsal protect
this first installation. Backup and restore procedures are still pending.
