from datetime import date, datetime

import pytest

from app.assignment_eligibility import evaluate_assignment
from app.assignment_timing import Interval


ZONE = "America/Los_Angeles"


def shift(start, end):
    return Interval(datetime.fromisoformat(start), datetime.fromisoformat(end))


def evaluate(candidate, assigned, policies, **changes):
    options = {
        "employee_active": True, "role_active": True, "qualified": True,
        "timezone_name": ZONE, "rollover_minute": 300,
        "rest_minutes": 0,
    }
    options.update(changes)
    return evaluate_assignment(
        candidate, assigned, assigned_policies=policies, **options,
    )


def policy(rollover, zone=ZONE):
    return {"timezone_name": zone, "rollover_minute": rollover}


def test_midnight_shift_stays_on_its_saved_next_date():
    candidate = shift("2026-10-26T10:00-07:00", "2026-10-26T18:00-07:00")
    early = shift("2026-10-27T01:00-07:00", "2026-10-27T03:00-07:00")
    result = evaluate(candidate, [early], [policy(0)])
    assert dict(result.daily_work) == {date(2026, 10, 26): 450}
    assert "daily_hours" not in result.warnings

    # Reinterpreting the neighbor under 05:00 would incorrectly add two hours.
    changed = evaluate(candidate, [early], [policy(300)])
    assert dict(changed.daily_work) == {date(2026, 10, 26): 570}
    assert "daily_hours" in changed.warnings


def test_saved_date_labels_combine_without_reclassifying_early_hours():
    candidate = shift("2026-10-26T10:00-07:00", "2026-10-26T18:00-07:00")
    early = shift("2026-10-26T01:00-07:00", "2026-10-26T03:00-07:00")
    result = evaluate(candidate, [early], [policy(0)])
    assert dict(result.daily_work) == {date(2026, 10, 26): 570}
    assert "daily_hours" in result.warnings


def test_each_saved_timezone_is_used_for_labor_allocation():
    candidate = shift("2026-10-26T10:00-07:00", "2026-10-26T18:00-07:00")
    other = shift("2026-10-26T23:00-07:00", "2026-10-27T01:00-07:00")
    result = evaluate(candidate, [other], [policy(0, "UTC")])
    assert dict(result.daily_work) == {date(2026, 10, 26): 450}
    assert "daily_hours" not in result.warnings


def test_mixed_rollover_does_not_hide_real_overlap():
    candidate = shift("2026-10-26T10:00-07:00", "2026-10-26T18:00-07:00")
    other = shift("2026-10-26T12:00-07:00", "2026-10-26T13:00-07:00")
    assert "overlap" in evaluate(
        candidate, [other], [policy(0)],
    ).hard_blocks


def test_mixed_rollover_does_not_hide_insufficient_elapsed_rest():
    candidate = shift("2026-10-26T09:00-07:00", "2026-10-26T15:00-07:00")
    previous = shift("2026-10-25T16:00-07:00", "2026-10-25T22:00-07:00")
    result = evaluate(
        candidate, [previous], [policy(0)], rest_minutes=720,
    )
    assert "rest_before" in result.hard_blocks


def test_meal_estimate_keeps_exact_saved_day_proportions():
    candidate = shift("2026-10-27T10:00-07:00", "2026-10-27T11:00-07:00")
    overnight = shift("2026-10-26T20:00-07:00", "2026-10-27T04:00-07:00")
    result = evaluate(candidate, [overnight], [policy(0)])
    # Eight-hour neighbor: 450 net minutes, split evenly at saved midnight.
    assert dict(result.daily_work) == {date(2026, 10, 27): 285}


@pytest.mark.parametrize("policies", [
    [],
    [{}],
    [{"timezone_name": ZONE}],
    [policy(0), policy(300)],
])
def test_missing_or_misaligned_saved_policies_fail_closed(policies):
    candidate = shift("2026-10-26T10:00-07:00", "2026-10-26T18:00-07:00")
    other = shift("2026-10-27T01:00-07:00", "2026-10-27T03:00-07:00")
    with pytest.raises(ValueError):
        evaluate(candidate, [other], policies)
