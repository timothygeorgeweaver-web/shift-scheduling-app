from copy import deepcopy
import json

import pytest

from app.published_roster import build_roster_snapshot


def fixture():
    return {
        "week": {
            "id": "week", "location_id": "location",
            "week_start_date": "2026-10-26",
            "timezone": "America/Los_Angeles",
            "business_day_start_minute": 300,
            "revision": 7, "internal_secret": "private-week-data",
        },
        "slots": [{
            "id": "slot", "role_id": "cook", "role_name": "Cook",
            "start_at": "2026-10-26T17:00:00+00:00",
            "end_at": "2026-10-27T01:00:00+00:00",
            "note": "private-shift-note",
        }],
        "assignments": [{
            "id": "assignment", "slot_id": "slot",
            "employee_id": "employee", "display_name": "Test Employee",
            "approval_reason": "private-approval",
            "blocked_intervals": "private-availability",
        }],
    }


def test_only_explicit_safe_fields_are_projected():
    result = build_roster_snapshot(fixture())
    serialized = json.dumps(result)
    assert "private-" not in serialized
    assert set(result) == {"schema_version", "week", "shifts"}
    assert result["shifts"][0]["employee"] == {
        "id": "employee", "display_name": "Test Employee",
    }
    assert result["shifts"][0]["estimated_work_minutes"] == 450


def test_snapshot_is_independent_of_later_working_changes():
    data = fixture()
    original = deepcopy(data)
    snapshot = build_roster_snapshot(data)
    assert data == original
    data["assignments"][0]["display_name"] = "Changed"
    data["slots"][0]["role_name"] = "Changed"
    data["week"]["business_day_start_minute"] = 0
    assert snapshot["shifts"][0]["employee"]["display_name"] == "Test Employee"
    assert snapshot["shifts"][0]["role_name"] == "Cook"
    assert snapshot["week"]["business_day_start_minute"] == 300


def test_unassigned_shift_is_explicit():
    data = fixture()
    data["assignments"] = []
    assert build_roster_snapshot(data)["shifts"][0]["employee"] is None


def test_empty_working_week_can_be_projected_without_publishing():
    data = fixture()
    data["slots"] = []
    data["assignments"] = []
    assert build_roster_snapshot(data)["shifts"] == []


def test_following_monday_early_hours_belong_to_sunday():
    data = fixture()
    data["slots"][0].update(
        start_at="2026-11-02T09:00:00+00:00",
        end_at="2026-11-02T12:00:00+00:00",
    )
    row = build_roster_snapshot(data)["shifts"][0]
    assert row["business_date"] == "2026-11-01"
    assert row["local_start"].startswith("2026-11-02T01:00:00")
    assert row["duration_minutes"] == 180
    assert row["meal_estimate_minutes"] == 0


def test_fall_dst_duration_uses_actual_elapsed_time():
    data = fixture()
    data["slots"][0].update(
        start_at="2026-11-01T07:00:00+00:00",
        end_at="2026-11-01T12:00:00+00:00",
    )
    row = build_roster_snapshot(data)["shifts"][0]
    assert row["duration_minutes"] == 300
    assert row["business_date"] == "2026-10-31"


def test_duplicate_assignment_or_slot_is_rejected():
    data = fixture()
    data["assignments"].append(deepcopy(data["assignments"][0]))
    with pytest.raises(ValueError, match="Duplicate current"):
        build_roster_snapshot(data)
    data = fixture()
    data["slots"].append(deepcopy(data["slots"][0]))
    with pytest.raises(ValueError, match="Duplicate shift"):
        build_roster_snapshot(data)


def test_assignment_cannot_reference_another_week():
    data = fixture()
    data["assignments"][0]["slot_id"] = "another-week-slot"
    with pytest.raises(ValueError, match="outside this roster"):
        build_roster_snapshot(data)


@pytest.mark.parametrize("start,end", [
    ("2026-10-26T17:00:00", "2026-10-27T01:00:00"),
    ("2026-10-26T17:00:00+00:00", "2026-10-26T17:00:00+00:00"),
    ("2026-10-26T17:00:00+00:00", "2026-10-26T16:00:00+00:00"),
    ("2026-10-26T17:00:00+00:00", "2026-10-26T18:00:30+00:00"),
])
def test_invalid_endpoints_are_rejected(start, end):
    data = fixture()
    data["slots"][0].update(start_at=start, end_at=end)
    with pytest.raises(ValueError):
        build_roster_snapshot(data)


def test_outside_business_week_is_rejected():
    data = fixture()
    data["slots"][0].update(
        start_at="2026-10-26T08:00:00+00:00",
        end_at="2026-10-26T09:00:00+00:00",
    )
    with pytest.raises(ValueError, match="outside"):
        build_roster_snapshot(data)


def test_saved_job_order_then_earliest_finish():
    data = fixture()
    first = data["slots"][0]
    data["slots"] += [
        {**first, "id": "cashier", "role_id": "cashier", "role_name": "Cashier"},
        {**first, "id": "short-cook", "end_at": "2026-10-26T22:00:00+00:00"},
    ]
    rows = build_roster_snapshot(
        data, role_orders={"cook": 0, "cashier": 1},
    )["shifts"]
    assert [row["id"] for row in rows] == ["short-cook", "slot", "cashier"]
