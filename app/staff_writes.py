"""Transactional staff edits with optimistic revisions and atomic audit."""

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from psycopg import sql
from psycopg.types.json import Jsonb

from app.staff_data import staff_connection

_TARGETS = {
    "employees": ("employee", "display_name"),
    "roles": ("job_role", "name"),
}


def _location(conn, location_id):
    row = conn.execute(
        "SELECT id, organization_id FROM scheduling.location WHERE id = %s",
        (location_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Location was not found.")
    return row


def _stale():
    raise HTTPException(
        status_code=409,
        detail="This record changed. Refresh it before saving again.",
    )


def _audit(conn, location, action, entity_type, entity_id, before, after):
    actor = conn.execute(
        """
        SELECT user_id FROM scheduling.auth_session
        WHERE token_sha256 =
              current_setting('scheduling.session_sha256', true)
        """
    ).fetchone()
    if not actor:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    conn.execute(
        """
        INSERT INTO scheduling.audit_event(
            organization_id, location_id, actor_user_id, action,
            entity_type, entity_id, before_data, after_data
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            location["organization_id"], location["id"], actor["user_id"],
            action, entity_type, entity_id,
            Jsonb(jsonable_encoder(before)) if before is not None else None,
            Jsonb(jsonable_encoder(after)),
        ),
    )


def _columns(name_column):
    return sql.SQL("id, {}, active, revision{}").format(
        sql.Identifier(name_column),
        sql.SQL(", display_order" if name_column == "name" else ""),
    )


def create_record(token, location_id, kind, body):
    table, name_column = _TARGETS[kind]
    with staff_connection(token, write=True) as conn:
        location = _location(conn, location_id)
        row = conn.execute(
            sql.SQL(
                "INSERT INTO {} (organization_id,location_id,{}{}) "
                "VALUES (%s,%s,%s{}) RETURNING {}"
            ).format(
                sql.Identifier("scheduling", table),
                sql.Identifier(name_column),
                sql.SQL(",display_order" if kind == "roles" else ""),
                sql.SQL(",%s" if kind == "roles" else ""),
                _columns(name_column),
            ),
            (location["organization_id"], location["id"], body.name)
            + ((body.display_order,) if kind == "roles" else ()),
        ).fetchone()
        _audit(
            conn, location, table + ".created", table, row["id"], None, row
        )
        return row


def edit_record(token, location_id, record_id, kind, body):
    table, name_column = _TARGETS[kind]
    with staff_connection(token, write=True) as conn:
        location = _location(conn, location_id)
        before = conn.execute(
            sql.SQL(
                "SELECT {} FROM {} WHERE id = %s AND location_id = %s "
                "FOR UPDATE"
            ).format(
                _columns(name_column), sql.Identifier("scheduling", table)
            ),
            (record_id, location_id),
        ).fetchone()
        if not before:
            raise HTTPException(status_code=404, detail="Record was not found.")
        if before["revision"] != body.expected_revision:
            _stale()

        row = conn.execute(
            sql.SQL(
                "UPDATE {} SET {} = %s, active = %s, revision = revision + 1{} "
                "WHERE id = %s AND location_id = %s RETURNING {}"
            ).format(
                sql.Identifier("scheduling", table),
                sql.Identifier(name_column),
                sql.SQL(", display_order = %s" if kind == "roles" else ""),
                _columns(name_column),
            ),
            (
                body.name if body.name is not None else before[name_column],
                body.active if body.active is not None else before["active"],
            ) + ((
                body.display_order if body.display_order is not None
                else before["display_order"],
            ) if kind == "roles" else ()) + (record_id, location_id),
        ).fetchone()
        _audit(
            conn, location, table + ".updated", table, row["id"], before, row
        )
        return row


def set_qualification(token, location_id, employee_id, role_id, body):
    with staff_connection(token, write=True) as conn:
        location = _location(conn, location_id)
        employee = conn.execute(
            """
            SELECT id FROM scheduling.employee
            WHERE id = %s AND location_id = %s FOR UPDATE
            """,
            (employee_id, location_id),
        ).fetchone()
        role = conn.execute(
            "SELECT id FROM scheduling.job_role WHERE id = %s AND location_id = %s",
            (role_id, location_id),
        ).fetchone()
        if not employee or not role:
            raise HTTPException(
                status_code=404, detail="Employee or role was not found."
            )

        before = conn.execute(
            """
            SELECT employee_id, job_role_id, active, revision
            FROM scheduling.employee_qualification
            WHERE location_id = %s AND employee_id = %s AND job_role_id = %s
            FOR UPDATE
            """,
            (location_id, employee_id, role_id),
        ).fetchone()

        if before is None:
            if body.expected_revision != 0:
                _stale()
            row = conn.execute(
                """
                INSERT INTO scheduling.employee_qualification(
                    organization_id, location_id, employee_id, job_role_id, active
                ) VALUES (%s,%s,%s,%s,%s)
                RETURNING employee_id, job_role_id, active, revision
                """,
                (
                    location["organization_id"], location_id,
                    employee_id, role_id, body.active,
                ),
            ).fetchone()
            action = "qualification.created"
        else:
            if before["revision"] != body.expected_revision:
                _stale()
            row = conn.execute(
                """
                UPDATE scheduling.employee_qualification
                SET active = %s, revision = revision + 1
                WHERE location_id = %s AND employee_id = %s AND job_role_id = %s
                RETURNING employee_id, job_role_id, active, revision
                """,
                (body.active, location_id, employee_id, role_id),
            ).fetchone()
            action = "qualification.updated"

        _audit(
            conn, location, action, "employee_qualification",
            employee_id, before, row,
        )
        return row


def history(token, location_id, limit):
    with staff_connection(token) as conn:
        _location(conn, location_id)
        return conn.execute(
            """
            SELECT id, actor_user_id, action, entity_type, entity_id,
                   before_data, after_data, created_at
            FROM scheduling.audit_event
            WHERE location_id = %s
            ORDER BY created_at DESC, id DESC LIMIT %s
            """,
            (location_id, limit),
        ).fetchall()
