"""Explicit revisions and approvals for editing an assigned draft shift."""

from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from app.assignment_requests import RuleApproval
from app.shift_routes import EditShift


class EditAssignedShift(EditShift):
    """The server must re-evaluate the employee against the proposed shift.

    Client-supplied eligibility decisions are forbidden. Approvals must match
    freshly calculated warnings exactly before any change can be committed.
    """

    expected_assignment_id: UUID
    expected_assignment_revision: StrictInt = Field(
        ge=1, le=2147483646,
    )
    expected_location_revision: StrictInt = Field(
        ge=1, le=2147483646,
    )
    approvals: list[RuleApproval] = Field(
        default_factory=list, max_length=2,
    )

    @model_validator(mode="after")
    def unique_approvals(self):
        codes = [approval.rule_code for approval in self.approvals]
        if len(codes) != len(set(codes)):
            raise ValueError("Supply only one approval per warning.")
        return self
