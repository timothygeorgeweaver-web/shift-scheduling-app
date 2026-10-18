from fastapi.testclient import TestClient

from app.main import app


def test_public_shell_and_local_assets_are_served():
    with TestClient(app) as client:
        page = client.get("/")
        script = client.get("/static/app.js?v=1")
        stylesheet = client.get("/static/app.css?v=1")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert script.status_code == 200
    assert stylesheet.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "text/css" in stylesheet.headers["content-type"]


def test_auth_errors_are_not_cacheable():
    with TestClient(app) as client:
        response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
