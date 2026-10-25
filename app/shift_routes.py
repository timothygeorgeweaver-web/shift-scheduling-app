"""Staff-only schedule detail and role-specific draft shift creation."""

from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.auth_routes import _token
from app.business_day import business_date
from app.schedule_week_routes import authorized_location
from app.shift_timing import calculate_shift_timing
from app.staff_data import staff_connection
from app.staff_write_routes import mutation_token

router = APIRouter(prefix="/api/v1/staff", tags=["draft shifts"])


class CreateShift(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_id: UUID
    local_date: date
    start_minute: StrictInt = Field(ge=0, le=1439)
    end_minute: StrictInt = Field(ge=0, le=1440)
    ends_next_day: StrictBool = False
    expected_week_revision: StrictInt = Field(ge=1, le=2147483646)
    note: str = Field(default="", max_length=500)


def fail(error):
    failures = {
        "42501": (404, "Schedule was not found."),
        "40001": (409, "The schedule changed. Refresh before saving."),
        "55000": (423, "This schedule is not editable as a draft."),
        "22023": (422, "Check the role, shift date and times."),
        "22007": (422, "Invalid shift date or time."),
        "22008": (422, "Invalid shift date or time."),
    }
    if error.sqlstate in failures:
        status, detail = failures[error.sqlstate]
        raise HTTPException(status_code=status, detail=detail) from None
    raise error


def read_week(conn, location_id, week_id):
    authorized_location(conn, location_id)
    result = conn.execute(
        "SELECT scheduling.read_schedule_week(%s) AS result", (week_id,),
    ).fetchone()["result"]
    if result["week"]["location_id"] != str(location_id):
        raise HTTPException(status_code=404, detail="Schedule was not found.")
    return result


@router.get("/locations/{location_id}/schedule-weeks/{week_id}")
def schedule_detail(
    location_id: UUID, week_id: UUID, token: str = Depends(_token),
):
    with staff_connection(token) as conn:
        try:
            return read_week(conn, location_id, week_id)
        except psycopg.Error as error:
            fail(error)


@router.post(
    "/locations/{location_id}/schedule-weeks/{week_id}/shifts",
    status_code=201,
)
def create_shift(
    location_id: UUID, week_id: UUID, body: CreateShift,
    token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        try:
            data = read_week(conn, location_id, week_id)
            week = data["week"]
            first = date.fromisoformat(week["week_start_date"])
            start_local = datetime.combine(body.local_date, datetime.min.time()) + timedelta(minutes=body.start_minute)
            start_day = business_date(
                start_local, week["business_day_start_minute"],
            )
            if not first <= start_day <= first+timedelta(days=6):
                raise HTTPException(
                    status_code=422,
                    detail="The shift must start within this schedule week.",
                )
            try:
                timing = calculate_shift_timing(
                    body.local_date, body.start_minute, body.end_minute,
                    ends_next_day=body.ends_next_day,
                    timezone_name=week["timezone"],
                )
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from None

            return conn.execute(
                "SELECT scheduling.create_draft_shift(%s,%s,%s,%s,%s,%s) AS result",
                (
                    week_id, body.role_id, body.expected_week_revision,
                    timing.start_utc, timing.end_utc, body.note,
                ),
            ).fetchone()["result"]
        except psycopg.Error as error:
            fail(error)


class EditShift(CreateShift):
    expected_shift_revision: StrictInt = Field(ge=1, le=2147483646)


class RemoveShift(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_week_revision: StrictInt = Field(ge=1, le=2147483646)
    expected_shift_revision: StrictInt = Field(ge=1, le=2147483646)


@router.patch("/locations/{location_id}/schedule-weeks/{week_id}/shifts/{shift_id}")
def edit_shift(
    location_id: UUID, week_id: UUID, shift_id: UUID, body: EditShift,
    token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        try:
            data = read_week(conn, location_id, week_id)
            week = data["week"]
            first = date.fromisoformat(week["week_start_date"])
            start_local = datetime.combine(body.local_date, datetime.min.time()) + timedelta(minutes=body.start_minute)
            start_day = business_date(
                start_local, week["business_day_start_minute"],
            )
            if not first <= start_day <= first+timedelta(days=6):
                raise HTTPException(
                    status_code=422,
                    detail="The shift must start within this schedule week.",
                )
            try:
                timing = calculate_shift_timing(
                    body.local_date, body.start_minute, body.end_minute,
                    ends_next_day=body.ends_next_day,
                    timezone_name=week["timezone"],
                )
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from None
            return conn.execute(
                "SELECT scheduling.change_draft_shift("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s) AS result",
                (
                    week_id, shift_id, body.expected_shift_revision, False,
                    body.role_id, body.expected_week_revision,
                    timing.start_utc, timing.end_utc, body.note,
                ),
            ).fetchone()["result"]
        except psycopg.Error as error:
            fail(error)


@router.post(
    "/locations/{location_id}/schedule-weeks/{week_id}/shifts/{shift_id}/remove"
)
def remove_shift(
    location_id: UUID, week_id: UUID, shift_id: UUID, body: RemoveShift,
    token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        try:
            read_week(conn, location_id, week_id)
            return conn.execute(
                "SELECT scheduling.change_draft_shift("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s) AS result",
                (
                    week_id, shift_id, body.expected_shift_revision, True,
                    None, body.expected_week_revision, None, None, None,
                ),
            ).fetchone()["result"]
        except psycopg.Error as error:
            fail(error)
