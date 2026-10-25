"""Employee-safe official rosters and staff publication history."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.auth_routes import _token
from app.my_unavailability_routes import private_read
from app.shift_routes import fail, read_week
from app.staff_data import staff_connection
import psycopg

router = APIRouter(tags=["published schedules"])


@router.get("/api/v1/me/published-schedules/profiles")
def profiles(response: Response, token: str = Depends(_token)):
    rows = private_read(token, "SELECT scheduling.my_unavailability_profiles()")
    response.headers["Cache-Control"] = "no-store"
    keys = (
        "employee_id", "location_id", "business_name",
        "location_name", "timezone",
    )
    return [{key: row[key] for key in keys} for row in rows]


@router.get("/api/v1/me/published-schedules/{location_id}")
def employee_weeks(
    location_id: UUID, start: date, end: date,
    response: Response, token: str = Depends(_token),
):
    result = private_read(
        token, "SELECT scheduling.list_my_published_weeks(%s,%s,%s)",
        (location_id, start, end),
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/api/v1/me/published-schedules/{location_id}/{week_id}")
def employee_week(
    location_id: UUID, week_id: UUID,
    response: Response, token: str = Depends(_token),
):
    result = private_read(
        token, "SELECT scheduling.read_my_published_week(%s,%s)",
        (location_id, week_id),
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "/api/v1/staff/locations/{location_id}/schedule-weeks/{week_id}/publications"
)
def staff_history(
    location_id: UUID, week_id: UUID,
    response: Response, token: str = Depends(_token),
):
    with staff_connection(token, repeatable_read=True) as conn:
        try:
            read_week(conn, location_id, week_id)
            result = conn.execute(
                "SELECT scheduling.read_schedule_publication_history(%s) AS result",
                (week_id,),
            ).fetchone()["result"]
            response.headers["Cache-Control"] = "no-store"
            return result
        except psycopg.Error as error:
            fail(error)


@router.get(
    "/api/v1/staff/locations/{location_id}/schedule-weeks/{week_id}"
    "/publications/{publication_number}"
)
def staff_version(
    location_id: UUID, week_id: UUID, publication_number: int,
    response: Response, token: str = Depends(_token),
):
    with staff_connection(token, repeatable_read=True) as conn:
        try:
            read_week(conn, location_id, week_id)
            result = conn.execute(
                "SELECT scheduling.read_staff_published_version(%s,%s) AS result",
                (week_id, publication_number),
            ).fetchone()["result"]
            response.headers["Cache-Control"] = "no-store"
            return result
        except psycopg.Error as error:
            fail(error)
