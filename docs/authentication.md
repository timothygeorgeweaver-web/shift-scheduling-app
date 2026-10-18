# Authentication

## Implemented

Passwords use salted Argon2id hashes through pwdlib.
Passwords are not trimmed, normalized or silently truncated.
The hashing utility accepts nonempty inputs up to 1024 characters.
First-administrator creation requires a 15-character minimum passphrase.
Missing or malformed stored hashes reject verification.

POST /api/v1/auth/login accepts JSON email and password.
GET /api/v1/auth/me returns the signed-in identity and active memberships.
POST /api/v1/auth/logout requires X-CSRF-Token from login or me.
Credential failures are generic; validation does not echo submitted inputs.

Sessions use 256-bit random secrets. Only SHA256 token digests are stored.
Sessions expire after eight hours and support explicit revocation.
Identity lookup checks expiry, revocation, active account and membership.
Permissions are read fresh. Password hashes are excluded from responses.

Cookies are HttpOnly, SameSite Strict, host-only and use path /.
They use Secure over HTTPS. HTTP is for localhost development only.
Hosted deployment must enforce HTTPS and verify proxy configuration.
No CORS access is enabled. Browser mutation origins are checked.

The local server allows ten login attempts per client address per minute.
The bounded in-memory limiter resets on restart and is per process.
Shared rate limiting is required before multi-worker or public hosting.

## First administrator

From the project root, with the virtual environment active:

    python scripts/bootstrap_administrator.py

The script requires an empty initial business/account setup.
It privately prompts for credentials and creates one business, location,
account, active membership and administrator/scheduler permissions.
It does not create an employee profile or job qualifications.

Do not paste the app password, password hash, session cookie or .env.
The email is a login identifier; this step sends no email.

Setup writes are one database transaction.
After setup commits, verification tests real sign-in, identity, CSRF,
logout, revoked-session replay and expiry using the application DB role.
Verification sessions remain as expired/revoked history.

If setup commits but verification fails, preserve the account.
After diagnosis, verification can be repeated without recreating it:

    python scripts/bootstrap_administrator.py --verify-only

## Access boundaries and remaining work

The runtime role can read authentication records and create/revoke sessions.
It cannot modify users, permissions or employee records.
Authentication tables have no generic CRUD endpoints.

The login screen, staff administration, business-scoped authorization,
account recovery, account/membership deactivation integration checks,
session-history retention, shared rate limiting and hosted transport
configuration remain pending.
