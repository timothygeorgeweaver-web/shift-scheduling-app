# Account administration

Account administration requires an active administrator permission for the
specific business owning the requested location. Scheduler permission alone
does not authorize account creation, password changes, or access management.

The administrator transaction helper uses the existing live session checks
and row security, then verifies administrator membership in the target
business. An administrator from another business cannot access its location.

The helper exposes the authenticated actor ID for future atomic audit writes.
It grants no new database privileges and adds no account mutation endpoints.

The disposable PostgreSQL integration harness verifies each business's
administrator access, scheduler and employee denial, cross-business denial,
and read-only transaction behavior.

Next: implement audited account creation and employee linking, then account
access controls and the administrator browser interface. Existing accounts
and employee links remain unchanged by this foundation step.
