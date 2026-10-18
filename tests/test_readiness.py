import pytest
from fastapi.testclient import TestClient
import psycopg

from app import database
from app import main


def test_readiness_success(monkeypatch):
    monkeypatch.setattr(main, "check_database", lambda: None)
    with TestClient(main.app) as client:
        response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


def test_database_failure_is_safe_and_health_still_works(monkeypatch):
    def unavailable():
        raise database.DatabaseUnavailable("private driver information")

    monkeypatch.setattr(main, "check_database", unavailable)
    with TestClient(main.app) as client:
        response = client.get("/api/v1/ready")
        health = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable."}
    assert "private" not in response.text
    assert health.status_code == 200


def test_missing_configuration_does_not_connect(monkeypatch):
    monkeypatch.delenv("SHIFT_SCHEDULING_DATABASE_URL", raising=False)
    monkeypatch.setattr(database, "dotenv_values", lambda *a, **k: {})

    def unexpected_connect(*args, **kwargs):
        pytest.fail("Missing configuration must not attempt a connection")

    monkeypatch.setattr(database.psycopg, "connect", unexpected_connect)
    with pytest.raises(database.DatabaseUnavailable):
        database.check_database()


def test_driver_error_does_not_expose_credentials(monkeypatch):
    monkeypatch.setenv(
        "SHIFT_SCHEDULING_DATABASE_URL",
        "postgresql://example:secret-password@localhost/example",
    )

    def failed_connect(*args, **kwargs):
        raise psycopg.OperationalError("secret-password")

    monkeypatch.setattr(database.psycopg, "connect", failed_connect)
    with pytest.raises(database.DatabaseUnavailable) as error:
        database.check_database()
    assert str(error.value) == "Database is unavailable."
