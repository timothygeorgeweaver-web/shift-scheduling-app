"""Staff reads with transaction-local session context and database isolation."""

from contextlib import contextmanager

from fastapi import HTTPException
from psycopg.rows import dict_row

from app.auth_store import connection
from app.security import session_token_digest


@contextmanager
def staff_connection(token: str):
    with connection() as conn:
        conn.read_only = True
        conn.row_factory = dict_row
        digest = session_token_digest(token)
        conn.execute(
            "SELECT set_config('scheduling.session_sha256', %s, true)",
            (digest,),
        )

        user = conn.execute(
            """
            SELECT u.id
            FROM scheduling.auth_session s
            JOIN scheduling.app_user u ON u.id = s.user_id
            WHERE s.token_sha256 = %s
              AND s.revoked_at IS NULL AND s.expires_at > now()
              AND u.active
              AND EXISTS (
                  SELECT FROM scheduling.organization_member m
                  WHERE m.user_id = u.id AND m.active
              )
            """,
            (digest,),
        ).fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="Sign-in is required.")

        permitted = conn.execute(
            """
            SELECT 1
            FROM scheduling.organization_member m
            JOIN scheduling.member_permission p
              ON p.organization_id = m.organization_id
             AND p.user_id = m.user_id
            WHERE m.user_id = %s AND m.active
              AND p.permission IN ('administrator', 'scheduler')
            LIMIT 1
            """,
            (user["id"],),
        ).fetchone()
        if not permitted:
            raise HTTPException(status_code=403, detail="Staff access is required.")
        yield conn


def locations(token: str):
    with staff_connection(token) as conn:
        return conn.execute(
            """
            SELECT l.id, l.organization_id, l.name, l.timezone,
                   o.name AS organization_name
            FROM scheduling.location l
            JOIN scheduling.organization o ON o.id = l.organization_id
            ORDER BY lower(o.name), lower(l.name), l.id
            """
        ).fetchall()


def location_records(token: str, location_id, kind: str):
    queries = {
        "employees": """
            SELECT id, display_name, active FROM scheduling.employee
            WHERE location_id = %s ORDER BY lower(display_name), id
        """,
        "roles": """
            SELECT id, name, active FROM scheduling.job_role
            WHERE location_id = %s ORDER BY lower(name), id
        """,
        "qualifications": """
            SELECT employee_id, job_role_id, active
            FROM scheduling.employee_qualification
            WHERE location_id = %s ORDER BY employee_id, job_role_id
        """,
    }
    with staff_connection(token) as conn:
        location = conn.execute(
            "SELECT id FROM scheduling.location WHERE id = %s",
            (location_id,),
        ).fetchone()
        if not location:
            raise HTTPException(status_code=404, detail="Location was not found.")
        return conn.execute(queries[kind], (location_id,)).fetchall()
