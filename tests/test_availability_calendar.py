from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.availability_calendar import (
    CalendarPolicy, availability_gate, cutoff_for_week,
    local_today, week_start_for,
)

UTC = timezone.utc


@pytest.mark.parametrize("offset", range(7))
def test_every_day_maps_to_its_monday(offset):
    monday = date(2026, 10, 26)
    assert week_start_for(monday + timedelta(days=offset)) == monday


def test_default_cutoff_is_previous_monday_at_five_pacific():
    policy = CalendarPolicy()
    assert cutoff_for_week(date(2026, 10, 26), policy) == datetime(
        2026, 10, 20, 0, 0, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "difference, editable",
    [(-1, True), (0, False), (1, False)],
)
def test_exact_cutoff_boundary(difference, editable):
    policy = CalendarPolicy()
    target = date(2026, 10, 28)
    cutoff = cutoff_for_week(date(2026, 10, 26), policy)
    result = availability_gate(
        target, cutoff + timedelta(microseconds=difference), policy
    )
    assert result.editable is editable
    assert result.reason == (None if editable else "cutoff_reached")


def test_published_week_is_locked_before_cutoff():
    result = availability_gate(
        date(2026, 10, 26),
        datetime(2026, 10, 1, tzinfo=UTC),
        CalendarPolicy(),
        published=True,
    )
    assert not result.editable
    assert result.reason == "published_week"


def test_publication_reason_takes_precedence_after_cutoff():
    result = availability_gate(
        date(2026, 10, 26),
        datetime(2026, 10, 30, tzinfo=UTC),
        CalendarPolicy(),
        published=True,
    )
    assert result.reason == "published_week"


def test_week_start_and_cutoff_are_configurable():
    policy = CalendarPolicy(
        week_start=6, cutoff_days_before=3, cutoff_time=time(12, 30)
    )
    assert week_start_for(date(2026, 10, 26), 6) == date(2026, 10, 25)
    assert cutoff_for_week(date(2026, 10, 25), policy) == datetime(
        2026, 10, 22, 19, 30, tzinfo=UTC
    )


def test_local_date_is_not_utc_date():
    policy = CalendarPolicy()
    instant = datetime(2026, 10, 26, 1, 0, tzinfo=UTC)
    assert local_today(instant, policy) == date(2026, 10, 25)
    assert week_start_for(local_today(instant, policy)) == date(2026, 10, 19)


def test_spring_cutoffs_remain_five_pm_local():
    policy = CalendarPolicy()
    before = cutoff_for_week(date(2027, 3, 15), policy)
    after = cutoff_for_week(date(2027, 3, 22), policy)
    assert after - before == timedelta(hours=167)
    assert before.hour == 1
    assert after.hour == 0


def test_autumn_cutoffs_remain_five_pm_local():
    policy = CalendarPolicy()
    before = cutoff_for_week(date(2027, 11, 8), policy)
    after = cutoff_for_week(date(2027, 11, 15), policy)
    assert after - before == timedelta(hours=169)
    assert before.hour == 0
    assert after.hour == 1


@pytest.mark.parametrize(
    "start,clock",
    [
        (date(2027, 3, 15), time(2, 30)),
        (date(2027, 11, 8), time(1, 30)),
    ],
)
def test_dst_gap_or_ambiguous_cutoff_is_rejected(start, clock):
    policy = CalendarPolicy(cutoff_days_before=1, cutoff_time=clock)
    with pytest.raises(ValueError, match="nonexistent or ambiguous"):
        cutoff_for_week(start, policy)


def test_unaligned_week_date_is_rejected():
    with pytest.raises(ValueError, match="start of a week"):
        cutoff_for_week(date(2026, 10, 27), CalendarPolicy())


def test_naive_current_time_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        availability_gate(
            date(2026, 10, 26), datetime(2026, 10, 1), CalendarPolicy()
        )


@pytest.mark.parametrize("changes", [
    {"week_start": True},
    {"week_start": 7},
    {"cutoff_days_before": -1},
    {"cutoff_days_before": True},
    {"cutoff_time": time(17, tzinfo=UTC)},
])
def test_invalid_policy_is_rejected(changes):
    with pytest.raises(ValueError):
        CalendarPolicy(**changes)
