"""Staff-only draft schedule-week creation and bounded listing."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.auth_routes import _token
from app.staff_data import staff_connection
from app.staff_write_routes import mutation_token

router = APIRouter(prefix="/api/v1/staff", tags=["schedule weeks"])


class BlankWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week_start_date: date
    expected_location_revision: StrictInt = Field(ge=1, le=2147483647)


def authorized_location(conn, location_id):
    if not conn.execute(
        "SELECT id FROM scheduling.location WHERE id=%s", (location_id,),
    ).fetchone():
        raise HTTPException(status_code=404, detail="Location was not found.")


def fail(error):
    statuses = {
        "42501": (403, "Scheduler access is required."),
        "40001": (409, "A schedule exists or location settings changed. Refresh."),
        "23505": (409, "A schedule already exists for this week."),
        "22023": (422, "Check the dates and configured week start."),
    }
    if error.sqlstate in statuses:
        status, detail = statuses[error.sqlstate]
        raise HTTPException(status_code=status, detail=detail) from None
    raise error


@router.get("/locations/{location_id}/schedule-weeks")
def list_weeks(
    location_id: UUID, start: date, end: date,
    token: str = Depends(_token),
):
    with staff_connection(token) as conn:
        authorized_location(conn, location_id)
        try:
            return conn.execute(
                "SELECT scheduling.list_schedule_weeks(%s,%s,%s) AS result",
                (location_id, start, end),
            ).fetchone()["result"]
        except psycopg.Error as error:
            fail(error)


@router.post("/locations/{location_id}/schedule-weeks", status_code=201)
def create_week(
    location_id: UUID, body: BlankWeek,
    token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        authorized_location(conn, location_id)
        try:
            return conn.execute(
                "SELECT scheduling.create_blank_schedule_week(%s,%s,%s) AS result",
                (location_id, body.week_start_date, body.expected_location_revision),
            ).fetchone()["result"]
        except psycopg.Error as error:
            fail(error)
