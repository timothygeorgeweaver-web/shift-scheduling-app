"""Evaluate an assigned edit using freshly authorized proposed-shift inputs."""

from datetime import datetime

from app.assigned_shift_requests import EditAssignedShift
from app.assignment_candidates import evaluate_inputs
from app.assignment_requests import (
    AssignEmployee, AssignmentRejected, validate_assignment_save,
)
from app.shift_timing import calculate_shift_timing


def evaluate_assigned_shift_edit(request, inputs):
    """Return decision and reasons without changing any records.

    The caller must lock the current shift, assignment and dependencies, then
    obtain inputs for the PROPOSED role and actual dates. Reusing availability
    or qualifications read for the old shift is not sufficient.

    The snapshot retains current revisions and assignment identity, excludes
    this shift from neighboring assignments, and supplies effective availability
    for every actual date touched by the proposed shift.
    """
    if not isinstance(request, EditAssignedShift):
        raise ValueError("Supply a validated assigned-shift edit request.")

    current = inputs["current_assignment"]
    if (current is None
            or str(current["employee_id"]) != str(inputs["employee"]["id"])):
        raise AssignmentRejected("stale", "The assigned employee changed.")

    timing = calculate_shift_timing(
        request.local_date, request.start_minute, request.end_minute,
        ends_next_day=request.ends_next_day,
        timezone_name=inputs["week"]["timezone"],
    )
    slot = inputs["slot"]
    if (
        str(slot["role_id"]) != str(request.role_id)
        or datetime.fromisoformat(slot["start_at"]) != timing.start_utc
        or datetime.fromisoformat(slot["end_at"]) != timing.end_utc
    ):
        raise AssignmentRejected(
            "stale", "Eligibility inputs do not match the proposed shift."
        )

    policy = inputs["rest_policy"]
    decision = evaluate_inputs(inputs, rest_policy={
        key: policy[key]
        for key in ("window_start", "window_end", "rest_minutes")
    })
    assignment_request = AssignEmployee(
        employee_id=inputs["employee"]["id"],
        expected_week_revision=request.expected_week_revision,
        expected_slot_revision=request.expected_shift_revision,
        expected_location_revision=request.expected_location_revision,
        expected_assignment_id=request.expected_assignment_id,
        expected_assignment_revision=request.expected_assignment_revision,
        approvals=request.approvals,
    )
    reasons = validate_assignment_save(
        assignment_request, inputs, decision,
    )
    return decision, reasons
