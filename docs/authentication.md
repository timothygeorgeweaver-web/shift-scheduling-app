# Authentication implementation

## Implemented utilities

Passwords use Argon2id through pwdlib's recommended hasher.
Each new hash receives a fresh salt.
Passwords are never trimmed, normalized or silently truncated.
The utility accepts nonempty passwords up to 1024 characters.
Account creation strength requirements will be defined in its workflow.

Missing hashes and malformed stored hashes reject verification.
Do not log passwords, password hashes or connection credentials.

Session secrets contain 256 random bits generated with Python secrets.
Only a SHA256 digest is intended for database storage.
The raw secret belongs in a protected browser cookie, never a URL.
Fast SHA256 is appropriate for these high-entropy random tokens;
human passwords use Argon2id instead.

## Still pending

Database-backed sessions, expiry and revocation.
Sign-in, sign-out and authenticated identity endpoints.
Cookie configuration, CSRF protection and login throttling.
Business-scoped authorization and account administration.
First-administrator setup and the sign-in interface.

These utilities alone do not make the application authenticated.
No real accounts or sessions have been created.

## Authentication API implementation

POST /api/v1/auth/login accepts JSON email and password.
GET /api/v1/auth/me returns the signed-in identity and its active memberships.
POST /api/v1/auth/logout requires the X-CSRF-Token returned by login or me.

Sessions expire after eight hours and store only the session-token digest.
Each identity lookup checks session expiry, revocation, user active status
and active business membership. Permissions are read fresh.
Invalid credentials use one generic response.
Request validation does not echo submitted credentials.

Cookies are HttpOnly, SameSite Strict, host-only and use path /.
They use Secure when served over HTTPS. HTTP is for localhost development;
hosted deployment must enforce HTTPS and verify proxy configuration.
No CORS access is enabled. Browser mutation origins are checked.

The local server limits each client address to ten login attempts per minute.
This in-memory limit is bounded, resets on restart and is per process.
A shared, deployment-appropriate limiter is required before multi-worker
or public hosting. Do not trust forwarded client-address headers blindly.

API tests cover response behavior, cookies, CSRF, origins and throttling
with stubbed persistence. Real authenticated database round trips, expiry,
revocation and cross-business authorization still need integration checks
after the first-administrator setup.

The runtime role can read authentication records and create/revoke sessions.
It cannot modify users, permissions or employee records.
Authentication tables are not exposed through generic CRUD endpoints.
Personnel administration and business-scoped authorization are still pending.
