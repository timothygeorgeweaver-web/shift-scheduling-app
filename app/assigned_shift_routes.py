"""Atomic assigned-shift edits with fresh server-side eligibility checks."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from psycopg.types.json import Jsonb

from app.assigned_shift_evaluation import evaluate_assigned_shift_edit
from app.assigned_shift_requests import EditAssignedShift
from app.assignment_requests import AssignmentRejected
from app.shift_routes import fail, read_week
from app.shift_timing import calculate_shift_timing
from app.staff_data import staff_connection
from app.staff_write_routes import mutation_token

router = APIRouter(prefix="/api/v1/staff", tags=["assigned shift edits"])


@router.patch(
    "/locations/{location_id}/schedule-weeks/{week_id}"
    "/shifts/{slot_id}/assigned-edit"
)
def edit_assigned_shift(
    location_id: UUID, week_id: UUID, slot_id: UUID,
    body: EditAssignedShift, token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        try:
            data = read_week(conn, location_id, week_id)
            if not any(row["id"] == str(slot_id) for row in data["slots"]):
                raise HTTPException(status_code=404, detail="Shift was not found.")
            assignments = conn.execute(
                "SELECT scheduling.read_schedule_assignments(%s) AS result",
                (week_id,),
            ).fetchone()["result"]
            current = next(
                (row for row in assignments if row["slot_id"] == str(slot_id)),
                None,
            )
            if current is None or current["id"] != str(body.expected_assignment_id):
                raise HTTPException(status_code=409, detail="Assignment changed.")
            timing = calculate_shift_timing(
                body.local_date, body.start_minute, body.end_minute,
                ends_next_day=body.ends_next_day,
                timezone_name=data["week"]["timezone"],
            )
            parameters = (
                slot_id, current["employee_id"], body.expected_week_revision,
                body.expected_shift_revision, body.expected_location_revision,
                body.role_id, timing.start_utc, timing.end_utc,
            )
            locked = conn.execute(
                """
                WITH proposal AS MATERIALIZED (
                    SELECT scheduling.read_assigned_shift_proposal(
                        %s,%s,%s,%s,%s,%s,%s,%s
                    ) AS inputs
                )
                SELECT inputs,md5(inputs::text) AS fingerprint FROM proposal
                """, parameters,
            ).fetchone()
            decision, reasons = evaluate_assigned_shift_edit(
                body, locked["inputs"],
            )
            return conn.execute(
                """
                SELECT scheduling.commit_assigned_shift_edit(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) AS result
                """,
                (
                    slot_id, current["employee_id"], body.expected_week_revision,
                    body.expected_shift_revision, body.expected_location_revision,
                    body.expected_assignment_id,
                    body.expected_assignment_revision, body.role_id,
                    timing.start_utc, timing.end_utc, body.note,
                    locked["fingerprint"], Jsonb(decision), Jsonb(reasons),
                ),
            ).fetchone()["result"]
        except AssignmentRejected as error:
            raise HTTPException(
                status_code=409 if error.code == "stale" else 422,
                detail=str(error),
            ) from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except psycopg.Error as error:
            fail(error)
