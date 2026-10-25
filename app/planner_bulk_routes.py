"""Previewed additive date-range edits with one database transaction."""

from datetime import date, timedelta
import hashlib
import hmac
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.auth_store import connection, identity
from app.planner_logic import normalize_template, preview_additions
from app.security import session_token_digest
from app.staff_write_routes import mutation_token
from app.publication_locks import changed_cells_touch_publication

router = APIRouter(prefix="/api/v1/me/planner", tags=["planner mass edit"])
LAST_FIRST_DATE = date(9998, 12, 25)


class BulkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_date: date
    last_date: date
    weekly_cells: dict[str, list[StrictInt]]

    @model_validator(mode="after")
    def valid_range_and_template(self):
        if (
            self.first_date < date(2000, 1, 1)
            or self.last_date > date(9998, 12, 31)
            or self.first_date > self.last_date
            or (self.last_date-self.first_date).days >= 366
        ):
            raise ValueError("Choose an inclusive range of at most 366 dates.")
        if any(len(cells) > 96 for cells in self.weekly_cells.values()):
            raise ValueError("Use at most 96 cells per weekday.")
        self.weekly_cells = {
            key: list(cells)
            for key, cells in normalize_template(self.weekly_cells).items()
        }
        return self


class BulkApply(BulkRequest):
    expected_preview: str = Field(pattern=r"^[0-9a-f]{64}$")


def lock_scope(conn, location):
    return conn.execute(
        "SELECT scheduling.lock_my_planner_scope(%s)", (location,),
    ).fetchone()[0]


def read_selected(conn, location, body):
    """Read selected weekdays only; keep every matching lock/revision."""
    result = {}
    count = (body.last_date-body.first_date).days+1
    for offset in range(0, count, 7):
        anchor = min(body.first_date+timedelta(days=offset), LAST_FIRST_DATE)
        week = conn.execute(
            "SELECT scheduling.read_my_planner(%s,%s)", (location, anchor),
        ).fetchone()[0]
        for row in week["days"]:
            day = date.fromisoformat(row["date"])
            if (
                body.first_date <= day <= body.last_date
                and body.weekly_cells[str(day.weekday())]
            ):
                result[day] = row
    return dict(sorted(result.items()))


def gate_state(rows):
    return {
        day: (
            row["editable"], row["lock_reason"],
            row["cutoff"], row["frozen_blocked"],
            row.get("publication_locked_windows", []),
        )
        for day, row in rows.items()
    }


def build_preview(conn, location, body, digest, profile):
    rows = read_selected(conn, location, body)
    existing = {day: row["blocked_cells"] for day, row in rows.items()}
    locked = {
        day for day, row in rows.items()
        if not row["editable"] or changed_cells_touch_publication(
            row["blocked_cells"],
            set(row["blocked_cells"]).union(body.weekly_cells[str(day.weekday())]),
            row.get("publication_locked_windows", []),
        )
    }
    plan = preview_additions(
        body.first_date, body.last_date, body.weekly_cells, existing, locked,
    )
    stamp_data = {
        "session": digest,
        "employee": profile["employee_id"],
        "location": str(location),
        "location_revision": profile["location_revision"],
        "first_date": body.first_date.isoformat(),
        "last_date": body.last_date.isoformat(),
        "weekly_cells": body.weekly_cells,
        "rows": list(rows.values()),
    }
    preview_id = hashlib.sha256(json.dumps(
        stamp_data, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    changed = [
        {
            "date": change.day.isoformat(),
            "before": list(change.before),
            "after": list(change.after),
            "added_cells": len(change.after)-len(change.before),
        }
        for change in plan.changes
    ]
    response = {
        "preview_id": preview_id,
        "first_date": body.first_date.isoformat(),
        "last_date": body.last_date.isoformat(),
        "changed_dates": changed,
        "added_cell_count": sum(row["added_cells"] for row in changed),
        "skipped_locked_dates": [
            {"date": day.isoformat(), "reason": rows[day]["lock_reason"] or "published_week"}
            for day in plan.skipped_locked_dates
        ],
        "unchanged_matching_dates": len(rows)-len(changed)-len(locked),
    }
    return response, plan, rows


def execute_bulk(token, location, body, apply):
    if identity(token) is None:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    digest = session_token_digest(token)

    with connection() as conn:
        conn.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)", (digest,),
        )
        try:
            # The lock is held through preview construction or the complete
            # application transaction, including every week and audit insert.
            profile = lock_scope(conn, location)
            response, plan, rows = build_preview(
                conn, location, body, digest, profile,
            )
            if not apply:
                return response
            if not hmac.compare_digest(
                body.expected_preview, response["preview_id"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Dates, locks or policy changed. Create a fresh preview.",
                )

            groups = {}
            for change in plan.changes:
                offset = (change.day-body.first_date).days // 7
                anchor = min(
                    body.first_date+timedelta(days=offset*7), LAST_FIRST_DATE
                )
                groups.setdefault(anchor, []).append({
                    "date": change.day.isoformat(),
                    "expected_revision": rows[change.day]["revision"],
                    "cells": list(change.after),
                })

            for anchor, changes in sorted(groups.items()):
                conn.execute(
                    "SELECT scheduling.save_my_planner(%s,%s,%s,%s)",
                    (
                        location, anchor, profile["location_revision"],
                        Jsonb(changes),
                    ),
                ).fetchone()

            # Recheck even the skipped and unchanged matching dates. If a
            # deadline crossed while saving, the entire request rolls back.
            after_rows = read_selected(conn, location, body)
            if gate_state(after_rows) != gate_state(rows):
                raise HTTPException(
                    status_code=409,
                    detail="A lock changed during submission. Create a fresh preview.",
                )
            lock_scope(conn, location)  # Recheck actual session expiry.
            return {
                "status": "applied",
                "changed_date_count": len(plan.changes),
                "added_cell_count": response["added_cell_count"],
                "skipped_locked_dates": response["skipped_locked_dates"],
            }
        except psycopg.Error as error:
            failures = {
                "42501": (404, "Employee profile was not found."),
                "40001": (409, "Data changed. Create a fresh preview."),
                "55000": (409, "A date became locked. Create a fresh preview."),
                "22023": (422, "Check the dates, cells and cutoff policy."),
                "22007": (422, "Invalid calendar date."),
                "22008": (422, "Invalid calendar date."),
            }
            if error.sqlstate in failures:
                status, detail = failures[error.sqlstate]
                raise HTTPException(status_code=status, detail=detail) from None
            raise


@router.post("/{location_id}/bulk/preview")
def preview(
    location_id: UUID, body: BulkRequest,
    token: str = Depends(mutation_token),
):
    return execute_bulk(token, location_id, body, apply=False)


@router.post("/{location_id}/bulk/apply")
def apply(
    location_id: UUID, body: BulkApply,
    token: str = Depends(mutation_token),
):
    return execute_bulk(token, location_id, body, apply=True)
