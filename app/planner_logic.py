"""Exact local-day planner cells and pure additive bulk-edit previews.

Authorization, revisions, cutoff checks and transactions belong to the
persistence service. This module never reads or writes a database.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

MINUTES_PER_CELL = 15
CELLS_PER_DAY = 96


def normalize_cells(values):
    """Return sorted unique blocked cells; never coerce strings or booleans."""
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError("Supply a collection of planner cell numbers.")
    if any(type(value) is not int or not 0 <= value < CELLS_PER_DAY
           for value in values):
        raise ValueError("Planner cells must be integers from 0 through 95.")
    return tuple(sorted(set(values)))


def cells_to_intervals(values):
    """Convert blocked cells to merged half-open local-minute intervals."""
    cells = normalize_cells(values)
    result = []
    for cell in cells:
        start = cell * MINUTES_PER_CELL
        end = start + MINUTES_PER_CELL
        if result and result[-1][1] == start:
            result[-1] = (result[-1][0], end)
        else:
            result.append((start, end))
    return tuple(result)


def intervals_to_cells(intervals):
    """Reject misaligned intervals rather than silently rounding saved data."""
    if not isinstance(intervals, (list, tuple)):
        raise ValueError("Supply local-day intervals.")
    cells = set()
    for interval in intervals:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError("Each interval requires a start and end.")
        start, end = interval
        if (
            type(start) is not int or type(end) is not int
            or not 0 <= start < end <= 1440
            or start % MINUTES_PER_CELL or end % MINUTES_PER_CELL
        ):
            raise ValueError("Intervals must use exact 15-minute boundaries.")
        cells.update(range(start // MINUTES_PER_CELL, end // MINUTES_PER_CELL))
    return normalize_cells(cells)


def toggle_cell(values, cell):
    current = set(normalize_cells(values))
    normalize_cells([cell])
    if cell in current:
        current.remove(cell)
    else:
        current.add(cell)
    return normalize_cells(current)


def normalize_template(template):
    """Use JSON-compatible weekday keys: 0 Monday through 6 Sunday."""
    if not isinstance(template, Mapping):
        raise ValueError("Supply a weekly template.")
    allowed = {str(day) for day in range(7)}
    if any(type(key) is not str or key not in allowed for key in template):
        raise ValueError("Weekday keys must be strings from 0 through 6.")
    return {
        str(day): normalize_cells(template.get(str(day), ()))
        for day in range(7)
    }


@dataclass(frozen=True)
class DayAddition:
    day: date
    before: tuple[int, ...]
    after: tuple[int, ...]


@dataclass(frozen=True)
class BulkPreview:
    changes: tuple[DayAddition, ...]
    skipped_locked_dates: tuple[date, ...]


def preview_additions(first, last, template, existing, locked_dates=()):
    """Preview an inclusive additive mass edit without mutating input data.

    Only actual changes appear in changes. Locked dates with selected template
    cells appear in skipped_locked_dates. Blank template weekdays do nothing.
    The caller must recheck revisions and locks atomically at commit time.
    """
    if type(first) is not date or type(last) is not date or first > last:
        raise ValueError("Supply an ordered inclusive calendar-date range.")
    if not isinstance(existing, Mapping):
        raise ValueError("Supply existing date-specific blocked cells.")
    if not isinstance(locked_dates, (list, tuple, set, frozenset)):
        raise ValueError("Supply locked calendar dates.")
    if any(type(day) is not date for day in locked_dates):
        raise ValueError("Locks require calendar dates.")

    weekly = normalize_template(template)
    locked = set(locked_dates)
    changes = []
    skipped = []
    day = first
    while True:
        addition = weekly[str(day.weekday())]
        if addition:
            if day in locked:
                skipped.append(day)
            else:
                before = normalize_cells(existing.get(day, ()))
                after = normalize_cells(set(before).union(addition))
                if after != before:
                    changes.append(DayAddition(day, before, after))
        if day == last:
            break
        day += timedelta(days=1)

    return BulkPreview(tuple(changes), tuple(skipped))
