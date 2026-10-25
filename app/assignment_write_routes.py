"""Protected assignment writes; client eligibility decisions are never accepted."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from psycopg.types.json import Jsonb

from app.assignment_candidates import evaluate_inputs
from app.assignment_requests import (
    AssignEmployee, AssignmentRejected, validate_assignment_save,
)
from app.shift_routes import fail, read_week
from app.staff_data import staff_connection
from app.staff_write_routes import mutation_token

router = APIRouter(prefix="/api/v1/staff", tags=["assignment writes"])


@router.put(
    "/locations/{location_id}/schedule-weeks/{week_id}"
    "/shifts/{slot_id}/assignment"
)
def assign_employee(
    location_id: UUID, week_id: UUID, slot_id: UUID,
    body: AssignEmployee, token: str = Depends(mutation_token),
):
    # Mutation transactions intentionally use READ COMMITTED: acquire locks,
    # then read current inputs after any competing transaction finishes.
    with staff_connection(token, write=True) as conn:
        try:
            data = read_week(conn, location_id, week_id)
            if not any(row["id"] == str(slot_id) for row in data["slots"]):
                raise HTTPException(status_code=404, detail="Shift was not found.")

            locked = conn.execute(
                """
                WITH locked AS MATERIALIZED (
                    SELECT scheduling.lock_assignment_scope(
                        %s,%s,%s,%s,%s
                    ) AS inputs
                )
                SELECT inputs,md5(inputs::text) AS fingerprint FROM locked
                """,
                (
                    slot_id, body.employee_id, body.expected_week_revision,
                    body.expected_slot_revision, body.expected_location_revision,
                ),
            ).fetchone()
            inputs = locked["inputs"]
            saved = inputs["rest_policy"]
            decision = evaluate_inputs(inputs, rest_policy={
                name: saved[name]
                for name in ("window_start", "window_end", "rest_minutes")
            })
            reasons = validate_assignment_save(body, inputs, decision)

            return conn.execute(
                """
                SELECT scheduling.commit_assignment_evaluated(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) AS result
                """,
                (
                    slot_id, body.employee_id, body.expected_week_revision,
                    body.expected_slot_revision, body.expected_location_revision,
                    body.expected_assignment_id, body.expected_assignment_revision,
                    body.confirm_replacement, locked["fingerprint"],
                    Jsonb(decision), Jsonb(reasons),
                ),
            ).fetchone()["result"]
        except AssignmentRejected as error:
            status = 409 if error.code in {
                "stale", "replacement_required",
            } else 422
            raise HTTPException(status_code=status, detail=str(error)) from None
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except psycopg.Error as error:
            fail(error)


from pydantic import BaseModel, ConfigDict, Field, StrictInt


class UnassignEmployee(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_assignment_id: UUID
    expected_assignment_revision: StrictInt = Field(ge=1, le=2147483646)
    expected_week_revision: StrictInt = Field(ge=1, le=2147483646)
    expected_slot_revision: StrictInt = Field(ge=1, le=2147483646)
    expected_location_revision: StrictInt = Field(ge=1, le=2147483646)


@router.post(
    "/locations/{location_id}/schedule-weeks/{week_id}"
    "/shifts/{slot_id}/assignment/remove"
)
def unassign_employee(
    location_id: UUID, week_id: UUID, slot_id: UUID,
    body: UnassignEmployee, token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        try:
            data = read_week(conn, location_id, week_id)
            if not any(row["id"] == str(slot_id) for row in data["slots"]):
                raise HTTPException(status_code=404, detail="Shift was not found.")
            return conn.execute(
                "SELECT scheduling.unassign_draft_shift(%s,%s,%s,%s,%s,%s) AS result",
                (
                    slot_id, body.expected_assignment_id,
                    body.expected_assignment_revision, body.expected_week_revision,
                    body.expected_slot_revision, body.expected_location_revision,
                ),
            ).fetchone()["result"]
        except psycopg.Error as error:
            fail(error)
