"""Private seven-day planner reads and CSRF-protected atomic saves."""

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.auth_routes import _token
from app.auth_store import connection, identity
from app.my_unavailability_routes import private_read
from app.planner_logic import (
    cells_to_intervals, intervals_to_cells, normalize_cells,
)
from app.security import session_token_digest
from app.staff_write_routes import mutation_token

router = APIRouter(prefix="/api/v1/me/planner", tags=["employee planner"])


def check_first_date(value):
    if not date(2000, 1, 1) <= value <= date(9998, 12, 25):
        raise ValueError("Choose a supported seven-day date range.")
    return value


class DateChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    expected_revision: StrictInt = Field(ge=0, le=999999999)
    cells: list[StrictInt] = Field(max_length=96)

    @model_validator(mode="after")
    def validate_cells(self):
        self.cells = list(normalize_cells(self.cells))
        return self


class SaveWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_date: date
    expected_location_revision: StrictInt = Field(ge=1, le=2147483647)
    changes: list[DateChange] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def validate_dates(self):
        check_first_date(self.first_date)
        dates = [change.date for change in self.changes]
        if len(set(dates)) != len(dates):
            raise ValueError("Each date may occur only once.")
        last = self.first_date + timedelta(days=6)
        if any(not self.first_date <= day <= last for day in dates):
            raise ValueError("Every change must belong to the displayed week.")
        return self


def project_calendar(data):
    """Frozen intervals take precedence; never round historical snapshots."""
    for day in data["days"]:
        frozen = day["frozen_blocked"]
        if frozen is not None:
            day["source"] = "frozen"
            day["blocked"] = frozen
            try:
                day["effective_cells"] = list(intervals_to_cells(frozen))
            except ValueError:
                day["effective_cells"] = None
        else:
            cells = normalize_cells(day["blocked_cells"])
            day["source"] = "date"
            day["effective_cells"] = list(cells)
            day["blocked"] = cells_to_intervals(cells)
    return data


@router.get("/profiles")
def profiles(token: str = Depends(_token)):
    return private_read(token, "SELECT scheduling.my_unavailability_profiles()")


@router.get("/{location_id}")
def read_calendar(
    location_id: UUID, start: date, token: str = Depends(_token),
):
    try:
        check_first_date(start)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid planner dates.") from None
    data = private_read(
        token, "SELECT scheduling.read_my_planner(%s,%s)",
        (location_id, start),
    )
    return project_calendar(data)


@router.put("/{location_id}")
def save_calendar(
    location_id: UUID, body: SaveWeek,
    token: str = Depends(mutation_token),
):
    if identity(token) is None:
        raise HTTPException(status_code=401, detail="Sign-in is required.")

    changes = [change.model_dump(mode="json") for change in body.changes]
    with connection() as conn:
        conn.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)",
            (session_token_digest(token),),
        )
        try:
            result = conn.execute(
                "SELECT scheduling.save_my_planner(%s,%s,%s,%s)",
                (
                    location_id, body.first_date,
                    body.expected_location_revision, Jsonb(changes),
                ),
            ).fetchone()[0]
        except psycopg.Error as error:
            failures = {
                "42501": (404, "Employee profile was not found."),
                "40001": (409, "Data or policy changed. Refresh before saving."),
                "55000": (423, "A changed date is locked. Refresh the calendar."),
                "22023": (422, "Check the planner dates and selected cells."),
                "22007": (422, "Invalid planner date."),
                "22008": (422, "Invalid planner date."),
            }
            if error.sqlstate in failures:
                status, detail = failures[error.sqlstate]
                raise HTTPException(status_code=status, detail=detail) from None
            raise
        return project_calendar(result)
