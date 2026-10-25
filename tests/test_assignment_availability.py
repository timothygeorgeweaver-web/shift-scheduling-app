from datetime import date, datetime, timezone

import pytest

from app.assignment_availability import unavailable_during
from app.assignment_timing import Interval

UTC = timezone.utc


def instant(value):
    return datetime.fromisoformat(value).astimezone(UTC)


def shift(first, last):
    return Interval(instant(first), instant(last))


def check(candidate, days, zone="America/Los_Angeles"):
    return unavailable_during(candidate, days, timezone_name=zone)


def test_empty_blocks_mean_available():
    candidate = shift("2026-10-26T17:00:00Z", "2026-10-27T01:00:00Z")
    assert check(candidate, {date(2026, 10, 26): []}) == ()


def test_overnight_checks_following_calendar_date():
    candidate = shift("2026-10-27T06:30:00Z", "2026-10-27T09:00:00Z")
    result = check(candidate, {
        date(2026, 10, 26): [],
        date(2026, 10, 27): [(30, 75)],
    })
    assert result == (
        shift("2026-10-27T07:30:00Z", "2026-10-27T08:15:00Z"),
    )


def test_exact_non_quarter_frozen_minutes_are_preserved():
    candidate = shift("2026-10-26T17:00:00Z", "2026-10-26T18:00:00Z")
    assert check(candidate, {date(2026, 10, 26): [(607, 623)]}) == (
        shift("2026-10-26T17:07:00Z", "2026-10-26T17:23:00Z"),
    )


def test_touching_endpoints_do_not_conflict():
    candidate = shift("2026-10-26T17:00:00Z", "2026-10-26T18:00:00Z")
    assert check(candidate, {
        date(2026, 10, 26): [(540, 600), (660, 720)],
    }) == ()


def test_repeated_clock_minutes_block_both_occurrences():
    candidate = shift("2026-11-01T08:00:00Z", "2026-11-01T10:00:00Z")
    assert check(candidate, {date(2026, 11, 1): [(75, 90)]}) == (
        shift("2026-11-01T08:15:00Z", "2026-11-01T08:30:00Z"),
        shift("2026-11-01T09:15:00Z", "2026-11-01T09:30:00Z"),
    )


def test_nonexistent_clock_minutes_consume_no_actual_time():
    candidate = shift("2026-03-08T09:00:00Z", "2026-03-08T11:00:00Z")
    assert check(candidate, {date(2026, 3, 8): [(120, 180)]}) == ()


def test_adjacent_blocks_merge_and_clip_to_shift():
    candidate = shift("2026-10-26T17:10:00Z", "2026-10-26T17:40:00Z")
    assert check(candidate, {
        date(2026, 10, 26): [(600, 615), (615, 630), (630, 660)],
    }) == (candidate,)


def test_missing_date_is_not_silently_available():
    candidate = shift("2026-10-27T06:30:00Z", "2026-10-27T09:00:00Z")
    with pytest.raises(ValueError, match="missing"):
        check(candidate, {date(2026, 10, 26): []})


@pytest.mark.parametrize("ranges", [
    [(True, 30)], [(0, 1441)], [(60, 60)], [(90, 30)],
    [("15", 30)], [(0, 15, 30)],
])
def test_invalid_effective_ranges_are_rejected(ranges):
    candidate = shift("2026-10-26T17:00:00Z", "2026-10-26T18:00:00Z")
    with pytest.raises(ValueError):
        check(candidate, {date(2026, 10, 26): ranges})


def test_subminute_shift_is_not_rounded():
    candidate = shift("2026-10-26T17:00:01Z", "2026-10-26T18:00:00Z")
    with pytest.raises(ValueError, match="whole minutes"):
        check(candidate, {date(2026, 10, 26): []})
