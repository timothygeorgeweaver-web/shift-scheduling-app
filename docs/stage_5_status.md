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

Before enabling publication, confirm whether unassigned shifts may be published.
The saved V1 plan proposes allowing them with visible Unfilled status; this
remains a proposal rather than a recorded owner decision.
