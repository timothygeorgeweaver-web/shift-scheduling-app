from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from app.business_day import business_date
from app.template_pattern import TemplateDraft, TemplateShift, from_snapshot, to_snapshot

ROLE = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("rollover", [0, 300, 420])
@pytest.mark.parametrize("week_start", range(7))
def test_round_trip_preserves_business_weekdays(rollover, week_start):
    slots = [
        TemplateShift(
            role_id=ROLE, weekday=weekday,
            start_minute=minute, end_minute=(minute+60) % 1440,
            ends_next_day=minute+60 >= 1440,
            note=f"{weekday}/{minute}",
        )
        for weekday in range(7)
        for minute in (0, 285, 300, 1425)
    ]
    draft = TemplateDraft(name="Business week", slots=slots)
    snapshot = to_snapshot(draft, week_start, rollover)
    before = deepcopy(snapshot)
    assert from_snapshot(snapshot, draft.name, rollover) == draft
    assert from_snapshot(snapshot, draft.name) == draft
    assert snapshot == before

    first = date.fromisoformat(snapshot["week"]["week_start_date"])
    for value, saved in zip(slots, snapshot["slots"]):
        start = datetime.fromisoformat(saved["local_start"])
        day = business_date(start, rollover)
        assert day.weekday() == value.weekday
        assert first <= day <= first+timedelta(days=6)


def test_sunday_early_hours_use_following_monday_calendar_date():
    draft = TemplateDraft(name="Late Sunday", slots=[
        TemplateShift(
            role_id=ROLE, weekday=6, start_minute=120, end_minute=240,
        ),
    ])
    snapshot = to_snapshot(draft, 0, 300)
    assert snapshot["slots"][0]["local_start"] == "2000-01-10T02:00:00"
    assert from_snapshot(snapshot, draft.name, 300) == draft


def test_legacy_snapshot_without_rollover_remains_midnight():
    draft = TemplateDraft(name="Legacy", slots=[
        TemplateShift(role_id=ROLE, weekday=0, start_minute=0, end_minute=60),
    ])
    snapshot = to_snapshot(draft, 0)
    del snapshot["week"]["business_day_start_minute"]
    assert from_snapshot(snapshot, draft.name) == draft
    assert snapshot["slots"][0]["local_start"] == "2000-01-03T00:00:00"


def test_conflicting_rollover_metadata_is_rejected():
    snapshot = to_snapshot(TemplateDraft(name="Conflict"), 0, 300)
    with pytest.raises(ValueError):
        from_snapshot(snapshot, "Conflict", 0)


@pytest.mark.parametrize("value", [True, 301, -15, 1440, "300"])
def test_invalid_saved_rollover_is_rejected(value):
    snapshot = to_snapshot(TemplateDraft(name="Invalid"), 0)
    snapshot["week"]["business_day_start_minute"] = value
    with pytest.raises(ValueError):
        from_snapshot(snapshot, "Invalid")
