"""CSRF-protected staff mutations and authorized history."""

import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth_routes import _check_origin, _csrf, _token
from app.staff_models import (
    CreateStaff, EditStaff, SetQualification, CreateRole, EditRole,
)
from app import staff_writes

router = APIRouter(prefix="/api/v1/staff", tags=["staff"])


def mutation_token(request: Request):
    _check_origin(request)
    token = _token(request)
    supplied = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(
        supplied.encode("utf-8"), _csrf(token).encode("ascii")
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")
    return token


@router.post("/locations/{location_id}/employees", status_code=201)
def create_employee(
    location_id: UUID, body: CreateStaff, token: str = Depends(mutation_token)
):
    return staff_writes.create_record(token, location_id, "employees", body)


@router.patch("/locations/{location_id}/employees/{employee_id}")
def edit_employee(
    location_id: UUID, employee_id: UUID, body: EditStaff,
    token: str = Depends(mutation_token),
):
    return staff_writes.edit_record(
        token, location_id, employee_id, "employees", body
    )


@router.post("/locations/{location_id}/roles", status_code=201)
def create_role(
    location_id: UUID, body: CreateRole, token: str = Depends(mutation_token)
):
    return staff_writes.create_record(token, location_id, "roles", body)


@router.patch("/locations/{location_id}/roles/{role_id}")
def edit_role(
    location_id: UUID, role_id: UUID, body: EditRole,
    token: str = Depends(mutation_token),
):
    return staff_writes.edit_record(token, location_id, role_id, "roles", body)


@router.put("/locations/{location_id}/employees/{employee_id}/qualifications/{role_id}")
def set_qualification(
    location_id: UUID, employee_id: UUID, role_id: UUID,
    body: SetQualification, token: str = Depends(mutation_token),
):
    return staff_writes.set_qualification(
        token, location_id, employee_id, role_id, body
    )


@router.get("/locations/{location_id}/history")
def history(
    location_id: UUID, limit: int = Query(default=50, ge=1, le=100),
    token: str = Depends(_token),
):
    return staff_writes.history(token, location_id, limit)


from pydantic import BaseModel, ConfigDict, Field, StrictInt


class OrderedRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    expected_revision: StrictInt = Field(ge=1, le=2147483646)


class ReorderRoles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: list[OrderedRole] = Field(max_length=10000)


@router.post("/locations/{location_id}/roles/order")
def reorder_roles(
    location_id: UUID, body: ReorderRoles,
    token: str = Depends(mutation_token),
):
    return staff_writes.reorder_roles(token, location_id, body)
