# Linked employee account access management

Administrators can expand a linked employee and select Manage sign-in access.
They can suspend/reactivate the business membership and choose employee,
scheduler and administrator permissions. At least one permission is required.

The account's global active flag is not changed. Business suspension affects
that business membership. All existing sessions for the affected user are
revoked on an access change, including sessions used for other businesses.
The user must sign in again. Reactivation does not revive old sessions.

Employee profile active status and account sign-in access are separate.
These controls operate on accounts linked to employee profiles. The original
bootstrap administrator may not yet have an employee profile.

GET and PATCH use administrator checks for the requested business. PATCH
requires CSRF validation and the current membership revision.
The database independently repeats authorization, serializes access edits
within the business, and protects its last active administrator.

Membership, permissions, session revocation and audit history commit together.
The runtime role receives only execution permission on the controlled function,
rather than general membership or permission write grants.

Integration checks cover suspension, reactivation, permission changes,
session revocation, authorization, validation, stale edits, audit rollback,
last-administrator protection and simultaneous administrator demotions.

Password reset/change is not included in these controls.
Browser access-management verification remains pending.
