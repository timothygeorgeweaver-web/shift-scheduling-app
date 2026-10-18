"""Resolve blocked local-day minutes; no entries means fully available."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

DAY = 1440
MinuteRange = tuple[int, int]


@dataclass(frozen=True)
class BlockWindow:
    start: int
    end: int
    ends_next_day: bool = False

    def __post_init__(self):
        if type(self.start) is not int or not 0 <= self.start < DAY:
            raise ValueError("Invalid start minute.")
        if type(self.end) is not int or not 0 <= self.end <= DAY:
            raise ValueError("Invalid end minute.")
        if type(self.ends_next_day) is not bool:
            raise ValueError("Next-day selection must be boolean.")
        if self.ends_next_day and self.end == DAY:
            raise ValueError("Use a next-day clock time below 24:00.")
        if not 0 < self.absolute_end - self.start <= DAY:
            raise ValueError("Select ends next day for overnight blocks; maximum 24 hours.")

    @property
    def absolute_end(self):
        return self.end + (DAY if self.ends_next_day else 0)


def _merge(intervals: Sequence[MinuteRange]) -> tuple[MinuteRange, ...]:
    result = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return tuple(result)


def blocked_for_date(
    day: date,
    recurring_for_date: Callable[[date], Sequence[BlockWindow]],
    exceptions: Mapping[date, Sequence[BlockWindow]],
) -> tuple[MinuteRange, ...]:
    """The caller selects effective-dated recurring versions by origin date."""
    if type(day) is not date:
        raise ValueError("Supply a local calendar date.")
    if day in exceptions:
        windows = exceptions[day]
        # This date's replacement clears every prior-day carry-in.
        return _merge([(w.start, min(w.absolute_end, DAY)) for w in windows])

    intervals = [
        (w.start, min(w.absolute_end, DAY))
        for w in recurring_for_date(day)
    ]
    if day > date.min:
        previous = day - timedelta(days=1)
        windows = (
            exceptions[previous] if previous in exceptions
            else recurring_for_date(previous)
        )
        intervals.extend(
            (0, w.absolute_end - DAY)
            for w in windows if w.absolute_end > DAY
        )
    return _merge(intervals)


def available_for_date(
    day: date,
    recurring_for_date: Callable[[date], Sequence[BlockWindow]],
    exceptions: Mapping[date, Sequence[BlockWindow]],
) -> tuple[MinuteRange, ...]:
    result = []
    cursor = 0
    for start, end in blocked_for_date(day, recurring_for_date, exceptions):
        if cursor < start:
            result.append((cursor, start))
        cursor = end
    if cursor < DAY:
        result.append((cursor, DAY))
    return tuple(result)


def intersects_blocked(
    start: int, end: int, blocked: Sequence[MinuteRange]
) -> bool:
    """Compare one local-date portion; callers split overnight shifts first."""
    if (
        type(start) is not int or type(end) is not int
        or not 0 <= start < end <= DAY
    ):
        raise ValueError("Supply a nonempty local-day interval.")
    return any(start < blocked_end and blocked_start < end
               for blocked_start, blocked_end in blocked)
