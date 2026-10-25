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


class ClearWeekAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_week_revision: StrictInt = Field(ge=1, le=2147483646)
    expected_location_revision: StrictInt = Field(ge=1, le=2147483646)


@router.post(
    "/locations/{location_id}/schedule-weeks/{week_id}/assignments/clear"
)
def clear_week_assignments(
    location_id: UUID, week_id: UUID, body: ClearWeekAssignments,
    token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        try:
            return conn.execute(
                "SELECT scheduling.clear_draft_week_assignments("
                "%s,%s,%s,%s) AS result",
                (
                    location_id, week_id, body.expected_week_revision,
                    body.expected_location_revision,
                ),
            ).fetchone()["result"]
        except psycopg.Error as error:
            fail(error)


@router.post(
    "/locations/{location_id}/schedule-weeks/{week_id}/assignments/review"
)
def review_week_assignments(
    location_id: UUID, week_id: UUID, body: ClearWeekAssignments,
    token: str = Depends(mutation_token),
):
    from dataclasses import asdict
    from app.assignment_review import review_assignment

    with staff_connection(token, write=True) as conn:
        try:
            scope = conn.execute(
                "SELECT scheduling.lock_template_destination(%s,%s,%s,%s) AS result",
                (
                    location_id, week_id, body.expected_week_revision,
                    body.expected_location_revision,
                ),
            ).fetchone()["result"]
            assignments = conn.execute(
                "SELECT scheduling.read_schedule_assignments(%s) AS result",
                (week_id,),
            ).fetchone()["result"]
            slots = {row["id"]: row for row in scope["slots"]}
            reviewed = 0

            for assignment in assignments:
                if assignment["review_status"] == "clear":
                    continue
                slot = slots[assignment["slot_id"]]
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
                        assignment["slot_id"], assignment["employee_id"],
                        body.expected_week_revision, slot["revision"],
                        body.expected_location_revision,
                    ),
                ).fetchone()
                report = review_assignment(locked["inputs"])
                conn.execute(
                    "SELECT scheduling.store_assignment_review(%s,%s,%s)",
                    (
                        assignment["id"], locked["fingerprint"],
                        Jsonb(asdict(report)),
                    ),
                )
                reviewed += 1

            current = conn.execute(
                "SELECT scheduling.read_schedule_assignments(%s) AS result",
                (week_id,),
            ).fetchone()["result"]
            return {
                "reviewed_count": reviewed,
                "needs_review_count": sum(
                    row["review_status"] != "clear" for row in current
                ),
                "assignments": current,
            }
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except psycopg.Error as error:
            fail(error)
