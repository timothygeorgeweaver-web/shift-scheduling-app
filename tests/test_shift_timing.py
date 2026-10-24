from datetime import date, datetime, timezone

import pytest

from app.shift_timing import calculate_shift_timing

MONDAY = date(2027, 1, 4)


@pytest.mark.parametrize("duration,deduction,working", [
    (60, 0, 60),
    (359, 0, 359),
    (360, 0, 360),
    (361, 30, 331),
    (375, 30, 345),
    (480, 30, 450),
    (510, 30, 480),
    (720, 30, 690),
])
def test_approved_per_shift_deduction(duration, deduction, working):
    result = calculate_shift_timing(MONDAY, 0, duration)
    assert result.duration_minutes == duration
    assert result.meal_estimate_minutes == deduction
    assert result.estimated_work_minutes == working


def test_nine_to_five_thirty_is_eight_estimated_work_hours():
    result = calculate_shift_timing(MONDAY, 540, 1050)
    assert result.duration_minutes == 510
    assert result.estimated_work_minutes == 480


def test_deduction_does_not_change_actual_shift_endpoints():
    result = calculate_shift_timing(MONDAY, 540, 1050)
    assert result.start_utc == datetime(2027, 1, 4, 17, 0, tzinfo=timezone.utc)
    assert result.end_utc == datetime(2027, 1, 5, 1, 30, tzinfo=timezone.utc)
    assert (result.end_utc-result.start_utc).total_seconds() == 510*60


def test_overnight_shift_remains_one_shift_with_one_deduction():
    result = calculate_shift_timing(
        MONDAY, 1320, 360, ends_next_day=True
    )
    assert result.duration_minutes == 480
    assert result.meal_estimate_minutes == 30
    assert result.estimated_work_minutes == 450


def test_same_day_24_hour_end_means_midnight():
    result = calculate_shift_timing(MONDAY, 1200, 1440)
    assert result.duration_minutes == 240
    assert result.end_utc == datetime(2027, 1, 5, 8, 0, tzinfo=timezone.utc)


def test_spring_forward_uses_actual_elapsed_time():
    result = calculate_shift_timing(date(2026, 3, 8), 0, 420)
    assert result.duration_minutes == 360
    assert result.meal_estimate_minutes == 0


def test_fall_back_uses_actual_elapsed_time():
    result = calculate_shift_timing(date(2026, 11, 1), 0, 360)
    assert result.duration_minutes == 420
    assert result.estimated_work_minutes == 390


def test_overnight_fall_back_deducts_once():
    result = calculate_shift_timing(
        date(2026, 10, 31), 1320, 360, ends_next_day=True
    )
    assert result.duration_minutes == 540
    assert result.meal_estimate_minutes == 30
    assert result.estimated_work_minutes == 510


@pytest.mark.parametrize("day,start,end", [
    (date(2026, 3, 8), 150, 240),
    (date(2026, 3, 8), 60, 150),
    (date(2026, 11, 1), 90, 240),
    (date(2026, 11, 1), 0, 90),
])
def test_ambiguous_or_nonexistent_endpoints_are_rejected(day, start, end):
    with pytest.raises(ValueError, match="nonexistent or ambiguous"):
        calculate_shift_timing(day, start, end)


@pytest.mark.parametrize("start,end,options", [
    (600, 600, {}),
    (1200, 120, {}),
    (-1, 60, {}),
    (1440, 1440, {}),
    (0, 1441, {}),
    (True, 60, {}),
    (0, False, {}),
    (0, 60, {"ends_next_day": 1}),
    (0, 1440, {"ends_next_day": True}),
    (0, 60, {"timezone_name": "Not/AZone"}),
])
def test_invalid_shift_inputs_are_rejected(start, end, options):
    with pytest.raises(ValueError):
        calculate_shift_timing(MONDAY, start, end, **options)


def test_separate_short_shifts_do_not_receive_a_per_shift_deduction():
    first = calculate_shift_timing(MONDAY, 480, 690)
    second = calculate_shift_timing(MONDAY, 900, 1110)
    assert first.estimated_work_minutes + second.estimated_work_minutes == 420
    assert first.meal_estimate_minutes + second.meal_estimate_minutes == 0


def test_location_timezone_controls_the_saved_instants():
    result = calculate_shift_timing(
        MONDAY, 540, 1050, timezone_name="UTC"
    )
    assert result.start_utc == datetime(2027, 1, 4, 9, 0, tzinfo=timezone.utc)
    assert result.estimated_work_minutes == 480
