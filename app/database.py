"""Database connection and connectivity checks."""

import os
from pathlib import Path

from dotenv import dotenv_values
import psycopg

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class DatabaseUnavailable(RuntimeError):
    """Safe public failure without credentials or raw driver errors."""


def database_url() -> str:
    value = os.environ.get("SHIFT_SCHEDULING_DATABASE_URL")
    if value is None:
        value = dotenv_values(
            ENV_FILE, interpolate=False
        ).get("SHIFT_SCHEDULING_DATABASE_URL")
    if not value:
        raise DatabaseUnavailable("Database is unavailable.")
    return value


def check_database() -> None:
    """Verify connectivity only; schema readiness is added with migrations."""
    try:
        url = database_url()
        with psycopg.connect(
            url,
            connect_timeout=3,
            options="-c statement_timeout=3000",
        ) as connection:
            connection.read_only = True
            row = connection.execute("SELECT 1").fetchone()
            if row != (1,):
                raise DatabaseUnavailable("Database is unavailable.")
    except (psycopg.Error, ValueError):
        raise DatabaseUnavailable("Database is unavailable.") from None
