# Local recovery checkpoints

Run with the application stopped and virtual environment active:

    python scripts/create_recovery_checkpoint.py

The script uses PostgreSQL administration commands through the existing
sudo configuration. It never prompts for a sudo password.

It saves under ~/shift-scheduling-evidence/checkpoints:
- A custom-format PostgreSQL database dump.
- A copy of the local configuration, with restricted permissions.
- A Git bundle of the current committed application code.
- The checkpoint script and verification inventories.

Checkpoint files contain private data and credentials.
Do not commit, upload or paste their contents.

Each run creates a uniquely named, separate recovery database.
The database is restored independently and checked against the original:
table data fingerprints, owners, columns, constraints, indexes and
application table/column permissions.
The original database must remain unchanged during verification.

The recovery database is retained with public connection access revoked
and its default transaction mode set to read-only.
The script never drops or replaces an existing database.

This checkpoint covers the local pilot's current ten-table, three-migration
baseline. Update the baseline check deliberately as the schema evolves.
These are local checkpoints; off-device backup and retention remain pending.
The Git bundle contains HEAD; new uncommitted recovery tooling is copied
separately into the checkpoint.

If any step fails, retain the checkpoint directory and recovery database.
Diagnose the exact failure before retrying or removing anything.
A checkpoint is verified only when CHECKPOINT_RESULT=PASS is printed.
