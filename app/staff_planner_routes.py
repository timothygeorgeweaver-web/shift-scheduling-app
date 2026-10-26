"""Authorized staff access to employee business-day unavailability."""

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.auth_routes import _token
from app.auth_store import connection, identity
from app.business_day import business_day_bounds, validate_rollover
from app.planner_bulk_routes import lock_scope
from app.planner_routes import DateChange, project_calendar
from app.security import session_token_digest
from app.staff_write_routes import mutation_token

router = APIRouter(
    prefix="/api/v1/staff/locations", tags=["staff employee unavailability"],
)


def check_start(value):
    if not date(2000, 1, 1) <= value <= date(9998, 12, 24):
        raise ValueError("Choose a supported eight-calendar-date range.")
    return value


class SaveBusinessWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_date: date
    expected_location_revision: StrictInt = Field(ge=1, le=2147483647)
    changes: list[DateChange] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_dates(self):
        check_start(self.first_date)
        days = [change.date for change in self.changes]
        if len(set(days)) != len(days):
            raise ValueError("Each calendar date may occur only once.")
        if any(not self.first_date <= day <= self.first_date+timedelta(days=7)
               for day in days):
            raise ValueError("Changes must belong to the displayed business week.")
        return self


def fail(error):
    failures = {
        "42501": (404, "Employee profile was not found."),
        "40001": (409, "Data or rollover policy changed. Refresh before saving."),
        "55000": (423, "A changed calendar date is locked. Refresh the planner."),
        "22023": (422, "Check planner dates, cells and policy."),
        "22007": (422, "Invalid calendar date."),
        "22008": (422, "Invalid calendar date."),
    }
    if error.sqlstate in failures:
        status, detail = failures[error.sqlstate]
        raise HTTPException(status_code=status, detail=detail) from None
    raise error


def read_eight(conn, location, employee, first):
    left = project_calendar(conn.execute(
        "SELECT scheduling.read_staff_planner(%s,%s,%s)", (location, employee, first),
    ).fetchone()[0])
    right = project_calendar(conn.execute(
        "SELECT scheduling.read_staff_planner(%s,%s,%s)",
        (location, employee, first+timedelta(days=1)),
    ).fetchone()[0])
    try:
        for offset in range(7):
            business_day_bounds(
                first+timedelta(days=offset), left["profile"]["timezone"],
                left["profile"]["business_day_start_minute"],
            )
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="The rollover time is missing or repeated on a selected date. Ask an administrator to choose another rollover.",
        ) from None
    rows = {row["date"]: row for row in left["days"]+right["days"]}
    return {
        "first_date": first.isoformat(),
        "profile": left["profile"],
        "calendar_days": [rows[key] for key in sorted(rows)],
    }


def gate(row):
    return (
        row["editable"], row["lock_reason"],
        row["cutoff"], row["frozen_blocked"],
        row.get("publication_locked_windows", []),
    )


@router.get("/{location_id}/employees/{employee_id}/business-planner")
def read_business_week(
    location_id: UUID, employee_id: UUID, start: date, token: str = Depends(_token),
):
    try:
        check_start(start)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    if identity(token) is None:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    with connection() as conn:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)",
            (session_token_digest(token),),
        )
        try:
            return read_eight(conn, location_id, employee_id, start)
        except psycopg.Error as error:
            fail(error)


@router.put("/{location_id}/employees/{employee_id}/business-planner")
def save_business_week(
    location_id: UUID, employee_id: UUID, body: SaveBusinessWeek,
    token: str = Depends(mutation_token),
):
    if identity(token) is None:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    with connection() as conn:
        conn.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)",
            (session_token_digest(token),),
        )
        try:
            profile = read_eight(
                conn, location_id, employee_id, body.first_date
            )["profile"]
            if profile["location_revision"] != body.expected_location_revision:
                raise HTTPException(status_code=409, detail="Rollover or policy changed.")
            rollover = validate_rollover(profile["business_day_start_minute"])
            state = read_eight(conn, location_id, employee_id, body.first_date)
            rows = {row["date"]: row for row in state["calendar_days"]}
            changes = []

            for change in body.changes:
                row = rows[change.date.isoformat()]
                if row["revision"] != change.expected_revision:
                    raise HTTPException(status_code=409, detail="A calendar date changed.")
                before, after = set(row["blocked_cells"]), set(change.cells)
                outside = set()
                if change.date == body.first_date:
                    outside = set(range(rollover//15))
                elif change.date == body.first_date+timedelta(days=7):
                    outside = set(range(rollover//15, 96))
                if before.intersection(outside) != after.intersection(outside):
                    raise HTTPException(
                        status_code=422,
                        detail="Cells outside the displayed business week must be preserved.",
                    )
                if before == after:
                    continue
                if not row["editable"]:
                    raise HTTPException(status_code=423, detail="A changed date is locked.")
                changes.append(change.model_dump(mode="json"))

            # Both batches share this connection and transaction.
            for offset in (0, 7):
                batch = [
                    change for change in changes
                    if (date.fromisoformat(change["date"])-body.first_date).days//7 == offset//7
                ]
                if batch:
                    anchor = body.first_date+timedelta(days=0 if offset == 0 else 1)
                    conn.execute(
                        "SELECT scheduling.save_staff_planner(%s,%s,%s,%s,%s)",
                        (location_id, employee_id, anchor, profile["location_revision"], Jsonb(batch)),
                    ).fetchone()

            result = read_eight(conn, location_id, employee_id, body.first_date)
            after_rows = {row["date"]: row for row in result["calendar_days"]}
            for change in changes:
                key = change["date"]
                if gate(rows[key]) != gate(after_rows[key]):
                    raise HTTPException(
                        status_code=409, detail="A lock changed during submission.",
                    )
            read_eight(conn, location_id, employee_id, body.first_date)
            return result
        except psycopg.Error as error:
            fail(error)
