from datetime import date, datetime
from fractions import Fraction

import pytest

from app.business_day_labor import allocate_shift_labor
from app.shift_timing import calculate_shift_timing

ZONE = "America/Los_Angeles"


def allocation(start, end, rollover=300):
    return allocate_shift_labor(
        datetime.fromisoformat(start), datetime.fromisoformat(end),
        timezone_name=ZONE, rollover_minute=rollover,
    )


def test_overnight_before_rollover_belongs_to_one_business_day():
    rows = allocation("2027-01-04T20:00-08:00", "2027-01-05T04:00-08:00")
    assert len(rows) == 1
    assert rows[0].business_date == date(2027, 1, 4)
    assert rows[0].duration_minutes == 480
    assert rows[0].meal_estimate_minutes == 30
    assert rows[0].estimated_work_minutes == 450


def test_crossing_rollover_splits_meal_proportionally():
    rows = allocation("2027-01-04T22:00-08:00", "2027-01-05T06:00-08:00")
    assert [row.business_date for row in rows] == [date(2027, 1, 4), date(2027, 1, 5)]
    assert [row.duration_minutes for row in rows] == [420, 60]
    assert [row.meal_estimate_minutes for row in rows] == [Fraction(105, 4), Fraction(15, 4)]
    assert sum(row.estimated_work_minutes for row in rows) == 450


def test_fractional_allocations_are_not_rounded():
    rows = allocation("2027-01-05T00:00-08:00", "2027-01-05T07:00-08:00")
    assert [row.estimated_work_minutes for row in rows] == [Fraction(1950, 7), Fraction(780, 7)]
    assert sum(row.meal_estimate_minutes for row in rows) == 30
    assert sum(row.estimated_work_minutes for row in rows) == 390


def test_exact_rollover_end_creates_no_empty_next_day():
    rows = allocation("2027-01-04T23:00-08:00", "2027-01-05T05:00-08:00")
    assert len(rows) == 1
    assert rows[0].business_date == date(2027, 1, 4)
    assert rows[0].duration_minutes == rows[0].estimated_work_minutes == 360
    assert rows[0].meal_estimate_minutes == 0


def test_configured_rollover_changes_grouping_without_changing_total():
    start, end = "2027-01-04T22:00-08:00", "2027-01-05T06:00-08:00"
    assert len(allocation(start, end, 300)) == 2
    rows = allocation(start, end, 360)
    assert len(rows) == 1 and rows[0].estimated_work_minutes == 450


def test_midnight_policy_retains_calendar_day_split():
    rows = allocation("2027-01-04T22:00-08:00", "2027-01-05T06:00-08:00", 0)
    assert [row.duration_minutes for row in rows] == [120, 360]
    assert [row.meal_estimate_minutes for row in rows] == [Fraction(15, 2), Fraction(45, 2)]


@pytest.mark.parametrize("day,start,end,next_day", [
    (date(2027, 1, 4), 540, 900, False),
    (date(2027, 1, 4), 540, 915, False),
    (date(2026, 3, 7), 300, 300, True),
    (date(2026, 10, 31), 300, 300, True),
])
def test_totals_match_approved_shift_estimate(day, start, end, next_day):
    timing = calculate_shift_timing(
        day, start, end, ends_next_day=next_day, timezone_name=ZONE,
    )
    rows = allocate_shift_labor(timing.start_utc, timing.end_utc, timezone_name=ZONE)
    assert sum(row.duration_minutes for row in rows) == timing.duration_minutes
    assert sum(row.meal_estimate_minutes for row in rows) == timing.meal_estimate_minutes
    assert sum(row.estimated_work_minutes for row in rows) == timing.estimated_work_minutes


@pytest.mark.parametrize("start,end,minutes", [
    ("2026-03-07T05:00-08:00", "2026-03-08T05:00-07:00", 1380),
    ("2026-10-31T05:00-07:00", "2026-11-01T05:00-08:00", 1500),
])
def test_dst_uses_elapsed_day_length_and_one_meal(start, end, minutes):
    rows = allocation(start, end)
    assert len(rows) == 1
    assert rows[0].duration_minutes == minutes
    assert rows[0].meal_estimate_minutes == 30
    assert rows[0].estimated_work_minutes == minutes-30


@pytest.mark.parametrize("start,end,rollover", [
    ("2026-03-07T20:00-08:00", "2026-03-08T08:00-07:00", 150),
    ("2026-10-31T20:00-07:00", "2026-11-01T08:00-08:00", 90),
])
def test_invalid_dst_rollover_is_not_guessed(start, end, rollover):
    with pytest.raises(ValueError, match="boundary"):
        allocation(start, end, rollover)


@pytest.mark.parametrize("start,end", [
    ("2027-01-04T09:00", "2027-01-04T17:00-08:00"),
    ("2027-01-04T09:00-08:00", "2027-01-04T09:00-08:00"),
    ("2027-01-04T10:00-08:00", "2027-01-04T09:00-08:00"),
    ("2027-01-04T09:00:01-08:00", "2027-01-04T17:00-08:00"),
])
def test_invalid_endpoints_rejected(start, end):
    with pytest.raises(ValueError):
        allocation(start, end)
