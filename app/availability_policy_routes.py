"""Administrator-managed location availability deadlines."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.account_access import administrator_connection
from app.auth_routes import _token
from app.staff_write_routes import mutation_token

router = APIRouter(prefix="/api/v1/staff", tags=["availability policy"])


class EditAvailabilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=1)
    week_start: StrictInt = Field(ge=0, le=6)
    cutoff_days_before: StrictInt = Field(ge=0, le=365)
    cutoff_time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


@router.get("/locations/{location_id}/availability-policy")
def read_policy(location_id: UUID, token: str = Depends(_token)):
    with administrator_connection(token, location_id) as (conn, location, actor):
        return conn.execute(
            """
            SELECT id AS location_id, name, timezone, revision, week_start,
                   availability_cutoff_days AS cutoff_days_before,
                   left(availability_cutoff_time::text,5) AS cutoff_time
            FROM scheduling.location WHERE id=%s
            """, (location_id,),
        ).fetchone()


@router.patch("/locations/{location_id}/availability-policy")
def edit_policy(
    location_id: UUID, body: EditAvailabilityPolicy,
    token: str = Depends(mutation_token),
):
    with administrator_connection(
        token, location_id, write=True
    ) as (conn, location, actor):
        try:
            return conn.execute(
                """
                SELECT scheduling.edit_availability_policy(
                    %s,%s,%s,%s,%s
                ) AS policy
                """,
                (
                    location_id, body.expected_revision, body.week_start,
                    body.cutoff_days_before, body.cutoff_time,
                ),
            ).fetchone()["policy"]
        except psycopg.Error as error:
            statuses = {"40001": 409, "42501": 403, "22023": 422}
            if error.sqlstate in statuses:
                raise HTTPException(
                    status_code=statuses[error.sqlstate],
                    detail="Policy changed or is invalid. Refresh and check the fields.",
                ) from None
            raise
