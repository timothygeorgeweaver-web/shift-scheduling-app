"""Administrator-only employee sign-in creation."""

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import psycopg
from pydantic import (
    BaseModel, ConfigDict, Field, SecretStr, StrictInt, field_validator,
)

from app.account_access import administrator_connection
from app.security import hash_password
from app.staff_write_routes import mutation_token

router = APIRouter(prefix="/api/v1/staff", tags=["account administration"])


class CreateEmployeeAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254)
    password: SecretStr
    expected_revision: StrictInt = Field(ge=1)

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid email address.")
        return value

    @field_validator("password")
    @classmethod
    def valid_password(cls, value):
        if not 12 <= len(value.get_secret_value()) <= 1024:
            raise ValueError("Password must contain 12 to 1024 characters.")
        return value


@router.post(
    "/locations/{location_id}/employees/{employee_id}/account",
    status_code=201,
)
def create_employee_account(
    location_id: UUID,
    employee_id: UUID,
    body: CreateEmployeeAccount,
    token: str = Depends(mutation_token),
):
    with administrator_connection(token, location_id, write=True) as (
        conn, location, actor_id
    ):
        # Authorize before performing the expensive password hash.
        password_hash = hash_password(body.password.get_secret_value())
        try:
            row = conn.execute(
                """
                SELECT scheduling.create_employee_account(
                    %s,%s,%s,%s,%s
                ) AS account
                """,
                (
                    location_id, employee_id, body.expected_revision,
                    body.email, password_hash,
                ),
            ).fetchone()
        except psycopg.Error as error:
            if error.sqlstate in ("23505", "40001"):
                raise HTTPException(
                    status_code=409,
                    detail="Employee changed, is already linked, or account details are unavailable.",
                ) from None
            if error.sqlstate == "42501":
                raise HTTPException(
                    status_code=403, detail="Account operation is not permitted."
                ) from None
            if error.sqlstate == "22023":
                raise HTTPException(
                    status_code=422, detail="Invalid account details."
                ) from None
            raise
        return row["account"]


from typing import Literal
from fastapi import Request
from pydantic import StrictBool
from psycopg.types.json import Jsonb
from app.auth_routes import _token


class ChangeAccountAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=1)
    active: StrictBool
    permissions: list[Literal["administrator", "scheduler", "employee"]] = Field(
        min_length=1, max_length=3
    )

    @field_validator("permissions")
    @classmethod
    def unique_permissions(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate permissions.")
        return sorted(value)


def account_access_operation(token, location_id, employee_id, state=None):
    with administrator_connection(
        token, location_id, write=state is not None
    ) as (conn, location, actor_id):
        try:
            result = conn.execute(
                "SELECT scheduling.employee_account_access(%s,%s,%s::jsonb) AS account",
                (location_id, employee_id, Jsonb(state) if state is not None else None),
            ).fetchone()["account"]
        except psycopg.Error as error:
            statuses = {"40001": 409, "42501": 403, "P0002": 404, "22023": 422}
            status = statuses.get(error.sqlstate)
            if status:
                raise HTTPException(
                    status_code=status,
                    detail=(
                        "Access changed or this action would remove the last active "
                        "administrator. Refresh and check the account."
                        if status == 409 else "Account operation is unavailable."
                    ),
                ) from None
            raise
        return result


@router.get("/locations/{location_id}/employees/{employee_id}/account")
def read_employee_account(
    location_id: UUID, employee_id: UUID,
    token: str = Depends(_token),
):
    return account_access_operation(token, location_id, employee_id)


@router.patch("/locations/{location_id}/employees/{employee_id}/account")
def change_employee_account(
    location_id: UUID, employee_id: UUID,
    body: ChangeAccountAccess,
    token: str = Depends(mutation_token),
):
    return account_access_operation(
        token, location_id, employee_id, body.model_dump()
    )
