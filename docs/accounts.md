# Accounts and employee foundation

A user identity is separate from an employee profile.
Users can belong to businesses through organization_member.
Permissions attach to business membership and may overlap.
Employee profiles and job roles belong to a specific business location.
Qualifications connect employees to job roles within that same location.

An employee profile can exist before it is linked to a user account.
A user with no password hash cannot use password authentication.
Account, membership, employee and role active states must be checked
by their relevant authentication and scheduling services.

Composite foreign keys reject cross-business or cross-location links.
These constraints protect relationships; they do not implement API
authorization or prevent cross-business reads on their own.

The application role still has no access to these tables.
Authentication, access grants, tenant isolation, sessions and
administration interfaces remain pending.

## Database verification

scripts/verify_accounts_foundation.sql tests valid membership,
overlapping permissions and qualifications, plus rejection of:
- Qualifications referencing another business's role.
- Employee-account links without business membership.
- Unknown permissions.
- Duplicate qualifications.

Run inside a transaction as shift_scheduling_owner.
Its fixtures are rolled back to a savepoint.
