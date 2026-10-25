"""Map business-day planner cells onto existing calendar-date storage."""

from collections.abc import Mapping
from datetime import date, timedelta

from app.business_day import planner_cell_start, validate_rollover
from app.planner_logic import normalize_cells, normalize_template

FIRST_DATE = date(2000, 1, 1)
LAST_DATE = date(9998, 12, 31)


def supported_date(value):
    if type(value) is not date or not FIRST_DATE <= value <= LAST_DATE:
        raise ValueError("Choose a supported calendar date.")
    return value


def calendar_address(business_day, cell, rollover_minute=300):
    """Return the actual calendar date and midnight-based storage cell."""
    supported_date(business_day)
    local = planner_cell_start(business_day, cell, rollover_minute)
    supported_date(local.date())
    return local.date(), (local.hour * 60 + local.minute) // 15


def merge_business_edits(first_date, edits, existing, *,
                         rollover_minute=300, locked_dates=()):
    """Apply explicit cell changes while preserving all untouched stored cells.

    Each edit is (business_date, business_cell, unavailable_boolean).
    The persistence layer must also check revisions, authorization and locks
    atomically. This function never writes to a database or mutates its inputs.
    """
    supported_date(first_date)
    validate_rollover(rollover_minute)
    last = first_date + timedelta(days=6)
    supported_date(last)
    if not isinstance(edits, (list, tuple)) or len(edits) > 672:
        raise ValueError("Supply at most seven days of explicit cell edits.")
    if not isinstance(existing, Mapping):
        raise ValueError("Supply existing calendar-date cells.")
    if not isinstance(locked_dates, (list, tuple, set, frozenset)):
        raise ValueError("Supply locked calendar dates.")
    locked = {supported_date(day) for day in locked_dates}
    before, after, visited = {}, {}, set()

    for edit in edits:
        if not isinstance(edit, (list, tuple)) or len(edit) != 3:
            raise ValueError("Each edit requires a business date, cell and state.")
        business_day, cell, unavailable = edit
        supported_date(business_day)
        if not first_date <= business_day <= last:
            raise ValueError("Edit lies outside the displayed business week.")
        if type(unavailable) is not bool:
            raise ValueError("Unavailable must be boolean.")
        day, calendar_cell = calendar_address(
            business_day, cell, rollover_minute,
        )
        address = (day, calendar_cell)
        if address in visited:
            raise ValueError("A cell may only be edited once per request.")
        visited.add(address)
        if day not in before:
            before[day] = normalize_cells(existing.get(day, ()))
            after[day] = set(before[day])
        previous = calendar_cell in after[day]
        if previous == unavailable:
            continue
        if day in locked:
            raise ValueError("A changed calendar date is locked.")
        if unavailable:
            after[day].add(calendar_cell)
        else:
            after[day].discard(calendar_cell)

    return {
        day: normalize_cells(after[day])
        for day in sorted(after)
        if normalize_cells(after[day]) != before[day]
    }


def bulk_calendar_additions(first_date, last_date, weekly_cells, *,
                            rollover_minute=300):
    """Map inclusive business-date additions without clearing stored cells.

    Weekday keys and cell numbers refer to the business-day planner.
    The last business date may add cells on the following calendar date.
    """
    supported_date(first_date)
    supported_date(last_date)
    validate_rollover(rollover_minute)
    if first_date > last_date or (last_date-first_date).days >= 366:
        raise ValueError("Choose an inclusive range of at most 366 business dates.")
    weekly = normalize_template(weekly_cells)
    result = {}
    for offset in range((last_date-first_date).days+1):
        business_day = first_date + timedelta(days=offset)
        for cell in weekly[str(business_day.weekday())]:
            day, calendar_cell = calendar_address(
                business_day, cell, rollover_minute,
            )
            result.setdefault(day, set()).add(calendar_cell)
    return {
        day: normalize_cells(cells)
        for day, cells in sorted(result.items())
    }
