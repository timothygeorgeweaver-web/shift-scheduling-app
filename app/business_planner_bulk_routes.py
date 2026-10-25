"""Previewed additive business-day edits over calendar-date storage."""

from datetime import date, timedelta
import hashlib
import hmac
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from psycopg.types.json import Jsonb
from pydantic import Field, StrictInt

from app.auth_store import connection, identity
from app.business_day import validate_rollover
from app.business_planner_routes import fail
from app.planner_bulk_routes import BulkRequest, gate_state, lock_scope
from app.planner_logic import normalize_cells
from app.security import session_token_digest
from app.staff_write_routes import mutation_token
from app.unavailability_business_day import (
    bulk_calendar_additions, calendar_address,
)

router = APIRouter(
    prefix="/api/v1/me/business-planner", tags=["business-day mass edit"],
)
LAST_FIRST = date(9998, 12, 25)


class BusinessBulkRequest(BulkRequest):
    expected_location_revision: StrictInt = Field(ge=1, le=2147483647)


class BusinessBulkApply(BusinessBulkRequest):
    expected_preview: str = Field(pattern=r"^[0-9a-f]{64}$")


def read_selected(conn, location, additions):
    if not additions:
        return {}
    first, last = min(additions), max(additions)
    rows = {}
    for offset in range(0, (last-first).days+1, 7):
        anchor = min(first+timedelta(days=offset), LAST_FIRST)
        week = conn.execute(
            "SELECT scheduling.read_my_planner(%s,%s)", (location, anchor),
        ).fetchone()[0]
        for row in week["days"]:
            day = date.fromisoformat(row["date"])
            if day in additions:
                rows[day] = row
    if set(rows) != set(additions):
        raise HTTPException(status_code=422, detail="Selected calendar dates are unavailable.")
    return dict(sorted(rows.items()))


def build_preview(conn, location, body, digest, profile):
    rollover = validate_rollover(profile["business_day_start_minute"])
    additions = bulk_calendar_additions(
        body.first_date, body.last_date, body.weekly_cells,
        rollover_minute=rollover,
    )
    origins = {}
    for offset in range((body.last_date-body.first_date).days+1):
        business_day = body.first_date+timedelta(days=offset)
        for cell in body.weekly_cells[str(business_day.weekday())]:
            actual, _ = calendar_address(business_day, cell, rollover)
            origins.setdefault(actual, set()).add(business_day.isoformat())

    rows = read_selected(conn, location, additions)
    changed, skipped = [], []
    for day, row in rows.items():
        labels = sorted(origins[day])
        if not row["editable"]:
            skipped.append({
                "date": day.isoformat(), "business_dates": labels,
                "reason": row["lock_reason"],
            })
            continue
        before = normalize_cells(row["blocked_cells"])
        after = normalize_cells(set(before).union(additions[day]))
        if before != after:
            changed.append({
                "date": day.isoformat(), "business_dates": labels,
                "before": list(before), "after": list(after),
                "added_cells": len(after)-len(before),
            })

    stamp = {
        "session": digest, "employee": profile["employee_id"],
        "location": str(location), "location_revision": profile["location_revision"],
        "rollover": rollover, "first_date": body.first_date.isoformat(),
        "last_date": body.last_date.isoformat(), "weekly_cells": body.weekly_cells,
        "rows": list(rows.values()),
    }
    preview_id = hashlib.sha256(json.dumps(
        stamp, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return {
        "preview_id": preview_id,
        "first_date": body.first_date.isoformat(),
        "last_date": body.last_date.isoformat(),
        "changed_dates": changed, "skipped_locked_dates": skipped,
        "added_cell_count": sum(row["added_cells"] for row in changed),
        "unchanged_matching_dates": len(rows)-len(changed)-len(skipped),
    }, rows, additions


def execute(token, location, body, apply):
    if identity(token) is None:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    digest = session_token_digest(token)
    with connection() as conn:
        conn.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)", (digest,),
        )
        try:
            profile = lock_scope(conn, location)
            if profile["location_revision"] != body.expected_location_revision:
                raise HTTPException(
                    status_code=409,
                    detail="Policy changed. Return to the main calendar and refresh.",
                )
            try:
                preview, rows, additions = build_preview(
                    conn, location, body, digest, profile,
                )
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from None
            if not apply:
                return preview
            if not hmac.compare_digest(body.expected_preview, preview["preview_id"]):
                raise HTTPException(
                    status_code=409, detail="Dates or locks changed. Create a new preview.",
                )

            groups = {}
            origin = min(additions) if additions else body.first_date
            for change in preview["changed_dates"]:
                day = date.fromisoformat(change["date"])
                anchor = min(
                    origin+timedelta(days=((day-origin).days//7)*7), LAST_FIRST,
                )
                groups.setdefault(anchor, []).append({
                    "date": change["date"],
                    "expected_revision": rows[day]["revision"],
                    "cells": change["after"],
                })
            for anchor, changes in sorted(groups.items()):
                conn.execute(
                    "SELECT scheduling.save_my_planner(%s,%s,%s,%s)",
                    (location, anchor, profile["location_revision"], Jsonb(changes)),
                ).fetchone()

            if gate_state(read_selected(conn, location, additions)) != gate_state(rows):
                raise HTTPException(
                    status_code=409, detail="A lock changed during submission. Preview again.",
                )
            lock_scope(conn, location)
            return {
                "status": "applied",
                "changed_date_count": len(preview["changed_dates"]),
                "added_cell_count": preview["added_cell_count"],
                "skipped_locked_dates": preview["skipped_locked_dates"],
            }
        except psycopg.Error as error:
            fail(error)


@router.post("/{location_id}/bulk/preview")
def preview(
    location_id: UUID, body: BusinessBulkRequest,
    token: str = Depends(mutation_token),
):
    return execute(token, location_id, body, False)


@router.post("/{location_id}/bulk/apply")
def apply(
    location_id: UUID, body: BusinessBulkApply,
    token: str = Depends(mutation_token),
):
    return execute(token, location_id, body, True)
