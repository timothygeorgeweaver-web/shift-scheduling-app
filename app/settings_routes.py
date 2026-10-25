"""Audited administrator-only business/location settings."""

from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.account_access import administrator_connection
from app.auth_routes import _token
from app.staff_write_routes import mutation_token

router = APIRouter(prefix="/api/v1/staff", tags=["location settings"])


class EditSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_organization_revision: StrictInt = Field(ge=1)
    expected_location_revision: StrictInt = Field(ge=1)
    business_name: str = Field(min_length=1, max_length=200)
    location_name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator("business_name", "location_name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Name is required.")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        value = value.strip()
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Invalid timezone.") from None
        return value


@router.get("/locations/{location_id}/settings")
def read_settings(location_id: UUID, token: str = Depends(_token)):
    with administrator_connection(token, location_id) as (conn, location, actor):
        return conn.execute(
            """
            SELECT o.id AS organization_id, l.id AS location_id,
                   o.name AS business_name, l.name AS location_name, l.timezone,
                   o.revision AS organization_revision,
                   l.revision AS location_revision
            FROM scheduling.location l
            JOIN scheduling.organization o ON o.id=l.organization_id
            WHERE l.id=%s
            """, (location_id,),
        ).fetchone()


@router.patch("/locations/{location_id}/settings")
def edit_settings(
    location_id: UUID, body: EditSettings,
    token: str = Depends(mutation_token),
):
    with administrator_connection(
        token, location_id, write=True
    ) as (conn, location, actor):
        try:
            return conn.execute(
                """
                SELECT scheduling.edit_location_settings(
                    %s,%s,%s,%s,%s,%s
                ) AS settings
                """,
                (
                    location_id, body.expected_organization_revision,
                    body.expected_location_revision, body.business_name,
                    body.location_name, body.timezone,
                ),
            ).fetchone()["settings"]
        except psycopg.Error as error:
            statuses = {"40001": 409, "42501": 403, "22023": 422}
            if error.sqlstate in statuses:
                raise HTTPException(
                    status_code=statuses[error.sqlstate],
                    detail="Settings changed or are invalid. Refresh and check the details.",
                ) from None
            raise


class EditBusinessDayPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_location_revision: StrictInt = Field(ge=1, le=2147483646)
    business_day_start_minute: StrictInt = Field(ge=0, le=1439)

    @field_validator("business_day_start_minute")
    @classmethod
    def valid_rollover(cls, value):
        from app.business_day import validate_rollover
        return validate_rollover(value)


@router.get("/locations/{location_id}/business-day-policy")
def read_business_day_policy(location_id: UUID, token: str = Depends(_token)):
    with administrator_connection(token, location_id) as (conn, location, actor):
        return conn.execute(
            """
            SELECT id AS location_id,revision AS location_revision,
                   business_day_start_minute,timezone
            FROM scheduling.location WHERE id=%s
            """, (location_id,),
        ).fetchone()


@router.patch("/locations/{location_id}/business-day-policy")
def edit_business_day_policy(
    location_id: UUID, body: EditBusinessDayPolicy,
    token: str = Depends(mutation_token),
):
    with administrator_connection(
        token, location_id, write=True
    ) as (conn, location, actor):
        try:
            return conn.execute(
                "SELECT scheduling.edit_business_day_policy(%s,%s,%s) AS policy",
                (location_id, body.expected_location_revision,
                 body.business_day_start_minute),
            ).fetchone()["policy"]
        except psycopg.Error as error:
            statuses = {"40001": 409, "42501": 403, "22023": 422}
            if error.sqlstate in statuses:
                raise HTTPException(
                    status_code=statuses[error.sqlstate],
                    detail="Rollover settings changed or are invalid. Refresh and try again.",
                ) from None
            raise


class EditRestPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_location_revision: StrictInt = Field(ge=1, le=2147483646)
    window_start: StrictInt = Field(ge=0, le=1439)
    window_end: StrictInt = Field(ge=0, le=1439)
    rest_minutes: StrictInt = Field(ge=0, le=1440)

    @field_validator("window_start", "window_end", "rest_minutes")
    @classmethod
    def quarter_hour(cls, value):
        if value % 15:
            raise ValueError("Choose a fifteen-minute increment.")
        return value


@router.get("/locations/{location_id}/rest-policy")
def read_rest_policy(location_id: UUID, token: str = Depends(_token)):
    with administrator_connection(token, location_id) as (conn, location, actor):
        return conn.execute(
            """
            SELECT id AS location_id,revision AS location_revision,
                   rest_window_start_minute AS window_start,
                   rest_window_end_minute AS window_end,
                   minimum_rest_minutes AS rest_minutes
            FROM scheduling.location WHERE id=%s
            """, (location_id,),
        ).fetchone()


@router.patch("/locations/{location_id}/rest-policy")
def edit_rest_policy(
    location_id: UUID, body: EditRestPolicy,
    token: str = Depends(mutation_token),
):
    with administrator_connection(
        token, location_id, write=True
    ) as (conn, location, actor):
        try:
            policy = conn.execute(
                "SELECT scheduling.edit_rest_policy(%s,%s,%s,%s,%s) AS policy",
                (location_id, body.expected_location_revision,
                 body.window_start, body.window_end, body.rest_minutes),
            ).fetchone()["policy"]
            if policy["location_revision"] != body.expected_location_revision:
                from app.assignment_review_service import review_week_locked
                weeks = conn.execute(
                    "SELECT scheduling.assignment_review_weeks(%s) AS result",
                    (location_id,),
                ).fetchone()["result"]
                for week in weeks:
                    review_week_locked(
                        conn, location_id, week["id"], week["revision"],
                        policy["location_revision"],
                    )
            return policy
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except psycopg.Error as error:
            statuses = {"40001": 409, "42501": 403, "22023": 422, "55000": 423}
            if error.sqlstate in statuses:
                detail = (
                    "Existing assignments require review before changing rest policy."
                    if error.sqlstate == "55000" else
                    "Rest settings changed or are invalid. Refresh and check the details."
                )
                raise HTTPException(
                    status_code=statuses[error.sqlstate], detail=detail,
                ) from None
            raise
