from copy import deepcopy

import pytest

from app.assignment_candidates import evaluate_inputs, read_candidate

REST = {"window_start": 1200, "window_end": 360, "rest_minutes": 720}


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
        "assigned": [],
        "calendar_days": [{
            "date": "2026-10-26",
            "blocked_cells": [], "frozen_blocked": None,
        }],
    }


def evaluate(data):
    return evaluate_inputs(data, rest_policy=REST)


def test_eligible_and_input_unchanged():
    data = fixture()
    before = deepcopy(data)
    result = evaluate(data)
    assert result["category"] == "eligible"
    assert result["hard_blocks"] == []
    assert result["warnings"] == []
    assert result["daily_work"] == [{
        "business_date": "2026-10-26",
        "estimated_minutes_numerator": 450,
        "estimated_minutes_denominator": 1,
    }]
    assert data == before


def test_editable_cells_produce_warning_without_private_intervals():
    data = fixture()
    data["calendar_days"][0]["blocked_cells"] = [40]
    result = evaluate(data)
    assert result["category"] == "override_required"
    assert result["warnings"] == ["unavailability"]
    assert "calendar_days" not in result
    assert "assigned" not in result
    assert "unavailable" not in result


def test_empty_frozen_snapshot_overrides_editable_cells():
    data = fixture()
    data["calendar_days"][0].update(
        blocked_cells=[40], frozen_blocked=[],
    )
    assert evaluate(data)["category"] == "eligible"


def test_exact_frozen_minutes_produce_warning():
    data = fixture()
    data["calendar_days"][0]["frozen_blocked"] = [[607, 608]]
    assert evaluate(data)["warnings"] == ["unavailability"]


@pytest.mark.parametrize("field,code", [
    ("qualified", "not_qualified"),
    ("role_active", "inactive_role"),
    ("employee_active", "inactive_employee"),
])
def test_fresh_hard_rules(field, code):
    data = fixture()
    if field == "employee_active":
        data["employee"]["active"] = False
    else:
        data[field] = False
    result = evaluate(data)
    assert result["category"] == "blocked"
    assert code in result["hard_blocks"]


def neighbor(first, last):
    return {
        "start_at": first, "end_at": last,
        "timezone": "America/Los_Angeles",
        "business_day_start_minute": 300,
    }


def test_adjacent_week_previous_shift_enforces_rest():
    data = fixture()
    data["assigned"] = [neighbor(
        "2026-10-26T02:00:00+00:00",
        "2026-10-26T09:00:00+00:00",
    )]
    assert "rest_before" in evaluate(data)["hard_blocks"]


def test_overlapping_assignment_blocks():
    data = fixture()
    data["assigned"] = [neighbor(
        "2026-10-26T18:00:00+00:00",
        "2026-10-26T19:00:00+00:00",
    )]
    assert "overlap" in evaluate(data)["hard_blocks"]


def test_other_role_hours_are_added():
    data = fixture()
    data["assigned"] = [neighbor(
        "2026-10-26T15:00:00+00:00",
        "2026-10-26T17:00:00+00:00",
    )]
    assert "daily_hours" in evaluate(data)["warnings"]


def test_mixed_saved_rollovers_fail_explicitly():
    data = fixture()
    row = neighbor(
        "2026-10-25T15:00:00+00:00",
        "2026-10-25T17:00:00+00:00",
    )
    row["business_day_start_minute"] = 0
    data["assigned"] = [row]
    with pytest.raises(ValueError, match="different saved"):
        evaluate(data)


def test_duplicate_or_missing_effective_dates_fail():
    data = fixture()
    data["calendar_days"] *= 2
    with pytest.raises(ValueError, match="Duplicate"):
        evaluate(data)
    data["calendar_days"] = []
    with pytest.raises(ValueError, match="missing"):
        evaluate(data)


def test_rest_policy_must_be_explicit_and_complete():
    with pytest.raises(ValueError, match="complete rest"):
        evaluate_inputs(fixture(), rest_policy={})


def test_reader_fetches_again_in_callers_transaction():
    class Connection:
        def __init__(self):
            self.calls = []
            self.data = fixture()

        def execute(self, statement, parameters):
            self.calls.append((statement, parameters))
            return self

        def fetchone(self):
            return {"result": deepcopy(self.data)}

    conn = Connection()
    assert read_candidate(
        conn, "slot", "employee", rest_policy=REST,
    )["category"] == "eligible"
    conn.data["qualified"] = False
    assert read_candidate(
        conn, "slot", "employee", rest_policy=REST,
    )["category"] == "blocked"
    assert len(conn.calls) == 2
    assert all(parameters == ("slot", "employee")
               for _, parameters in conn.calls)
