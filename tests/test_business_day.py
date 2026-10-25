from datetime import date, datetime, timedelta, timezone

import pytest

from app.business_day import (
    business_date, business_date_at, business_day_bounds,
    planner_cell_for, planner_cell_start, validate_rollover,
)

ZONE = "America/Los_Angeles"


@pytest.mark.parametrize("clock,expected", [
    ("2026-10-26T00:00:00", date(2026, 10, 25)),
    ("2026-10-26T04:59:59", date(2026, 10, 25)),
    ("2026-10-26T05:00:00", date(2026, 10, 26)),
    ("2026-10-26T23:59:59", date(2026, 10, 26)),
    ("2026-01-01T02:00:00", date(2025, 12, 31)),
])
def test_default_business_date(clock, expected):
    assert business_date(datetime.fromisoformat(clock)) == expected


def test_custom_and_midnight_rollovers():
    local = datetime(2026, 10, 26, 6)
    assert business_date(local, 420) == date(2026, 10, 25)
    assert business_date(local, 0) == date(2026, 10, 26)


def test_overnight_shift_stays_in_one_business_day():
    start = datetime(2026, 10, 26, 20)
    end = datetime(2026, 10, 27, 2)
    assert business_date(start) == business_date(end) == date(2026, 10, 26)


@pytest.mark.parametrize("rollover", [0, 15, 300, 420, 1425])
def test_all_cells_round_trip_without_changing_calendar_times(rollover):
    first = date(2026, 12, 28)
    for offset in range(7):
        day = first + timedelta(days=offset)
        for cell in range(96):
            local = planner_cell_start(day, cell, rollover)
            assert planner_cell_for(local, rollover) == (day, cell)
            assert business_date(local, rollover) == day


def test_default_column_end_and_next_column_start():
    monday = date(2026, 10, 26)
    assert planner_cell_start(monday, 0) == datetime(2026, 10, 26, 5)
    assert planner_cell_start(monday, 76) == datetime(2026, 10, 27, 0)
    assert planner_cell_start(monday, 95) == datetime(2026, 10, 27, 4, 45)
    assert planner_cell_start(monday, 95) + timedelta(minutes=15) == \
        planner_cell_start(monday + timedelta(days=1), 0)


def test_actual_instant_converted_using_location_timezone():
    instant = datetime(2026, 10, 27, 9, tzinfo=timezone.utc)
    assert business_date_at(instant, ZONE) == date(2026, 10, 26)


@pytest.mark.parametrize("day,hours", [
    (date(2026, 3, 7), 23),
    (date(2026, 10, 31), 25),
    (date(2026, 10, 26), 24),
])
def test_elapsed_business_day_duration_across_dst(day, hours):
    start, end = business_day_bounds(day, ZONE)
    assert end - start == timedelta(hours=hours)
    assert business_date_at(start, ZONE) == day
    assert business_date_at(end - timedelta(microseconds=1), ZONE) == day
    assert business_date_at(end, ZONE) == day + timedelta(days=1)


@pytest.mark.parametrize("day,rollover", [
    (date(2026, 3, 8), 150),
    (date(2026, 11, 1), 90),
])
def test_ambiguous_or_nonexistent_boundary_is_not_guessed(day, rollover):
    with pytest.raises(ValueError):
        business_day_bounds(day, ZONE, rollover)


@pytest.mark.parametrize("value", [True, -15, 1440, 301, 300.0, "05:00", None])
def test_invalid_rollover(value):
    with pytest.raises(ValueError):
        validate_rollover(value)


@pytest.mark.parametrize("cell", [True, -1, 96, 1.0, "1"])
def test_invalid_cell(cell):
    with pytest.raises(ValueError):
        planner_cell_start(date(2026, 10, 26), cell)


def test_reject_inexact_or_aware_cell_input():
    for value in [
        datetime(2026, 10, 26, 5, 1),
        datetime(2026, 10, 26, 5, 0, 1),
        datetime(2026, 10, 26, 5, tzinfo=timezone.utc),
    ]:
        with pytest.raises(ValueError):
            planner_cell_for(value)


def test_actual_instant_requires_timezone():
    with pytest.raises(ValueError):
        business_date_at(datetime(2026, 10, 26), ZONE)


def test_calendar_date_required():
    with pytest.raises(ValueError):
        planner_cell_start(datetime(2026, 10, 26), 0)
