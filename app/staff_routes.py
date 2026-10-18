"""Business-scoped staff read APIs."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth_routes import _token
from app import staff_data

router = APIRouter(prefix="/api/v1/staff", tags=["staff"])


@router.get("/locations")
def locations(token: str = Depends(_token)):
    return staff_data.locations(token)


@router.get("/locations/{location_id}/employees")
def employees(location_id: UUID, token: str = Depends(_token)):
    return staff_data.location_records(token, location_id, "employees")


@router.get("/locations/{location_id}/roles")
def roles(location_id: UUID, token: str = Depends(_token)):
    return staff_data.location_records(token, location_id, "roles")


@router.get("/locations/{location_id}/qualifications")
def qualifications(location_id: UUID, token: str = Depends(_token)):
    return staff_data.location_records(token, location_id, "qualifications")
