"""Administrator authorization scoped to the requested business location."""

from contextlib import contextmanager

from fastapi import HTTPException

from app.staff_data import staff_connection


@contextmanager
def administrator_connection(token, location_id, *, write=False):
    """Yield one transaction and its authorized location and actor.

    Callers must keep account mutations and their audit event inside this
    transaction. This helper grants no additional database permissions.
    """
    with staff_connection(token, write=write) as conn:
        location = conn.execute(
            """
            SELECT id, organization_id
            FROM scheduling.location WHERE id = %s
            """,
            (location_id,),
        ).fetchone()
        if not location:
            raise HTTPException(
                status_code=404, detail="Location was not found."
            )

        actor = conn.execute(
            """
            SELECT u.id
            FROM scheduling.auth_session s
            JOIN scheduling.app_user u ON u.id = s.user_id
            JOIN scheduling.organization_member m ON m.user_id = u.id
            JOIN scheduling.member_permission p
              ON p.organization_id = m.organization_id
             AND p.user_id = m.user_id
            WHERE s.token_sha256 =
                  current_setting('scheduling.session_sha256', true)
              AND s.revoked_at IS NULL
              AND s.expires_at > now()
              AND u.active AND m.active
              AND m.organization_id = %s
              AND p.permission = 'administrator'
            """,
            (location["organization_id"],),
        ).fetchone()
        if not actor:
            raise HTTPException(
                status_code=403,
                detail="Administrator access is required.",
            )
        yield conn, location, actor["id"]
