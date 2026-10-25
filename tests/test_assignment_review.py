from copy import deepcopy

import pytest

from app.assignment_review import review_assignment, review_assignments


def fixture():
    return {
        "week": {
            "revision": 3, "timezone": "America/Los_Angeles",
            "business_day_start_minute": 300,
        },
        "slot": {
            "id": "slot", "revision": 2,
            "start_at": "2026-10-26T17:00:00+00:00",
            "end_at": "2026-10-27T01:00:00+00:00",
        },
        "employee": {
            "id": "employee", "display_name": "Test Employee",
            "revision": 4, "active": True,
        },
        "role_active": True,
        "qualified": True,
        "rest_policy": {
            "window_start": 1200, "window_end": 360,
            "rest_minutes": 720, "location_revision": 5,
        },
        "current_assignment": {
            "id": "assignment", "revision": 1, "employee_id": "employee",
        },
        "assigned": [],
        "calendar_days": [{
            "date": "2026-10-26", "blocked_cells": [],
            "frozen_blocked": None,
        }],
    }


def neighbor(first, last):
    return {
        "start_at": first, "end_at": last,
        "timezone": "America/Los_Angeles",
        "business_day_start_minute": 300,
    }


def test_valid_assignment_stays_valid_and_inputs_are_preserved():
    data = fixture()
    before = deepcopy(data)
    result = review_assignment(data)
    assert not result.needs_review
    assert result.assignment_id == "assignment"
    assert result.assignment_revision == 1
    assert data == before


def test_stricter_rest_policy_flags_previous_shift_without_unassigning():
    data = fixture()
    data["assigned"] = [neighbor(
        "2026-10-26T01:00:00+00:00",
        "2026-10-26T05:00:00+00:00",
    )]
    assert not review_assignment(data).needs_review
    before = deepcopy(data)
    result = review_assignment(data, proposed_rest_policy={
        "window_start": 1200, "window_end": 360, "rest_minutes": 780,
    })
    assert "rest_before" in result.hard_blocks
    assert result.needs_review
    assert data == before


def test_stricter_policy_checks_following_assignment_too():
    data = fixture()
    data["slot"].update(
        start_at="2026-10-26T21:00:00+00:00",
        end_at="2026-10-27T05:00:00+00:00",
    )
    data["assigned"] = [neighbor(
        "2026-10-27T17:00:00+00:00",
        "2026-10-27T19:00:00+00:00",
    )]
    assert not review_assignment(data).needs_review
    result = review_assignment(data, proposed_rest_policy={
        "window_start": 1200, "window_end": 360, "rest_minutes": 780,
    })
    assert "rest_after" in result.hard_blocks


def test_new_unavailability_requires_review():
    data = fixture()
    data["calendar_days"][0]["blocked_cells"] = [40]
    result = review_assignment(data)
    assert result.warnings == ("unavailability",)
    assert result.needs_review


def test_empty_frozen_snapshot_still_takes_precedence():
    data = fixture()
    data["calendar_days"][0].update(
        blocked_cells=[40], frozen_blocked=[],
    )
    assert not review_assignment(data).needs_review


def test_combined_hours_and_unavailability_are_separate_review_reasons():
    data = fixture()
    data["calendar_days"][0]["blocked_cells"] = [40]
    data["assigned"] = [neighbor(
        "2026-10-26T15:00:00+00:00",
        "2026-10-26T17:00:00+00:00",
    )]
    assert set(review_assignment(data).warnings) == {
        "unavailability", "daily_hours",
    }


@pytest.mark.parametrize("field,code", [
    ("qualified", "not_qualified"),
    ("role_active", "inactive_role"),
    ("employee_active", "inactive_employee"),
])
def test_changed_hard_requirements_are_flagged(field, code):
    data = fixture()
    if field == "employee_active":
        data["employee"]["active"] = False
    else:
        data[field] = False
    assert code in review_assignment(data).hard_blocks


def test_unrelated_saved_business_days_do_not_create_false_conflicts():
    data = fixture()
    row = neighbor(
        "2026-10-25T15:00:00+00:00",
        "2026-10-25T17:00:00+00:00",
    )
    row["business_day_start_minute"] = 0
    data["assigned"] = [row]
    result = review_assignment(data)
    assert not result.needs_review
    assert result.review_reasons == ()


def test_missing_or_wrong_assignment_is_rejected():
    data = fixture()
    data["current_assignment"] = None
    with pytest.raises(ValueError, match="existing assignment"):
        review_assignment(data)
    data = fixture()
    data["current_assignment"]["employee_id"] = "someone-else"
    with pytest.raises(ValueError, match="assigned employee"):
        review_assignment(data)


def test_batch_review_is_stable_and_rejects_duplicates():
    first = fixture()
    second = deepcopy(first)
    second["current_assignment"]["id"] = "another-assignment"
    second["slot"]["id"] = "another-slot"
    results = review_assignments([first, second])
    assert [row.assignment_id for row in results] == [
        "another-assignment", "assignment",
    ]
    with pytest.raises(ValueError, match="Duplicate"):
        review_assignments([first, first])
