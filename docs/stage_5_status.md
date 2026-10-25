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
