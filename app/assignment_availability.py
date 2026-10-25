"""Intersect a shift with effective local-date blocks, including DST folds.

The authorized reader supplies complete effective blocks for each local date
touched by the shift. Frozen intervals take precedence over editable cells.
Missing dates are rejected rather than silently treated as available.
"""

from collections.abc import Mapping
from datetime import date, timedelta, timezone
from zoneinfo import ZoneInfo

from app.assignment_timing import Interval

UTC = timezone.utc
MINUTE = timedelta(minutes=1)


def unavailable_during(candidate, effective_days, *, timezone_name):
    """Return merged actual-time intersections with effective minute ranges.

    Each date maps to local-day (start, end) minute pairs in [0, 1440].
    An empty collection explicitly means fully available on that date.
    Repeated wall-clock minutes match both occurrences; skipped minutes
    consume no actual time. Input intervals are never rounded.
    """
    if not isinstance(candidate, Interval):
        raise ValueError("Supply a validated shift interval.")
    if not isinstance(effective_days, Mapping):
        raise ValueError("Supply effective calendar-date blocks.")

    zone = ZoneInfo(timezone_name)
    start = candidate.start.astimezone(UTC)
    end = candidate.end.astimezone(UTC)
    if any(value.second or value.microsecond for value in (start, end)):
        raise ValueError("Shift endpoints must use exact whole minutes.")
    if end - start > timedelta(hours=26):
        raise ValueError("Shift exceeds the supported actual-time duration.")

    normalized = {}
    for day, ranges in effective_days.items():
        if type(day) is not date or not isinstance(ranges, (list, tuple)):
            raise ValueError("Invalid effective date or interval collection.")
        values = []
        for pair in ranges:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("Supply local-day minute pairs.")
            first, last = pair
            if (type(first) is not int or type(last) is not int
                    or not 0 <= first < last <= 1440):
                raise ValueError("Invalid effective minute range.")
            values.append((first, last))
        normalized[day] = tuple(values)

    result = []
    cursor = start
    blocked_start = None
    while cursor < end:
        local = cursor.astimezone(zone)
        day = local.date()
        if day not in normalized:
            raise ValueError("Effective availability is missing for a shift date.")
        minute = local.hour * 60 + local.minute
        blocked = any(first <= minute < last
                      for first, last in normalized[day])
        if blocked and blocked_start is None:
            blocked_start = cursor
        elif not blocked and blocked_start is not None:
            result.append(Interval(blocked_start, cursor))
            blocked_start = None
        cursor += MINUTE

    if blocked_start is not None:
        result.append(Interval(blocked_start, end))
    return tuple(result)
