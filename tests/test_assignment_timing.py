from datetime import datetime, timedelta, timezone

import pytest

from app.assignment_timing import Interval, overlaps, rest_required, violates_rest

UTC = timezone.utc
SETTINGS = {"timezone_name": "America/Los_Angeles"}


def interval(start, end):
    return Interval(datetime.fromisoformat(start), datetime.fromisoformat(end))


def test_overlap_and_adjacent_endpoints():
    first = interval("2026-01-05T09:00-08:00", "2026-01-05T17:00-08:00")
    adjacent = interval("2026-01-05T17:00-08:00", "2026-01-05T18:00-08:00")
    crossing = interval("2026-01-05T16:45-08:00", "2026-01-05T18:00-08:00")
    contained = interval("2026-01-05T10:00-08:00", "2026-01-05T11:00-08:00")
    assert not overlaps(first, adjacent)
    assert overlaps(first, crossing)
    assert overlaps(crossing, first)
    assert overlaps(first, contained)


@pytest.mark.parametrize("ending,expected", [
    ("2026-01-06T19:59-08:00", 0),
    ("2026-01-06T20:00-08:00", 720),
    ("2026-01-06T23:59-08:00", 720),
    ("2026-01-06T00:00-08:00", 720),
    ("2026-01-06T06:00-08:00", 720),
    ("2026-01-06T06:00:01-08:00", 0),
    ("2026-01-06T06:01-08:00", 0),
])
def test_inclusive_rest_window(ending, expected):
    end = datetime.fromisoformat(ending)
    previous = Interval(end - timedelta(hours=4), end)
    assert rest_required(previous, **SETTINGS) == expected


def test_exact_rest_allowed_and_short_rest_denied_across_week_boundary():
    previous = interval("2026-01-04T16:00-08:00", "2026-01-04T22:00-08:00")
    exact = interval("2026-01-05T10:00-08:00", "2026-01-05T16:00-08:00")
    short = interval("2026-01-05T09:45-08:00", "2026-01-05T16:00-08:00")
    assert not violates_rest(previous, exact, **SETTINGS)
    assert violates_rest(previous, short, **SETTINGS)


@pytest.mark.parametrize("end,next_start,expected", [
    ("2026-03-07T22:00-08:00", "2026-03-08T10:00-07:00", True),
    ("2026-03-07T22:00-08:00", "2026-03-08T11:00-07:00", False),
    ("2026-10-31T22:00-07:00", "2026-11-01T09:00-08:00", False),
])
def test_rest_uses_elapsed_time_across_dst(end, next_start, expected):
    finish = datetime.fromisoformat(end)
    start = datetime.fromisoformat(next_start)
    previous = Interval(finish - timedelta(hours=4), finish)
    following = Interval(start, start + timedelta(hours=4))
    assert violates_rest(previous, following, **SETTINGS) is expected


def test_daytime_window_and_configurable_rest():
    previous = interval("2026-01-05T09:00-08:00", "2026-01-05T15:00-08:00")
    assert rest_required(previous, **SETTINGS) == 0
    assert rest_required(
        previous, **SETTINGS,
        window_start=900, window_end=1020, rest_minutes=600,
    ) == 600


def test_overlapping_inputs_rejected_by_rest_check():
    first = interval("2026-01-05T16:00-08:00", "2026-01-05T22:00-08:00")
    second = interval("2026-01-05T21:00-08:00", "2026-01-06T01:00-08:00")
    with pytest.raises(ValueError):
        violates_rest(first, second, **SETTINGS)


def test_invalid_intervals():
    naive = datetime(2026, 1, 5)
    aware = naive.replace(tzinfo=UTC)
    for start, end in [(naive, aware), (aware, naive), (aware, aware)]:
        with pytest.raises(ValueError):
            Interval(start, end)


@pytest.mark.parametrize("settings", [
    {"window_start": True},
    {"window_end": 1440},
    {"rest_minutes": -1},
    {"rest_minutes": 12.5},
])
def test_invalid_rest_settings(settings):
    previous = interval("2026-01-05T16:00-08:00", "2026-01-05T22:00-08:00")
    with pytest.raises(ValueError):
        rest_required(previous, **SETTINGS, **settings)
