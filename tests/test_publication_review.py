from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.assignment_candidates import evaluate_inputs
from app.publication_review import (
    PublishWeek, PublicationRejected, review_publication,
    validate_publication_confirmation,
)


def fixture():
    slot = {
        "id": "slot", "revision": 2, "role_id": "cook", "role_name": "Cook",
        "start_at": "2026-10-26T17:00:00+00:00",
        "end_at": "2026-10-27T01:00:00+00:00",
    }
    week = {
        "id": "week", "revision": 3, "location_id": "location",
        "week_start_date": "2026-10-26",
        "timezone": "America/Los_Angeles", "business_day_start_minute": 300,
    }
    assignment = {
        "id": "assignment", "revision": 1, "slot_id": "slot",
        "employee_id": "employee", "display_name": "Test Employee",
    }
    inputs = {
        "week": deepcopy(week), "slot": deepcopy(slot),
        "employee": {
            "id": "employee", "display_name": "Test Employee",
            "revision": 1, "active": True,
        },
        "current_assignment": {
            "id": "assignment", "revision": 1, "employee_id": "employee",
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
    return {
        "week": week, "slots": [slot], "assignments": [assignment],
        "assignment_inputs": [inputs], "approvals": [],
    }


def approval(inputs, code):
    decision = evaluate_inputs(inputs, rest_policy={
        key: inputs["rest_policy"][key]
        for key in ("window_start", "window_end", "rest_minutes")
    })
    return {
        "assignment_id": "assignment", "assignment_revision": 1,
        "employee_id": "employee", "rule_code": code,
        "reason": "private approval reason",
        "changed_since_approval": False,
        "policy": {
            "rest": deepcopy(inputs["rest_policy"]),
            "timezone": inputs["week"]["timezone"],
            "business_day_start_minute": 300,
        },
        "rule_details": {"daily_work": decision["daily_work"]},
    }


def test_all_assignments_are_checked_without_mutating_inputs():
    data = fixture()
    before = deepcopy(data)
    result = review_publication(data)
    assert result["can_publish"]
    assert result["hard_conflicts"] == result["warnings"] == []
    assert data == before


def test_unflagged_assignment_still_receives_fresh_hard_check():
    data = fixture()
    data["assignments"][0]["review_status"] = "clear"
    data["assignment_inputs"][0]["qualified"] = False
    result = review_publication(data)
    assert not result["can_publish"]
    assert result["hard_conflicts"][0]["rule_codes"] == ["not_qualified"]


def test_warning_requires_current_approval_without_exposing_reason():
    data = fixture()
    inputs = data["assignment_inputs"][0]
    inputs["calendar_days"][0]["blocked_cells"] = [40]
    assert not review_publication(data)["can_publish"]
    data["approvals"] = [approval(inputs, "unavailability")]
    result = review_publication(data)
    assert result["can_publish"]
    assert result["warnings"][0]["approved"]
    assert "private approval reason" not in str(result)


@pytest.mark.parametrize("change", [
    {"changed_since_approval": True},
    {"changed_since_approval": None},
    {"assignment_revision": 2},
    {"employee_id": "different-employee"},
    {"reason": "   "},
    {"policy": {}},
])
def test_stale_or_invalid_warning_approval_does_not_authorize_publish(change):
    data = fixture()
    inputs = data["assignment_inputs"][0]
    inputs["calendar_days"][0]["blocked_cells"] = [40]
    data["approvals"] = [{**approval(inputs, "unavailability"), **change}]
    assert not review_publication(data)["can_publish"]


def test_changed_daily_hours_need_new_approval():
    data = fixture()
    inputs = data["assignment_inputs"][0]
    inputs["assigned"] = [{
        "start_at": "2026-10-26T15:00:00+00:00",
        "end_at": "2026-10-26T17:00:00+00:00",
        "timezone": "America/Los_Angeles", "business_day_start_minute": 300,
    }]
    data["approvals"] = [approval(inputs, "daily_hours")]
    assert review_publication(data)["can_publish"]
    data["approvals"][0]["rule_details"]["daily_work"] = []
    assert not review_publication(data)["can_publish"]


def test_missing_or_duplicate_fresh_inputs_fail_closed():
    data = fixture()
    data["assignment_inputs"] = []
    with pytest.raises(ValueError, match="every assigned"):
        review_publication(data)
    data = fixture()
    data["assignment_inputs"] *= 2
    with pytest.raises(ValueError, match="Duplicate"):
        review_publication(data)


def test_changed_shift_input_fails_closed():
    data = fixture()
    data["assignment_inputs"][0]["slot"]["revision"] += 1
    with pytest.raises(ValueError, match="changed"):
        review_publication(data)


def confirm(request, review):
    validate_publication_confirmation(
        request, review, week_revision=3,
        location_revision=4, publication_number=0,
    )


def request(**changes):
    return PublishWeek(
        **{
            "expected_week_revision": 3,
            "expected_location_revision": 4,
            "expected_publication_number": 0,
            "confirm_publish": True,
            **changes,
        }
    )


def test_unfilled_requires_its_own_explicit_confirmation():
    data = fixture()
    data["assignments"] = []
    data["assignment_inputs"] = []
    review = review_publication(data)
    assert review["can_publish"]
    with pytest.raises(PublicationRejected) as caught:
        confirm(request(), review)
    assert caught.value.code == "unfilled_confirmation_required"
    confirm(request(confirm_unfilled=True), review)


def test_stale_publication_or_missing_confirmation_is_rejected():
    review = review_publication(fixture())
    with pytest.raises(PublicationRejected) as caught:
        confirm(request(expected_publication_number=1), review)
    assert caught.value.code == "stale"
    with pytest.raises(PublicationRejected) as caught:
        confirm(request(confirm_publish=False), review)
    assert caught.value.code == "confirmation_required"


def test_confirming_does_not_waive_a_hard_conflict():
    data = fixture()
    data["assignment_inputs"][0]["qualified"] = False
    with pytest.raises(PublicationRejected) as caught:
        confirm(request(confirm_unfilled=True), review_publication(data))
    assert caught.value.code == "hard_block"


@pytest.mark.parametrize("changes", [
    {"confirm_publish": "yes"},
    {"expected_week_revision": True},
    {"expected_publication_number": -1},
    {"roster": {}},
    {"can_publish": True},
])
def test_request_rejects_coercion_and_client_decisions(changes):
    with pytest.raises(ValidationError):
        request(**changes)
