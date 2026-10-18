# Stage 1 review — October 18, 2026

Source: Shift Scheduling App V1 Plan, version 1.1, section 12.
Stage 1 covers the project, PostgreSQL migrations, authentication/roles,
tenant/location/role/employee administration and a test deployment.

## Implemented and verified

- Chromebook-local FastAPI/PostgreSQL application with health/readiness.
- Business isolation, restricted runtime database role and row security.
- Administrator bootstrap and business/location creation during setup.
- Browser sign-in, refresh persistence and sign-out.
- Employee and job-role creation, editing and active/inactive status.
- Employee role qualifications.
- Audited employee sign-in creation and profile linking.
- Administrator-only business access suspension/reactivation and permissions.
- Session revocation following account access changes.
- Stale-edit handling, atomic audit rollback and last-administrator protection.
- Real database checks in disposable copies.
- User-confirmed browser checks for staff and linked-account workflows.

## Still open

Business/location administration currently relies on setup tooling.
An administration screen for editing business/location details is outstanding.
The current location timezone is America/Los_Angeles.

Week-start and availability-cutoff settings belong with the availability
workflow. Their proposed defaults in the V1 plan still require an explicit
owner decision before implementation.

The bootstrap administrator is not necessarily linked to an employee profile.
Linking existing sign-ins for personal employee workflows remains to be addressed.

Password recovery/invitations are an open operational mechanism in the plan.
They have not been implemented.

## Recovery and delivery

The checkpoint tooling now targets the seven-migration, eleven-table baseline
and compares row-security policies, functions and full privilege inventories.
A checkpoint is accepted only after CHECKPOINT_RESULT=PASS.

Testing is local on the Chromebook with a manually started server.
Hosted deployment, automatic startup, encrypted/off-device backups and the
full scheduling pilot remain later operational work.

Stage 1 is not marked complete. Remaining administration work must be
resolved explicitly; successful staff/account tests do not close those gaps.

## Location administration implementation update

Business/location name and timezone administration now have a controlled
database function, API, browser form and disposable-database integration tests.
Migration 0008 is applied locally. Browser verification is still pending.
This supersedes the earlier statement that editing location details depends
entirely on setup tooling. Other listed gaps remain open.

## Verified handoff to availability

The owner confirmed all browser business/location settings checks passed,
including persistence, invalid timezone rejection and audit history.
Week-start and cutoff defaults have now been explicitly approved and are
recorded in decisions.md.

Stage 2 starts with shared calendar/cutoff logic. Existing-account profile
linking and password recovery remain tracked limitations; the bootstrap
administrator can administer the app without an employee profile.

The checkpoint tool now targets eight migrations. The block creating this
handoff refreshes the independently restored checkpoint; acceptance requires
CHECKPOINT_RESULT=PASS in its output.
