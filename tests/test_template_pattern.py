from copy import deepcopy
from datetime import date

import pytest
from pydantic import ValidationError

from app.template_pattern import (
    TemplateDraft, TemplateShift, from_snapshot, to_snapshot,
)

ROLE = "11111111-1111-4111-8111-111111111111"


def shift(**changes):
    return TemplateShift(**{
        "role_id": ROLE,
        "weekday": 0,
        "start_minute": 555,
        "end_minute": 1035,
        **changes,
    })


@pytest.mark.parametrize("week_start", range(7))
def test_round_trip_keeps_weekdays_times_notes_and_duplicate_slots(week_start):
    original = TemplateDraft(name="  Standard week  ", slots=[
        shift(note="First"),
        shift(note="Second"),
        shift(weekday=6, start_minute=1320, end_minute=360,
              ends_next_day=True, note="Sunday overnight"),
    ])
    snapshot = to_snapshot(original, week_start)
    restored = from_snapshot(snapshot, original.name)
    assert restored == original
    assert restored.name == "Standard week"
    assert len(restored.slots) == 3
    assert date.fromisoformat(snapshot["week"]["week_start_date"]).weekday() == week_start


def test_blank_template_is_valid_and_stays_blank():
    draft = TemplateDraft(name="Blank")
    assert from_snapshot(to_snapshot(draft, 0), "Blank") == draft
    assert draft.slots == []


def test_full_day_uses_same_time_on_next_day():
    value = shift(start_minute=0, end_minute=0, ends_next_day=True)
    draft = TemplateDraft(name="Full day", slots=[value])
    snapshot = to_snapshot(draft, 0)
    assert snapshot["slots"][0]["local_start"] == "2000-01-03T00:00:00"
    assert snapshot["slots"][0]["local_end"] == "2000-01-04T00:00:00"


@pytest.mark.parametrize("changes", [
    {"weekday": True},
    {"weekday": "1"},
    {"weekday": -1},
    {"weekday": 7},
    {"start_minute": True},
    {"start_minute": 541},
    {"end_minute": 901},
    {"start_minute": 1440},
    {"end_minute": 1440},
    {"ends_next_day": 1},
    {"end_minute": 555},
    {"end_minute": 540},
    {"note": "x"*501},
    {"employee_id": ROLE},
])
def test_invalid_template_shift_rejected(changes):
    with pytest.raises(ValidationError):
        shift(**changes)


@pytest.mark.parametrize("value", [True, -1, 7, "0"])
def test_invalid_week_start_rejected(value):
    with pytest.raises(ValueError):
        to_snapshot(TemplateDraft(name="Test"), value)


@pytest.mark.parametrize("name", ["", "   ", "x"*101])
def test_invalid_name_rejected(name):
    with pytest.raises(ValidationError):
        TemplateDraft(name=name)


def test_saved_off_grid_time_is_rejected_without_rounding():
    original = to_snapshot(TemplateDraft(name="Test", slots=[shift()]), 0)
    changed = deepcopy(original)
    changed["slots"][0]["local_start"] = "2000-01-03T09:16:00"
    with pytest.raises(ValueError):
        from_snapshot(changed, "Test")
    assert changed["slots"][0]["local_start"] == "2000-01-03T09:16:00"
    assert original["slots"][0]["local_start"] == "2000-01-03T09:15:00"


def test_saved_endpoints_cannot_silently_include_seconds():
    snapshot = to_snapshot(TemplateDraft(name="Test", slots=[shift()]), 0)
    snapshot["slots"][0]["local_end"] = "2000-01-03T17:15:01"
    with pytest.raises(ValueError):
        from_snapshot(snapshot, "Test")


def test_saved_shift_must_start_inside_its_week():
    snapshot = to_snapshot(TemplateDraft(name="Test", slots=[shift()]), 0)
    snapshot["slots"][0]["local_start"] = "2000-01-10T09:15:00"
    snapshot["slots"][0]["local_end"] = "2000-01-10T17:15:00"
    with pytest.raises(ValueError):
        from_snapshot(snapshot, "Test")
