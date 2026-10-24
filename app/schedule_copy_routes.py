"""Copy schedule structure into a new unassigned draft in one transaction."""

from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.shift_routes import fail, read_week
from app.shift_timing import calculate_shift_timing
from app.staff_data import staff_connection
from app.staff_write_routes import mutation_token
from app.staff_writes import _audit, _location

router = APIRouter(prefix="/api/v1/staff", tags=["schedule copying"])


class CopyWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week_start_date: date
    expected_source_revision: StrictInt = Field(ge=1, le=2147483647)
    expected_location_revision: StrictInt = Field(ge=1, le=2147483647)


@router.post(
    "/locations/{location_id}/schedule-weeks/{source_week_id}/copy",
    status_code=201,
)
def copy_week(
    location_id: UUID, source_week_id: UUID, body: CopyWeek,
    token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        try:
            # Authorize before attempting destination creation.
            read_week(conn, location_id, source_week_id)
            destination = conn.execute(
                "SELECT scheduling.create_blank_schedule_week(%s,%s,%s) AS result",
                (location_id, body.week_start_date,
                 body.expected_location_revision),
            ).fetchone()["result"]

            # Creation holds the organization lock used by shift mutations.
            # Re-read the source under that lock to get a consistent snapshot.
            source = read_week(conn, location_id, source_week_id)
            source_week = source["week"]
            if source_week["revision"] != body.expected_source_revision:
                raise HTTPException(
                    status_code=409,
                    detail="The source schedule changed. Refresh before copying.",
                )
            if source_week["week_start_day"] != destination["week_start_day"]:
                raise HTTPException(
                    status_code=422,
                    detail="Source and destination must use the same week-start day.",
                )
            if len(source["slots"]) > 500:
                raise HTTPException(
                    status_code=422,
                    detail="This schedule exceeds the 500-shift copy limit.",
                )

            first = date.fromisoformat(source_week["week_start_date"])
            result = None
            revision = destination["revision"]

            for slot in source["slots"]:
                local_start = datetime.fromisoformat(slot["local_start"])
                local_end = datetime.fromisoformat(slot["local_end"])
                day = body.week_start_date + timedelta(
                    days=(local_start.date()-first).days
                )
                try:
                    timing = calculate_shift_timing(
                        day,
                        local_start.hour*60+local_start.minute,
                        local_end.hour*60+local_end.minute,
                        ends_next_day=local_end.date() != local_start.date(),
                        timezone_name=destination["timezone"],
                    )
                except ValueError as error:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Cannot copy a shift to {day}: {error}",
                    ) from None

                result = conn.execute(
                    "SELECT scheduling.create_draft_shift("
                    "%s,%s,%s,%s,%s,%s) AS result",
                    (
                        destination["id"], slot["role_id"], revision,
                        timing.start_utc, timing.end_utc, slot["note"],
                    ),
                ).fetchone()["result"]
                revision = result["week"]["revision"]

            if result is None:
                result = read_week(conn, location_id, UUID(destination["id"]))

            location = _location(conn, location_id)
            _audit(
                conn, location, "schedule.week_copied", "location", location_id,
                {
                    "source_week_id": source_week["id"],
                    "source_revision": source_week["revision"],
                },
                {
                    "destination_week_id": destination["id"],
                    "week_start_date": destination["week_start_date"],
                    "copied_shift_count": len(source["slots"]),
                },
            )
            return result
        except psycopg.Error as error:
            if error.sqlstate == "23505":
                raise HTTPException(
                    status_code=409,
                    detail="A schedule already covers the destination week.",
                ) from None
            fail(error)
