"""Staff-only read-only assignment candidate classification."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg

from app.assignment_candidates import read_saved_candidate
from app.auth_routes import _token
from app.shift_routes import fail, read_week
from app.staff_data import staff_connection

router = APIRouter(prefix="/api/v1/staff", tags=["assignment candidates"])


@router.get(
    "/locations/{location_id}/schedule-weeks/{week_id}"
    "/shifts/{slot_id}/candidates"
)
def assignment_candidates(
    location_id: UUID, week_id: UUID, slot_id: UUID,
    token: str = Depends(_token),
):
    with staff_connection(token, repeatable_read=True) as conn:
        try:
            data = read_week(conn, location_id, week_id)
            if data["week"]["state"] != "draft":
                raise HTTPException(
                    status_code=423, detail="Open an editable draft week."
                )
            if not any(row["id"] == str(slot_id) for row in data["slots"]):
                raise HTTPException(status_code=404, detail="Shift was not found.")

            employees = conn.execute(
                "SELECT id FROM scheduling.employee WHERE location_id=%s "
                "ORDER BY lower(display_name),id",
                (location_id,),
            ).fetchall()
            groups = {
                "eligible": [],
                "override_required": [],
                "blocked": [],
            }
            for employee in employees:
                result = read_saved_candidate(conn, slot_id, employee["id"])
                groups[result["category"]].append(result)

            return {
                "slot_id": str(slot_id),
                "week_revision": data["week"]["revision"],
                "groups": groups,
            }
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except psycopg.Error as error:
            fail(error)
