"""Staff-only independent schedule-template snapshots."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from app.auth_routes import _token
from app.schedule_week_routes import authorized_location
from app.shift_routes import fail
from app.staff_data import staff_connection
from app.staff_write_routes import mutation_token

router = APIRouter(prefix="/api/v1/staff", tags=["schedule templates"])


class SaveTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    expected_source_revision: StrictInt = Field(ge=1, le=2147483647)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Enter a template name.")
        return value


def template_failure(error):
    if error.sqlstate == "23505":
        raise HTTPException(
            status_code=409, detail="A template already uses that name."
        ) from None
    fail(error)


def read_template(conn, location_id, template_id):
    authorized_location(conn, location_id)
    result = conn.execute(
        "SELECT scheduling.read_schedule_template(%s) AS result",
        (template_id,),
    ).fetchone()["result"]
    if result["location_id"] != str(location_id):
        raise HTTPException(status_code=404, detail="Template was not found.")
    return result


@router.get("/locations/{location_id}/schedule-templates")
def list_templates(location_id: UUID, token: str = Depends(_token)):
    with staff_connection(token) as conn:
        authorized_location(conn, location_id)
        try:
            return conn.execute(
                "SELECT scheduling.list_schedule_templates(%s) AS result",
                (location_id,),
            ).fetchone()["result"]
        except psycopg.Error as error:
            template_failure(error)


@router.get("/locations/{location_id}/schedule-templates/{template_id}")
def template_detail(
    location_id: UUID, template_id: UUID, token: str = Depends(_token),
):
    with staff_connection(token) as conn:
        try:
            return read_template(conn, location_id, template_id)
        except psycopg.Error as error:
            template_failure(error)


@router.post(
    "/locations/{location_id}/schedule-weeks/{week_id}/templates",
    status_code=201,
)
def save_template(
    location_id: UUID, week_id: UUID, body: SaveTemplate,
    token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        authorized_location(conn, location_id)
        try:
            return conn.execute(
                "SELECT scheduling.save_schedule_template(%s,%s,%s,%s) AS result",
                (location_id, week_id, body.expected_source_revision, body.name),
            ).fetchone()["result"]
        except psycopg.Error as error:
            template_failure(error)


from datetime import date
from app.schedule_copy_routes import populate_draft
from app.staff_writes import _audit, _location


class ApplyTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week_start_date: date
    expected_template_revision: StrictInt = Field(ge=1, le=2147483647)
    expected_location_revision: StrictInt = Field(ge=1, le=2147483647)


@router.post(
    "/locations/{location_id}/schedule-templates/{template_id}/apply",
    status_code=201,
)
def apply_template(
    location_id: UUID, template_id: UUID, body: ApplyTemplate,
    token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        try:
            read_template(conn, location_id, template_id)
            destination = conn.execute(
                "SELECT scheduling.create_blank_schedule_week(%s,%s,%s) AS result",
                (location_id, body.week_start_date,
                 body.expected_location_revision),
            ).fetchone()["result"]
            template = read_template(conn, location_id, template_id)
            if template["revision"] != body.expected_template_revision:
                raise HTTPException(
                    status_code=409,
                    detail="The template changed. Refresh before using it.",
                )
            if template["week_start_day"] != destination["week_start_day"]:
                raise HTTPException(
                    status_code=422,
                    detail="The template and destination must use the same week-start day.",
                )
            result = populate_draft(
                conn, location_id, template["snapshot"],
                destination, body.week_start_date,
            )
            _audit(
                conn, _location(conn, location_id),
                "schedule.template_applied", "location", location_id,
                {
                    "template_id": template["id"],
                    "template_revision": template["revision"],
                },
                {
                    "destination_week_id": destination["id"],
                    "week_start_date": destination["week_start_date"],
                    "created_shift_count": len(result["slots"]),
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


from pydantic import StrictBool, model_validator


class ChangeTemplate(SaveTemplate):
    expected_source_revision: StrictInt | None = Field(default=None, ge=1)
    expected_template_revision: StrictInt = Field(ge=1, le=2147483646)
    source_week_id: UUID | None = None
    active: StrictBool = True

    @model_validator(mode="after")
    def paired_source(self):
        if (self.source_week_id is None) != (self.expected_source_revision is None):
            raise ValueError("Supply both source week and source revision.")
        return self


@router.patch("/locations/{location_id}/schedule-templates/{template_id}")
def change_template(
    location_id: UUID, template_id: UUID, body: ChangeTemplate,
    token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        try:
            read_template(conn, location_id, template_id)
            return conn.execute(
                "SELECT scheduling.change_schedule_template("
                "%s,%s,%s,%s,%s,%s) AS result",
                (
                    template_id, body.expected_template_revision,
                    body.name, body.active, body.source_week_id,
                    body.expected_source_revision,
                ),
            ).fetchone()["result"]
        except psycopg.Error as error:
            template_failure(error)


from psycopg.types.json import Jsonb
from app.template_pattern import TemplateDraft, from_snapshot, to_snapshot


class PatternWrite(TemplateDraft):
    expected_template_revision: StrictInt = Field(default=0, ge=0, le=2147483646)
    expected_location_revision: StrictInt = Field(ge=1, le=2147483647)


@router.get("/locations/{location_id}/schedule-templates/{template_id}/pattern")
def template_pattern(
    location_id: UUID, template_id: UUID, token: str = Depends(_token),
):
    with staff_connection(token) as conn:
        try:
            saved = read_template(conn, location_id, template_id)
            location = conn.execute(
                "SELECT revision FROM scheduling.location WHERE id=%s",
                (location_id,),
            ).fetchone()
            try:
                draft = from_snapshot(
                    saved["snapshot"], saved["name"],
                    saved["business_day_start_minute"],
                )
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from None
            return {
                **draft.model_dump(mode="json"),
                "id": saved["id"],
                "revision": saved["revision"],
                "week_start_day": saved["week_start_day"],
                "business_day_start_minute": saved["business_day_start_minute"],
                "location_revision": location["revision"],
            }
        except psycopg.Error as error:
            template_failure(error)


def write_pattern(token, location_id, template_id, body):
    with staff_connection(token, write=True) as conn:
        try:
            authorized_location(conn, location_id)
            location = conn.execute(
                "SELECT week_start FROM scheduling.location WHERE id=%s",
                (location_id,),
            ).fetchone()
            weekday = location["week_start"]
            rollover = 0  # New-template activation follows with database capture.
            if template_id is not None:
                saved = read_template(conn, location_id, template_id)
                weekday = saved["week_start_day"]
                rollover = saved["business_day_start_minute"]
            return conn.execute(
                "SELECT scheduling.save_template_pattern("
                "%s,%s,%s,%s,%s,%s) AS result",
                (
                    location_id, template_id, body.expected_template_revision,
                    body.expected_location_revision, body.name,
                    Jsonb(to_snapshot(body, weekday, rollover)),
                ),
            ).fetchone()["result"]
        except psycopg.Error as error:
            template_failure(error)


@router.post("/locations/{location_id}/schedule-templates", status_code=201)
def create_template_pattern(
    location_id: UUID, body: PatternWrite,
    token: str = Depends(mutation_token),
):
    return write_pattern(token, location_id, None, body)


@router.put("/locations/{location_id}/schedule-templates/{template_id}/pattern")
def replace_template_pattern(
    location_id: UUID, template_id: UUID, body: PatternWrite,
    token: str = Depends(mutation_token),
):
    return write_pattern(token, location_id, template_id, body)
