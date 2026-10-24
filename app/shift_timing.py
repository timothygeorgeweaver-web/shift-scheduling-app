"""Shift instants and estimated working minutes; no scheduled lunch interval."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


@dataclass(frozen=True)
class ShiftTiming:
    start_utc: datetime
    end_utc: datetime
    duration_minutes: int
    meal_estimate_minutes: int
    estimated_work_minutes: int


def _instant(day, minute, zone):
    naive = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minute)
    candidates = set()
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        instant = local.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(instant)
    if len(candidates) != 1:
        raise ValueError("Shift time is nonexistent or ambiguous in this timezone.")
    return candidates.pop()


def calculate_shift_timing(day, start_minute, end_minute, *,
                           ends_next_day=False,
                           timezone_name="America/Los_Angeles"):
    if type(day) is not date or not date(2000, 1, 1) <= day <= date(9998, 12, 30):
        raise ValueError("Supply a supported local calendar date.")
    if type(start_minute) is not int or not 0 <= start_minute < 1440:
        raise ValueError("Start minute must be an integer from 0 through 1439.")
    if type(end_minute) is not int or not 0 <= end_minute <= 1440:
        raise ValueError("End minute must be an integer from 0 through 1440.")
    if type(ends_next_day) is not bool:
        raise ValueError("Ends next day must be boolean.")
    if ends_next_day and end_minute == 1440:
        raise ValueError("Use a next-day clock time below 24:00.")

    absolute_end = end_minute + (1440 if ends_next_day else 0)
    if absolute_end <= start_minute:
        raise ValueError(
            "End must follow start; explicitly select next day for overnight shifts."
        )
    if type(timezone_name) is not str:
        raise ValueError("Supply a timezone name.")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unknown timezone.") from None

    start = _instant(day, start_minute, zone)
    end = _instant(day, absolute_end, zone)
    seconds = int((end - start).total_seconds())
    if seconds <= 0 or seconds % 60:
        raise ValueError("Shift must have a positive whole-minute elapsed duration.")

    duration = seconds // 60
    deduction = 30 if duration > 360 else 0
    return ShiftTiming(start, end, duration, deduction, duration - deduction)
