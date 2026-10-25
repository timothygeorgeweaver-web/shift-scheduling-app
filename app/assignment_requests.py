"""Assignment save validation over freshly locked eligibility inputs."""

from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StrictInt,
    field_validator, model_validator,
)


class RuleApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_code: Literal["unavailability", "daily_hours"]
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("A reason is required for each warning.")
        return value


class AssignEmployee(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: UUID
    expected_week_revision: StrictInt = Field(ge=1, le=2147483646)
    expected_slot_revision: StrictInt = Field(ge=1, le=2147483646)
    expected_location_revision: StrictInt = Field(ge=1, le=2147483646)
    expected_assignment_id: UUID | None = None
    expected_assignment_revision: StrictInt = Field(
        default=0, ge=0, le=2147483646,
    )
    confirm_replacement: StrictBool = False
    approvals: list[RuleApproval] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def check_assignment_and_approvals(self):
        if ((self.expected_assignment_id is None)
                != (self.expected_assignment_revision == 0)):
            raise ValueError("Supply the current assignment identity and revision together.")
        codes = [approval.rule_code for approval in self.approvals]
        if len(codes) != len(set(codes)):
            raise ValueError("Supply only one approval per warning.")
        return self


class AssignmentRejected(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def validate_assignment_save(request, inputs, result):
    """Return per-rule reasons only after fresh revision and rule checks.

    The caller must hold lock_assignment_scope in the same transaction,
    evaluate those locked inputs with saved policy, and authorize the actor.
    This pure function performs no database writes.
    """
    if not isinstance(request, AssignEmployee):
        raise ValueError("Supply a validated assignment request.")

    if (str(request.employee_id) != str(inputs["employee"]["id"])
            or str(result["employee_id"]) != str(request.employee_id)
            or str(result["slot_id"]) != str(inputs["slot"]["id"])):
        raise AssignmentRejected("stale", "Assignment inputs changed.")

    revisions = (
        (request.expected_week_revision, inputs["week"]["revision"]),
        (request.expected_slot_revision, inputs["slot"]["revision"]),
        (request.expected_location_revision,
         inputs["rest_policy"]["location_revision"]),
    )
    if any(expected != actual for expected, actual in revisions):
        raise AssignmentRejected("stale", "Schedule or policy changed.")

    current = inputs["current_assignment"]
    expected_id = (
        str(request.expected_assignment_id)
        if request.expected_assignment_id is not None else None
    )
    actual_id = str(current["id"]) if current is not None else None
    actual_revision = current["revision"] if current is not None else 0
    if (expected_id != actual_id
            or request.expected_assignment_revision != actual_revision):
        raise AssignmentRejected("stale", "The assigned employee changed.")

    if result["hard_blocks"] or result["category"] == "blocked":
        raise AssignmentRejected(
            "hard_block", "This employee cannot be assigned to this shift."
        )

    warnings = set(result["warnings"])
    if not warnings <= {"unavailability", "daily_hours"}:
        raise AssignmentRejected("unsupported_rule", "Unknown eligibility warning.")
    expected_category = "override_required" if warnings else "eligible"
    if result["category"] != expected_category:
        raise AssignmentRejected("invalid_result", "Eligibility result is inconsistent.")

    reasons = {approval.rule_code: approval.reason
               for approval in request.approvals}
    if set(reasons) != warnings:
        raise AssignmentRejected(
            "approval_required",
            "Provide a separate reason for every current warning.",
        )

    replacing = (
        current is not None
        and str(current["employee_id"]) != str(request.employee_id)
    )
    if replacing and not request.confirm_replacement:
        raise AssignmentRejected(
            "replacement_required", "Confirm replacing the assigned employee."
        )
    return reasons
