from datetime import date, datetime, timedelta

import pytest

from app.publication_locks import publication_lock_plan


ZONE = "America/Los_Angeles"


def test_default_rollover_preserves_both_adjacent_week_edges():
    plan = publication_lock_plan(date(2026, 10, 26), ZONE, 300)
    windows = plan["calendar_windows"]
    assert len(windows) == 8
    assert windows[0] == {
        "date": "2026-10-26", "start_minute": 300, "end_minute": 1440,
    }
    assert windows[-1] == {
        "date": "2026-11-02", "start_minute": 0, "end_minute": 300,
    }


@pytest.mark.parametrize("rollover", [0, 15, 300, 1425])
def test_exactly_672_quarter_hour_cells_and_no_adjacent_cells(rollover):
    first = date(2026, 10, 26)
    windows = publication_lock_plan(first, ZONE, rollover)["calendar_windows"]
    actual = {
        (row["date"], minute)
        for row in windows
        for minute in range(row["start_minute"], row["end_minute"], 15)
    }
    expected = set()
    for day in range(7):
        for cell in range(96):
            offset = rollover+cell*15
            calendar_day = first+timedelta(days=day+offset//1440)
            expected.add((calendar_day.isoformat(), offset % 1440))
    assert actual == expected
    assert len(actual) == 672


def test_midnight_week_uses_only_seven_calendar_dates():
    rows = publication_lock_plan(date(2026, 10, 26), ZONE, 0)["calendar_windows"]
    assert len(rows) == 7
    assert rows[-1]["date"] == "2026-11-01"
    assert all(row["start_minute"] == 0 and row["end_minute"] == 1440
               for row in rows)


@pytest.mark.parametrize("first,hours", [
    (date(2026, 3, 2), 167),
    (date(2026, 10, 26), 169),
    (date(2026, 10, 19), 168),
])
def test_utc_week_duration_uses_actual_dst_elapsed_hours(first, hours):
    plan = publication_lock_plan(first, ZONE, 300)
    elapsed = (
        datetime.fromisoformat(plan["ends_at"])
        - datetime.fromisoformat(plan["starts_at"])
    )
    assert elapsed == timedelta(hours=hours)


@pytest.mark.parametrize("first,rollover", [
    (date(2026, 3, 2), 150),
    (date(2026, 10, 26), 90),
])
def test_missing_or_repeated_rollover_inside_week_is_rejected(first, rollover):
    with pytest.raises(ValueError, match="ambiguous or nonexistent"):
        publication_lock_plan(first, ZONE, rollover)


@pytest.mark.parametrize("rollover", [True, -15, 1440, 301, "300"])
def test_invalid_rollovers_are_rejected(rollover):
    with pytest.raises(ValueError):
        publication_lock_plan(date(2026, 10, 26), ZONE, rollover)
