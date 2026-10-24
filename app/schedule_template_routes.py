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
