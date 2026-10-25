"""Pure assignment timing checks; authorization and persistence belong elsewhere."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self):
        for value in (self.start, self.end):
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError("Shift endpoints must be timezone-aware datetimes.")
        if self.end.astimezone(UTC) <= self.start.astimezone(UTC):
            raise ValueError("Shift end must follow shift start.")


def overlaps(left, right):
    """Adjacent endpoints are allowed; any positive intersection is overlap."""
    return (
        left.start.astimezone(UTC) < right.end.astimezone(UTC)
        and right.start.astimezone(UTC) < left.end.astimezone(UTC)
    )


def rest_required(previous, *, timezone_name,
                  window_start=1200, window_end=360, rest_minutes=720):
    """Apply an inclusive local-clock window to the previous shift's end."""
    if any(type(value) is not int for value in
           (window_start, window_end, rest_minutes)):
        raise ValueError("Rest settings must be integer minutes.")
    if not (0 <= window_start < 1440 and 0 <= window_end < 1440):
        raise ValueError("Rest window endpoints must be clock minutes.")
    if rest_minutes < 0:
        raise ValueError("Required rest cannot be negative.")

    local = previous.end.astimezone(ZoneInfo(timezone_name))
    clock = (
        local.hour * 60 + local.minute
        + local.second / 60 + local.microsecond / 60_000_000
    )
    if window_start <= window_end:
        applies = window_start <= clock <= window_end
    else:
        applies = clock >= window_start or clock <= window_end
    return rest_minutes if applies else 0


def violates_rest(previous, following, **settings):
    """Inputs must be chronological; elapsed rest uses UTC across DST."""
    if following.start.astimezone(UTC) < previous.end.astimezone(UTC):
        raise ValueError("Check overlapping shifts separately.")
    gap = following.start.astimezone(UTC) - previous.end.astimezone(UTC)
    return gap < timedelta(minutes=rest_required(previous, **settings))
