"""Business-day labels and exact planner cells; no database access.

Stored calendar dates and actual instants remain unchanged.
A planner column contains 96 local-clock cells starting at its rollover.
DST may make its elapsed duration different from 24 hours.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
DEFAULT_ROLLOVER_MINUTE = 300


def validate_rollover(value):
    if type(value) is not int or not 0 <= value < 1440 or value % 15:
        raise ValueError("Rollover must be a 15-minute clock time.")
    return value


def _date(value):
    if type(value) is not date:
        raise ValueError("Supply a calendar date.")
    return value


def business_date(local, rollover_minute=DEFAULT_ROLLOVER_MINUTE):
    """Label a local wall-clock datetime; no timezone conversion is performed."""
    validate_rollover(rollover_minute)
    if not isinstance(local, datetime):
        raise ValueError("Supply a local datetime.")
    day = local.date()
    if local.hour * 60 + local.minute < rollover_minute:
        day -= timedelta(days=1)
    return day


def business_date_at(instant, timezone_name,
                     rollover_minute=DEFAULT_ROLLOVER_MINUTE):
    """Label an actual instant using the location's local clock."""
    if not isinstance(instant, datetime) or instant.utcoffset() is None:
        raise ValueError("Supply a timezone-aware instant.")
    return business_date(
        instant.astimezone(ZoneInfo(timezone_name)), rollover_minute,
    )


def planner_cell_start(day, cell,
                       rollover_minute=DEFAULT_ROLLOVER_MINUTE):
    """Map a business-day cell to its exact naive local calendar datetime."""
    _date(day)
    validate_rollover(rollover_minute)
    if type(cell) is not int or not 0 <= cell < 96:
        raise ValueError("Planner cell must be an integer from 0 through 95.")
    return datetime.combine(day, time()) + timedelta(
        minutes=rollover_minute + cell * 15,
    )


def planner_cell_for(local, rollover_minute=DEFAULT_ROLLOVER_MINUTE):
    """Inverse mapping; reject non-quarter-hour or timezone-aware values."""
    validate_rollover(rollover_minute)
    if (
        not isinstance(local, datetime) or local.tzinfo is not None
        or local.second or local.microsecond or local.minute % 15
    ):
        raise ValueError("Supply an exact naive local quarter-hour datetime.")
    day = business_date(local, rollover_minute)
    offset = (
        (local.date() - day).days * 1440
        + local.hour * 60 + local.minute - rollover_minute
    )
    return day, offset // 15


def _boundary(day, timezone_name, rollover_minute):
    local = datetime.combine(day, time()) + timedelta(minutes=rollover_minute)
    zone = ZoneInfo(timezone_name)
    candidates = set()
    for fold in (0, 1):
        instant = local.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == local:
            candidates.add(instant)
    if len(candidates) != 1:
        raise ValueError("Business-day boundary is ambiguous or nonexistent.")
    return candidates.pop()


def business_day_bounds(day, timezone_name,
                        rollover_minute=DEFAULT_ROLLOVER_MINUTE):
    """Return half-open UTC boundaries; never guess a DST transition time."""
    _date(day)
    validate_rollover(rollover_minute)
    start = _boundary(day, timezone_name, rollover_minute)
    end = _boundary(day + timedelta(days=1), timezone_name, rollover_minute)
    if end <= start:
        raise ValueError("Business day must have positive elapsed duration.")
    return start, end
