"""Authentication persistence. Never return password hashes to API callers."""

from contextlib import contextmanager

from fastapi import HTTPException
import psycopg

from app.database import DatabaseUnavailable, database_url
from app.security import (
    hash_password,
    new_session_token,
    session_token_digest,
    verify_password,
)

_DUMMY_HASH = hash_password("Internal dummy verification value only.")


@contextmanager
def connection():
    try:
        with psycopg.connect(
            database_url(),
            connect_timeout=3,
            options="-c statement_timeout=5000",
        ) as conn:
            yield conn
    except (psycopg.Error, DatabaseUnavailable, ValueError):
        raise HTTPException(
            status_code=503, detail="Authentication is unavailable."
        ) from None


def create_session(email: str, password: str) -> str | None:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT id, password_hash, active
            FROM scheduling.app_user WHERE email = %s
            """,
            (email.strip().lower(),),
        ).fetchone()

        stored = row[1] if row and row[1] else _DUMMY_HASH
        verified = verify_password(password, stored)
        if not row or not row[1] or not row[2] or not verified:
            return None

        token = new_session_token()
        inserted = conn.execute(
            """
            INSERT INTO scheduling.auth_session(
                token_sha256, user_id, expires_at
            )
            SELECT %s, u.id, now() + interval '8 hours'
            FROM scheduling.app_user u
            WHERE u.id = %s AND u.active
              AND EXISTS (
                  SELECT FROM scheduling.organization_member m
                  WHERE m.user_id = u.id AND m.active
              )
            RETURNING token_sha256
            """,
            (session_token_digest(token), row[0]),
        ).fetchone()

        return token if inserted else None


def identity(token: str) -> dict | None:
    digest = session_token_digest(token)
    with connection() as conn:
        conn.read_only = True
        row = conn.execute(
            """
            SELECT u.id, u.email
            FROM scheduling.auth_session s
            JOIN scheduling.app_user u ON u.id = s.user_id
            WHERE s.token_sha256 = %s
              AND s.revoked_at IS NULL
              AND s.expires_at > now()
              AND u.active
              AND EXISTS (
                  SELECT FROM scheduling.organization_member m
                  WHERE m.user_id = u.id AND m.active
              )
            """,
            (digest,),
        ).fetchone()
        if not row:
            return None

        memberships = conn.execute(
            """
            SELECT m.organization_id, p.permission
            FROM scheduling.organization_member m
            LEFT JOIN scheduling.member_permission p
              ON p.organization_id = m.organization_id
             AND p.user_id = m.user_id
            WHERE m.user_id = %s AND m.active
            ORDER BY m.organization_id, p.permission
            """,
            (row[0],),
        ).fetchall()

        grouped = {}
        for organization_id, permission in memberships:
            permissions = grouped.setdefault(str(organization_id), [])
            if permission is not None:
                permissions.append(permission)

        return {
            "user_id": str(row[0]),
            "email": row[1],
            "memberships": [
                {"organization_id": key, "permissions": value}
                for key, value in grouped.items()
            ],
        }


def revoke_session(token: str) -> None:
    with connection() as conn:
        conn.execute(
            """
            UPDATE scheduling.auth_session
            SET revoked_at = COALESCE(revoked_at, now())
            WHERE token_sha256 = %s
            """,
            (session_token_digest(token),),
        )
