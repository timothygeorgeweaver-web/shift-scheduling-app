"""Employee-owned unavailability writes through controlled database functions."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from app.auth_store import connection, identity
from app.security import session_token_digest
from app.staff_write_routes import mutation_token
from app.unavailability_resolver import BlockWindow

router = APIRouter(prefix="/api/v1/me/unavailability", tags=["my unavailability"])


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: StrictInt = Field(ge=0, le=1439)
    end: StrictInt = Field(ge=0, le=1440)
    ends_next_day: StrictBool = False

    @model_validator(mode="after")
    def valid_duration(self):
        BlockWindow(self.start, self.end, self.ends_next_day)
        return self


class WriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["recurring", "exception", "delete"]
    date: date
    expected_revision: StrictInt = Field(ge=0, le=2147483646)
    expected_location_revision: StrictInt = Field(ge=1)
    weekly_windows: dict[str, list[Window]] | None = None
    windows: list[Window] | None = None

    @model_validator(mode="after")
    def valid_shape(self):
        if self.operation == "recurring":
            if self.windows is not None or self.weekly_windows is None:
                raise ValueError("Supply only weekly_windows for a recurring pattern.")
            if any(key not in "0123456" or len(key) != 1
                   for key in self.weekly_windows):
                raise ValueError("Weekday keys must be 0 through 6.")
            groups = self.weekly_windows.values()
        else:
            if self.weekly_windows is not None or self.windows is None:
                raise ValueError("Supply only windows for a date replacement.")
            if self.operation == "delete" and self.windows:
                raise ValueError("Deletion requires an empty windows list.")
            groups = [self.windows]
        if any(len(group) > 32 for group in groups):
            raise ValueError("Use at most 32 intervals per day.")
        return self


@router.post("/{location_id}")
def write_unavailability(
    location_id: UUID, body: WriteRequest,
    token: str = Depends(mutation_token),
):
    if identity(token) is None:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    payload = body.model_dump(mode="json")
    windows = (
        payload["weekly_windows"] if body.operation == "recurring"
        else payload["windows"]
    )
    with connection() as conn:
        conn.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)",
            (session_token_digest(token),),
        )
        try:
            return conn.execute(
                "SELECT scheduling.write_my_unavailability(%s,%s,%s,%s,%s,%s)",
                (
                    location_id, body.operation, body.date,
                    body.expected_revision, body.expected_location_revision,
                    Jsonb(windows),
                ),
            ).fetchone()[0]
        except psycopg.Error as error:
            failures = {
                "42501": (404, "Employee profile was not found."),
                "40001": (409, "Data or policy changed. Refresh before saving."),
                "55000": (423, "This date is locked."),
                "22023": (422, "Check the intervals, date, and cutoff policy."),
            }
            if error.sqlstate in failures:
                status, detail = failures[error.sqlstate]
                raise HTTPException(status_code=status, detail=detail) from None
            raise
