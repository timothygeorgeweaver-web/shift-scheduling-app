# Staff access

Implemented read endpoints:
- GET /api/v1/staff/locations
- GET /api/v1/staff/locations/{location_id}/employees
- GET /api/v1/staff/locations/{location_id}/roles
- GET /api/v1/staff/locations/{location_id}/qualifications

Administrators and schedulers can read staff for their active businesses.
Employee-only accounts cannot use these management endpoints.
The employee published-roster view will be a separate API.

Each read uses a transaction-local session-token digest.
PostgreSQL policies independently check the live session, account,
membership and administrator/scheduler permission for each business.
Missing context exposes no staff rows. Context resets after the transaction.
The runtime role remains unable to create, edit or delete staff records.

Authentication tables retain their restricted service-level access:
they are not covered by the staff policies and have no generic CRUD API.
The application service and its database credentials are trusted.
Database owners and superusers retain administrative access.

## Integration verification

With the application stopped:

    python scripts/verify_staff_read_isolation.py

This creates a uniquely named database copy, adds synthetic identities there,
and tests the real APIs using the runtime database role.
It also tests broad SQL row isolation, transaction context reset,
cross-business IDs, employee-only denial, expiry, revocation,
account deactivation and membership deactivation.

The original application database must remain unchanged.
The temporary copy is dropped on success and retained on failure.
Do not share retained database contents; the copy includes private data.

Staff creation/editing, qualification management, audit events and
the staff management interface are the next implementation slices.
