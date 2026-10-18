"""Password hashing and opaque session-token utilities."""

import hashlib
import re
import secrets

from argon2.exceptions import InvalidHashError, VerificationError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

_PASSWORD_HASH = PasswordHash.recommended()
MAX_PASSWORD_CHARACTERS = 1024
_TOKEN_PATTERN = re.compile(r"[0-9a-f]{64}")


def _valid_password_input(password: str) -> bool:
    return isinstance(password, str) and 0 < len(password) <= MAX_PASSWORD_CHARACTERS


def hash_password(password: str) -> str:
    """Hash without trimming, normalizing or truncating the password."""
    if not _valid_password_input(password):
        raise ValueError("Password length is invalid.")
    return _PASSWORD_HASH.hash(password)


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Reject missing or malformed credentials without exposing hash details."""
    if not _valid_password_input(password) or not stored_hash:
        return False
    try:
        return _PASSWORD_HASH.verify(password, stored_hash)
    except (UnknownHashError, InvalidHashError, VerificationError, ValueError):
        return False


def new_session_token() -> str:
    """Generate a 256-bit secret for the browser; never log it."""
    return secrets.token_hex(32)


def session_token_digest(token: str) -> str:
    """Return the database lookup digest, not the browser's raw secret."""
    if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None:
        raise ValueError("Session token is invalid.")
    return hashlib.sha256(token.encode("ascii")).hexdigest()
