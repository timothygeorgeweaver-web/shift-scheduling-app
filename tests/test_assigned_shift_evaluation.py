from copy import deepcopy
from uuid import uuid4

import pytest

from app.assigned_shift_evaluation import evaluate_assigned_shift_edit
from app.assigned_shift_requests import EditAssignedShift
from app.assignment_requests import AssignmentRejected


def fixture():
    role, employee, assignment, slot = [str(uuid4()) for _ in range(4)]
    request = EditAssignedShift(
        role_id=role, local_date="2026-10-26",
        start_minute=600, end_minute=1080,
        expected_week_revision=3, expected_shift_revision=2,
        expected_assignment_id=assignment,
        expected_assignment_revision=1,
        expected_location_revision=4,
    )
    inputs = {
        "week": {
            "revision": 3, "timezone": "America/Los_Angeles",
            "business_day_start_minute": 300,
        },
        "slot": {
            "id": slot, "role_id": role, "revision": 2,
            "start_at": "2026-10-26T17:00:00+00:00",
            "end_at": "2026-10-27T01:00:00+00:00",
        },
        "employee": {
            "id": employee, "display_name": "Test Employee",
            "revision": 1, "active": True,
        },
        "current_assignment": {
            "id": assignment, "employee_id": employee, "revision": 1,
        },
        "role_active": True, "qualified": True,
        "rest_policy": {
            "window_start": 1200, "window_end": 360,
            "rest_minutes": 720, "location_revision": 4,
        },
        "assigned": [],
        "calendar_days": [{
            "date": "2026-10-26", "blocked_cells": [],
            "frozen_blocked": None,
        }],
    }
    return request, inputs


def neighbor(start, end):
    return {
        "start_at": start, "end_at": end,
        "timezone": "America/Los_Angeles",
        "business_day_start_minute": 300,
    }


def test_valid_edit_preserves_inputs_and_employee():
    request, inputs = fixture()
    original = deepcopy(inputs)
    decision, reasons = evaluate_assigned_shift_edit(request, inputs)
    assert decision["category"] == "eligible"
    assert decision["employee_id"] == inputs["employee"]["id"]
    assert reasons == {}
    assert inputs == original


@pytest.mark.parametrize("field", ["role_id", "start_at", "end_at"])
def test_snapshot_must_match_proposed_role_and_times(field):
    request, inputs = fixture()
    inputs["slot"][field] = (
        str(uuid4()) if field == "role_id"
        else "2026-10-26T19:00:00+00:00"
    )
    with pytest.raises(AssignmentRejected) as caught:
        evaluate_assigned_shift_edit(request, inputs)
    assert caught.value.code == "stale"


@pytest.mark.parametrize("target,field", [
    ("week", "revision"),
    ("slot", "revision"),
    ("rest_policy", "location_revision"),
    ("current_assignment", "revision"),
])
def test_changed_revisions_reject_edit(target, field):
    request, inputs = fixture()
    inputs[target][field] += 1
    with pytest.raises(AssignmentRejected) as caught:
        evaluate_assigned_shift_edit(request, inputs)
    assert caught.value.code == "stale"


def test_changed_assignee_rejects_edit():
    request, inputs = fixture()
    inputs["current_assignment"]["employee_id"] = str(uuid4())
    with pytest.raises(AssignmentRejected) as caught:
        evaluate_assigned_shift_edit(request, inputs)
    assert caught.value.code == "stale"


@pytest.mark.parametrize("field", ["qualified", "role_active"])
def test_proposed_role_qualification_and_active_status_are_checked(field):
    request, inputs = fixture()
    inputs[field] = False
    with pytest.raises(AssignmentRejected) as caught:
        evaluate_assigned_shift_edit(request, inputs)
    assert caught.value.code == "hard_block"


@pytest.mark.parametrize("start,end", [
    ("2026-10-26T18:00:00+00:00", "2026-10-26T19:00:00+00:00"),
    ("2026-10-26T02:00:00+00:00", "2026-10-26T09:00:00+00:00"),
])
def test_overlap_and_rest_conflicts_block_edit(start, end):
    request, inputs = fixture()
    inputs["assigned"] = [neighbor(start, end)]
    with pytest.raises(AssignmentRejected) as caught:
        evaluate_assigned_shift_edit(request, inputs)
    assert caught.value.code == "hard_block"


def test_current_availability_requires_new_reason():
    request, inputs = fixture()
    inputs["calendar_days"][0]["blocked_cells"] = [40]
    with pytest.raises(AssignmentRejected) as caught:
        evaluate_assigned_shift_edit(request, inputs)
    assert caught.value.code == "approval_required"

    request = EditAssignedShift.model_validate({
        **request.model_dump(),
        "approvals": [{
            "rule_code": "unavailability",
            "reason": "Employee agreed to the changed shift",
        }],
    })
    decision, reasons = evaluate_assigned_shift_edit(request, inputs)
    assert decision["warnings"] == ["unavailability"]
    assert reasons == {
        "unavailability": "Employee agreed to the changed shift",
    }
    assert "calendar_days" not in decision
    assert "assigned" not in decision


def test_daily_hours_warning_requires_its_own_reason():
    request, inputs = fixture()
    inputs["assigned"] = [neighbor(
        "2026-10-26T15:00:00+00:00", "2026-10-26T17:00:00+00:00",
    )]
    with pytest.raises(AssignmentRejected) as caught:
        evaluate_assigned_shift_edit(request, inputs)
    assert caught.value.code == "approval_required"
    request = EditAssignedShift.model_validate({
        **request.model_dump(),
        "approvals": [{
            "rule_code": "daily_hours", "reason": "Coverage needed",
        }],
    })
    decision, reasons = evaluate_assigned_shift_edit(request, inputs)
    assert "daily_hours" in decision["warnings"]
    assert reasons == {"daily_hours": "Coverage needed"}


def test_missing_proposed_date_availability_fails_closed():
    request, inputs = fixture()
    inputs["calendar_days"] = []
    with pytest.raises(ValueError, match="missing"):
        evaluate_assigned_shift_edit(request, inputs)
