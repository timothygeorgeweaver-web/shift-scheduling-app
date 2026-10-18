# Staff editing

Implemented APIs:
- POST /api/v1/staff/locations/{location_id}/employees
- PATCH /api/v1/staff/locations/{location_id}/employees/{employee_id}
- POST /api/v1/staff/locations/{location_id}/roles
- PATCH /api/v1/staff/locations/{location_id}/roles/{role_id}
- PUT /api/v1/staff/locations/{location_id}/employees/{employee_id}/qualifications/{role_id}
- GET /api/v1/staff/locations/{location_id}/history

Create requests accept name.
Employee responses use display_name; role responses use name.
Patch requests require expected_revision and name and/or active.
Qualification requests require active and expected_revision.
For a new qualification, expected_revision is zero.
For an existing qualification, use its returned revision.
Extra request fields are rejected.

Each mutation requires a signed-in staff session, a valid origin and
X-CSRF-Token from the authenticated identity endpoint.
Administrators and schedulers manage staff within their active businesses.
Employee-only accounts cannot manage staff.
Account and permission administration remain separate, unimplemented APIs.

Staff mutations lock the affected record, check the expected revision,
increment it and append an audit event in one database transaction.
Qualification changes also lock the employee to serialize first-time inserts.
A stale revision returns HTTP 409; the client must refresh before retrying.
Deactivation preserves records and qualification history.
Eligibility must later evaluate employee, role and qualification active flags.

Audit events preserve actor, action, entity, before/after values and time.
Audit snapshots contain only explicit staff fields, not credentials.
The application role cannot update or delete audit history.
Failed audits roll back the staff mutation.
No staff deletion or ownership-changing endpoint is provided.

History returns at most 100 events, newest first; default limit is 50.
Staff API responses are marked no-store.

## Verification

Run the full real-database harness with the app stopped:

    python -W error scripts/verify_staff_read_isolation.py

It invokes scripts/verify_staff_write_api.py inside its temporary copy.
Checks include create/edit/deactivate/reactivate, qualifications,
CSRF and cross-business denial, stale revisions, two concurrent edits,
audit actor/history checks and rejection of audit updates.

A synthetic failing audit trigger is installed only in the temporary copy.
The failed request must leave neither its staff record nor an audit event.
The original application database must remain unchanged.
The copy is removed on success and retained on failure.

The browser staff management interface remains pending.
The independently restored baseline checkpoint remains available.
The recovery tool baseline/security-object checks must be updated
before the next checkpoint for the expanded schema.
