from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.assigned_shift_requests import EditAssignedShift


def payload():
    return {
        "role_id": str(uuid4()),
        "local_date": "2026-11-02",
        "start_minute": 540,
        "end_minute": 1020,
        "expected_week_revision": 3,
        "expected_shift_revision": 2,
        "expected_assignment_id": str(uuid4()),
        "expected_assignment_revision": 1,
        "expected_location_revision": 4,
    }


def test_request_preserves_shift_and_assignment_identity():
    body = payload()
    result = EditAssignedShift.model_validate(body)
    assert str(result.expected_assignment_id) == body["expected_assignment_id"]
    assert result.expected_assignment_revision == 1
    assert result.expected_location_revision == 4
    assert result.approvals == []


@pytest.mark.parametrize("field", [
    "expected_assignment_id",
    "expected_assignment_revision",
    "expected_location_revision",
])
def test_current_assignment_and_policy_are_required(field):
    body = payload()
    del body[field]
    with pytest.raises(ValidationError):
        EditAssignedShift.model_validate(body)


@pytest.mark.parametrize("field", [
    "expected_week_revision",
    "expected_shift_revision",
    "expected_assignment_revision",
    "expected_location_revision",
])
@pytest.mark.parametrize("value", [True, 0, -1, "2", 2147483647])
def test_revisions_are_strict_positive_bounded_integers(field, value):
    body = payload()
    body[field] = value
    with pytest.raises(ValidationError):
        EditAssignedShift.model_validate(body)


def test_separate_warning_reasons_are_trimmed():
    body = payload()
    body["approvals"] = [
        {"rule_code": "daily_hours", "reason": "  Coverage needed  "},
        {"rule_code": "unavailability", "reason": "Employee agreed"},
    ]
    result = EditAssignedShift.model_validate(body)
    assert result.approvals[0].reason == "Coverage needed"
    assert len(result.approvals) == 2


@pytest.mark.parametrize("approvals", [
    [{"rule_code": "rest_before", "reason": "Approved"}],
    [{"rule_code": "overlap", "reason": "Approved"}],
    [{"rule_code": "not_qualified", "reason": "Approved"}],
    [{"rule_code": "daily_hours", "reason": "   "}],
    [
        {"rule_code": "daily_hours", "reason": "First"},
        {"rule_code": "daily_hours", "reason": "Second"},
    ],
])
def test_hard_rule_approvals_blank_reasons_and_duplicates_are_rejected(approvals):
    body = payload()
    body["approvals"] = approvals
    with pytest.raises(ValidationError):
        EditAssignedShift.model_validate(body)


@pytest.mark.parametrize("field,value", [
    ("decision", {"category": "eligible"}),
    ("hard_blocks", []),
    ("employee_id", str(uuid4())),
    ("qualified", True),
    ("input_fingerprint", "client-supplied"),
])
def test_client_cannot_supply_decisions_or_change_assignee(field, value):
    body = payload()
    body[field] = value
    with pytest.raises(ValidationError):
        EditAssignedShift.model_validate(body)
