"""Exact elapsed labor allocation; no scheduled meal interval or payroll record."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.business_day import business_date_at, business_day_bounds, validate_rollover

UTC = timezone.utc


@dataclass(frozen=True)
class DayLabor:
    business_date: date
    duration_minutes: int
    meal_estimate_minutes: Fraction
    estimated_work_minutes: Fraction


def allocate_shift_labor(start, end, *, timezone_name, rollover_minute=300):
    """Split a shift at local business-day boundaries using actual elapsed time.

    The one per-shift meal estimate is allocated proportionally, without
    rounding. A shift ending exactly at rollover contributes nothing to the
    next business day. Ambiguous/nonexistent boundaries fail explicitly.
    """
    validate_rollover(rollover_minute)
    if type(timezone_name) is not str:
        raise ValueError("Supply a location timezone.")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Unknown location timezone.") from None
    for value in (start, end):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Shift endpoints must be timezone-aware.")
        instant = value.astimezone(UTC)
        if instant.second or instant.microsecond:
            raise ValueError("Shift endpoints must use exact elapsed minutes.")
    cursor, finish = start.astimezone(UTC), end.astimezone(UTC)
    if finish <= cursor:
        raise ValueError("Shift end must follow shift start.")
    total = (finish-cursor) // timedelta(minutes=1)
    meal = 30 if total > 360 else 0
    day = business_date_at(cursor, zone.key, rollover_minute)
    result = []
    while cursor < finish:
        beginning, boundary = business_day_bounds(day, zone.key, rollover_minute)
        if not beginning <= cursor < boundary:
            raise ValueError("Shift does not map consistently to a business day.")
        segment_end = min(finish, boundary)
        elapsed = segment_end-cursor
        minutes = elapsed // timedelta(minutes=1)
        if minutes <= 0 or elapsed != timedelta(minutes=minutes):
            raise ValueError("Business-day allocation requires whole elapsed minutes.")
        deduction = Fraction(meal * minutes, total)
        result.append(DayLabor(day, minutes, deduction, Fraction(minutes)-deduction))
        cursor = segment_end
        if cursor < finish:
            day += timedelta(days=1)
    return tuple(result)
