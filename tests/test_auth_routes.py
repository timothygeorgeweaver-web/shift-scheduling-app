import pytest
from fastapi.testclient import TestClient

from app import auth_routes, auth_store
from app.main import app

TOKEN = "a" * 64
LOGIN = {"email": "sample@example.invalid", "password": "Example passphrase."}


@pytest.fixture(autouse=True)
def reset_limiter(monkeypatch):
    monkeypatch.setattr(auth_routes, "limiter", auth_routes.LoginLimiter())


def test_unauthenticated_identity_is_denied():
    with TestClient(app) as client:
        response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_bad_credentials_are_generic(monkeypatch):
    monkeypatch.setattr(auth_store, "create_session", lambda *args: None)
    with TestClient(app) as client:
        response = client.post("/api/v1/auth/login", json=LOGIN)
    assert response.status_code == 401
    assert response.json() == {"detail": "Email or password is invalid."}
    assert "set-cookie" not in response.headers


def test_cookie_identity_and_csrf_logout(monkeypatch):
    monkeypatch.setattr(auth_store, "create_session", lambda *args: TOKEN)
    monkeypatch.setattr(auth_store, "identity", lambda token: {
        "user_id": "example-user",
        "email": LOGIN["email"],
        "memberships": [],
    })
    revoked = []
    monkeypatch.setattr(auth_store, "revoke_session", revoked.append)

    with TestClient(app) as client:
        signed_in = client.post("/api/v1/auth/login", json=LOGIN)
        assert signed_in.status_code == 200
        cookie = signed_in.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "max-age=28800" in cookie
        assert TOKEN not in signed_in.text

        identity = client.get("/api/v1/auth/me")
        assert identity.status_code == 200
        assert identity.headers["cache-control"] == "no-store"
        assert "password_hash" not in identity.text

        denied = client.post("/api/v1/auth/logout")
        assert denied.status_code == 403
        assert revoked == []

        signed_out = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": identity.json()["csrf_token"]},
        )
        assert signed_out.status_code == 204
        assert revoked == [TOKEN]
        assert client.get("/api/v1/auth/me").status_code == 401


def test_https_cookie_is_secure(monkeypatch):
    monkeypatch.setattr(auth_store, "create_session", lambda *args: TOKEN)
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/v1/auth/login", json=LOGIN)
    assert "secure" in response.headers["set-cookie"].lower()


def test_cross_origin_login_is_denied(monkeypatch):
    def forbidden(*args):
        pytest.fail("Cross-origin login must stop before credential processing")

    monkeypatch.setattr(auth_store, "create_session", forbidden)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json=LOGIN,
            headers={"Origin": "https://another.example"},
        )
    assert response.status_code == 403


def test_validation_does_not_echo_password():
    secret = "secret-value-" * 100
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": LOGIN["email"], "password": secret},
        )
    assert response.status_code == 422
    assert secret not in response.text


def test_unrecognized_session_is_denied(monkeypatch):
    monkeypatch.setattr(auth_store, "identity", lambda token: None)
    with TestClient(app) as client:
        client.cookies.set(auth_routes.COOKIE_NAME, TOKEN)
        response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_login_attempt_limit(monkeypatch):
    monkeypatch.setattr(auth_store, "create_session", lambda *args: None)
    with TestClient(app) as client:
        for _ in range(10):
            assert client.post("/api/v1/auth/login", json=LOGIN).status_code == 401
        limited = client.post("/api/v1/auth/login", json=LOGIN)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
