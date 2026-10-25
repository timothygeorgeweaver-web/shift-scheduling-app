from datetime import date, datetime

import pytest

from app.assignment_eligibility import evaluate_assignment
from app.assignment_timing import Interval

POLICY = dict(
    employee_active=True, role_active=True, qualified=True,
    timezone_name="America/Los_Angeles",
)


def interval(start, end):
    return Interval(datetime.fromisoformat(start), datetime.fromisoformat(end))


def shift(start, end, day="2027-01-04", end_day=None):
    return interval(
        f"{day}T{start}-08:00", f"{end_day or day}T{end}-08:00",
    )


def evaluate(candidate, assigned=(), unavailable=(), **changes):
    return evaluate_assignment(
        candidate, assigned, unavailable, **{**POLICY, **changes},
    )


def test_active_qualified_candidate_without_blocks_is_eligible():
    result = evaluate(shift("09:00", "17:00"))
    assert result.category == "eligible"
    assert result.hard_blocks == result.warnings == ()
    assert dict(result.daily_work) == {date(2027, 1, 4): 450}


@pytest.mark.parametrize("flag,reason", [
    ("employee_active", "inactive_employee"),
    ("role_active", "inactive_role"),
    ("qualified", "not_qualified"),
])
def test_active_and_qualification_failures_are_hard(flag, reason):
    result = evaluate(shift("09:00", "13:00"), **{flag: False})
    assert result.category == "blocked"
    assert reason in result.hard_blocks


def test_overlap_is_hard():
    result = evaluate(shift("12:45", "16:00"), [shift("09:00", "13:00")])
    assert result.category == "blocked"
    assert "overlap" in result.hard_blocks


def test_adjacent_shifts_and_exactly_eight_hours_are_allowed():
    result = evaluate(shift("13:00", "17:00"), [shift("09:00", "13:00")])
    assert result.category == "eligible"
    assert dict(result.daily_work)[date(2027, 1, 4)] == 480


def test_all_roles_contribute_to_daily_warning():
    result = evaluate(shift("13:00", "18:00"), [shift("09:00", "13:00")])
    assert result.category == "override_required"
    assert result.warnings == ("daily_hours",)
    assert dict(result.daily_work)[date(2027, 1, 4)] == 540


@pytest.mark.parametrize("end,expected", [
    ("17:30", "eligible"),
    ("17:45", "override_required"),
])
def test_daily_warning_uses_estimated_work_after_single_meal(end, expected):
    result = evaluate(shift("09:00", end))
    assert result.category == expected


def test_overnight_labor_uses_prior_business_day():
    result = evaluate(
        shift("20:00", "04:00", end_day="2027-01-05"),
        [shift("10:00", "11:00")],
    )
    assert result.category == "override_required"
    assert result.warnings == ("daily_hours",)
    assert dict(result.daily_work) == {date(2027, 1, 4): 510}


def test_availability_uses_full_shift_without_inventing_a_meal_interval():
    result = evaluate(
        shift("09:00", "17:30"), unavailable=[shift("17:15", "17:30")],
    )
    assert result.category == "override_required"
    assert result.warnings == ("unavailability",)


def test_adjacent_unavailability_does_not_intersect():
    result = evaluate(
        shift("09:00", "13:00"),
        unavailable=[shift("08:00", "09:00"), shift("13:00", "14:00")],
    )
    assert result.category == "eligible"


def test_all_soft_warnings_are_reported_together():
    result = evaluate(
        shift("13:00", "18:00"), [shift("09:00", "13:00")],
        [shift("17:00", "18:00")],
    )
    assert result.category == "override_required"
    assert result.warnings == ("unavailability", "daily_hours")


def test_soft_warnings_cannot_make_a_hard_failure_overridable():
    result = evaluate(
        shift("13:00", "18:00"), [shift("09:00", "13:00")],
        [shift("17:00", "18:00")], qualified=False,
    )
    assert result.category == "blocked"
    assert "not_qualified" in result.hard_blocks
    assert result.warnings == ("unavailability", "daily_hours")


def test_previous_week_rest_is_checked_and_exact_rest_passes():
    previous = shift("16:00", "22:00", day="2027-01-03")
    assert "rest_before" in evaluate(shift("09:00", "15:00"), [previous]).hard_blocks
    assert evaluate(shift("10:00", "16:00"), [previous]).category == "eligible"


def test_assignment_must_preserve_rest_before_following_shift():
    following = shift("09:00", "15:00", day="2027-01-05")
    result = evaluate(shift("16:00", "22:00"), [following])
    assert result.category == "blocked"
    assert "rest_after" in result.hard_blocks


@pytest.mark.parametrize("previous,candidate,blocked", [
    (
        ("2026-03-07T16:00-08:00", "2026-03-07T22:00-08:00"),
        ("2026-03-08T10:00-07:00", "2026-03-08T16:00-07:00"), True,
    ),
    (
        ("2026-10-31T16:00-07:00", "2026-10-31T22:00-07:00"),
        ("2026-11-01T09:00-08:00", "2026-11-01T15:00-08:00"), False,
    ),
])
def test_rest_uses_elapsed_hours_across_dst(previous, candidate, blocked):
    result = evaluate(interval(*candidate), [interval(*previous)])
    assert ("rest_before" in result.hard_blocks) is blocked


def test_non_boolean_qualification_is_rejected():
    with pytest.raises(ValueError):
        evaluate(shift("09:00", "13:00"), qualified="yes")
