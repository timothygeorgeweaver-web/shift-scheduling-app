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
