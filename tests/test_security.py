import pytest

from app import security


def test_password_hashes_are_salted_and_verify():
    password = "Example passphrase for verification only."
    first = security.hash_password(password)
    second = security.hash_password(password)

    assert first.startswith("$argon2id$")
    assert first != second
    assert password not in first
    assert security.verify_password(password, first)
    assert security.verify_password(password, second)
    assert not security.verify_password("Different passphrase.", first)


def test_password_whitespace_and_unicode_are_preserved():
    password = "  Café scheduling passphrase 🔐  "
    stored = security.hash_password(password)

    assert security.verify_password(password, stored)
    assert not security.verify_password(password.strip(), stored)
    assert not security.verify_password(
        "  Cafe\u0301 scheduling passphrase 🔐  ", stored
    )


@pytest.mark.parametrize("stored", [
    None,
    "",
    "not-a-password-hash",
    "$argon2id$broken",
])
def test_missing_or_malformed_hash_rejects_login(stored):
    assert not security.verify_password("Example passphrase.", stored)


@pytest.mark.parametrize("password", ["", "x" * 1025])
def test_invalid_password_length_is_rejected_before_hashing(monkeypatch, password):
    class ForbiddenHasher:
        def hash(self, value):
            pytest.fail("Invalid input must not reach password hashing")

        def verify(self, value, stored):
            pytest.fail("Invalid input must not reach password verification")

    monkeypatch.setattr(security, "_PASSWORD_HASH", ForbiddenHasher())

    with pytest.raises(ValueError, match="Password length is invalid"):
        security.hash_password(password)

    assert not security.verify_password(password, "irrelevant-hash")


def test_session_secret_and_stored_digest_are_distinct():
    first = security.new_session_token()
    second = security.new_session_token()

    assert first != second
    digest = security.session_token_digest(first)
    assert digest != first
    assert len(digest) == 64
    assert digest == security.session_token_digest(first)
    assert digest != security.session_token_digest(second)


@pytest.mark.parametrize("token", [
    "",
    "a" * 63,
    "a" * 65,
    "Z" * 64,
    "../unexpected-token",
])
def test_malformed_session_tokens_are_rejected(token):
    with pytest.raises(ValueError, match="Session token is invalid"):
        security.session_token_digest(token)
