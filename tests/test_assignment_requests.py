from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.assignment_requests import (
    AssignEmployee, AssignmentRejected, validate_assignment_save,
)

EMPLOYEE, OTHER, SLOT, ASSIGNMENT = [str(uuid4()) for _ in range(4)]


def fixture():
    inputs = {
        "employee": {"id": EMPLOYEE},
        "slot": {"id": SLOT, "revision": 2},
        "week": {"revision": 3},
        "rest_policy": {"location_revision": 4},
        "current_assignment": None,
    }
    result = {
        "employee_id": EMPLOYEE, "slot_id": SLOT,
        "category": "eligible", "hard_blocks": [], "warnings": [],
    }
    body = {
        "employee_id": EMPLOYEE,
        "expected_week_revision": 3,
        "expected_slot_revision": 2,
        "expected_location_revision": 4,
    }
    return inputs, result, body


def validate(inputs, result, body):
    return validate_assignment_save(AssignEmployee(**body), inputs, result)


def test_eligible_new_assignment_has_no_approvals_and_no_mutation():
    inputs, result, body = fixture()
    before = deepcopy((inputs, result, body))
    assert validate(inputs, result, body) == {}
    assert (inputs, result, body) == before


def test_each_soft_rule_requires_its_own_trimmed_reason():
    inputs, result, body = fixture()
    result.update(category="override_required",
                  warnings=["unavailability", "daily_hours"])
    body["approvals"] = [
        {"rule_code": "unavailability", "reason": "  Employee agreed  "},
        {"rule_code": "daily_hours", "reason": "Coverage approved"},
    ]
    assert validate(inputs, result, body) == {
        "unavailability": "Employee agreed",
        "daily_hours": "Coverage approved",
    }


@pytest.mark.parametrize("approvals", [
    [],
    [{"rule_code": "daily_hours", "reason": "Approved"}],
])
def test_missing_or_wrong_rule_approval_is_rejected(approvals):
    inputs, result, body = fixture()
    result.update(category="override_required", warnings=["unavailability"])
    body["approvals"] = approvals
    with pytest.raises(AssignmentRejected) as error:
        validate(inputs, result, body)
    assert error.value.code == "approval_required"


def test_unused_approval_is_rejected():
    inputs, result, body = fixture()
    body["approvals"] = [{"rule_code": "daily_hours", "reason": "Approved"}]
    with pytest.raises(AssignmentRejected):
        validate(inputs, result, body)


def test_hard_block_cannot_be_approved():
    inputs, result, body = fixture()
    result.update(category="blocked", hard_blocks=["rest_before"],
                  warnings=["daily_hours"])
    body["approvals"] = [{"rule_code": "daily_hours", "reason": "Approved"}]
    with pytest.raises(AssignmentRejected) as error:
        validate(inputs, result, body)
    assert error.value.code == "hard_block"


def test_replacement_needs_identity_revision_and_confirmation():
    inputs, result, body = fixture()
    inputs["current_assignment"] = {
        "id": ASSIGNMENT, "revision": 5, "employee_id": OTHER,
    }
    body.update(expected_assignment_id=ASSIGNMENT,
                expected_assignment_revision=5)
    with pytest.raises(AssignmentRejected) as error:
        validate(inputs, result, body)
    assert error.value.code == "replacement_required"
    body["confirm_replacement"] = True
    assert validate(inputs, result, body) == {}


def test_same_employee_does_not_require_replacement_confirmation():
    inputs, result, body = fixture()
    inputs["current_assignment"] = {
        "id": ASSIGNMENT, "revision": 5, "employee_id": EMPLOYEE,
    }
    body.update(expected_assignment_id=ASSIGNMENT,
                expected_assignment_revision=5)
    assert validate(inputs, result, body) == {}


@pytest.mark.parametrize("field", [
    "expected_week_revision",
    "expected_slot_revision",
    "expected_location_revision",
])
def test_stale_scope_revisions_are_rejected(field):
    inputs, result, body = fixture()
    body[field] += 1
    with pytest.raises(AssignmentRejected) as error:
        validate(inputs, result, body)
    assert error.value.code == "stale"


def test_unexpected_new_assignee_is_stale_even_with_confirmation():
    inputs, result, body = fixture()
    inputs["current_assignment"] = {
        "id": ASSIGNMENT, "revision": 1, "employee_id": OTHER,
    }
    body["confirm_replacement"] = True
    with pytest.raises(AssignmentRejected) as error:
        validate(inputs, result, body)
    assert error.value.code == "stale"


@pytest.mark.parametrize("changes", [
    {"expected_week_revision": True},
    {"confirm_replacement": "true"},
    {"expected_assignment_id": ASSIGNMENT},
    {"expected_assignment_revision": 1},
    {"approvals": [{"rule_code": "overlap", "reason": "Approved"}]},
    {"approvals": [{"rule_code": "daily_hours", "reason": "   "}]},
    {"approvals": [
        {"rule_code": "daily_hours", "reason": "First"},
        {"rule_code": "daily_hours", "reason": "Second"},
    ]},
    {"unrecognized": True},
])
def test_invalid_request_shapes_are_rejected(changes):
    _, _, body = fixture()
    body.update(changes)
    with pytest.raises(ValidationError):
        AssignEmployee(**body)
