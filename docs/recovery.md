# Local recovery checkpoints

Stop the application, activate its virtual environment, then run:

    python scripts/create_recovery_checkpoint.py

The script uses the existing passwordless PostgreSQL administration commands.
It saves a private checkpoint under ~/shift-scheduling-evidence/checkpoints.

Each checkpoint contains a custom database dump, restricted local configuration,
a Git bundle, recovery tooling, verification inventories and a result report.
These contain private data and credentials. Do not upload or commit them.

The current baseline is fourteen tables and eleven reviewed migrations.
Database migration checksums must match the repository migration files.

Every checkpoint restores independently into a new, uniquely named database.
Verification compares table data, ownership, columns, constraints, indexes,
row-security flags/policies, function definitions/owners/execution grants,
database/schema/table/column ACLs and application role security attributes.
Database ACLs are explicitly recreated on the new recovery database.

The source database must remain unchanged. The recovery copy is retained
with its default transaction mode set to read-only after comparison.
Role attributes are inventoried; cluster roles and their credentials are not
recreated. This is a same-cluster restore test, not a clean-server rebuild.

Commit the tooling before running the checkpoint so the Git bundle contains
the exact recovery implementation. Configuration, database-dump and Git-bundle
checksums are recorded. The bundled code still requires its documented Python
environment; dependency package binaries are not included.

Off-device storage, encrypted archives, retention and clean-server recovery
remain operational work for the later deployment/pilot stage.

If a step fails, preserve the checkpoint directory and recovery database.
Do not drop or overwrite them. A checkpoint is verified only when
CHECKPOINT_RESULT=PASS is printed.
