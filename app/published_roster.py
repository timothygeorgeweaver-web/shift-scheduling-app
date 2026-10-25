"""Build an independent employee-safe roster; no authorization or DB writes."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.business_day import business_date

UTC = timezone.utc


def _instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Roster shift endpoints must include a timezone.")
    return result.astimezone(UTC)


def build_roster_snapshot(data, *, role_orders=None):
    """Project explicitly allowed fields from an authorized working week.

    Publication must separately lock/revalidate the full week, verify current
    warning approvals and atomically store this snapshot with its version,
    availability locks and audit event. Calling this helper never publishes.

    Names and saved business-day settings are copied so later staff/settings
    edits cannot alter the official roster representation.
    """
    week = data["week"]
    zone = ZoneInfo(week["timezone"])
    rollover = week["business_day_start_minute"]
    if type(rollover) is not int or not 0 <= rollover < 1440 or rollover % 15:
        raise ValueError("Invalid saved business-day rollover.")
    first = date.fromisoformat(week["week_start_date"])
    role_orders = {} if role_orders is None else dict(role_orders)

    by_slot = {}
    assignment_ids = set()
    for assignment in data["assignments"]:
        if (assignment["slot_id"] in by_slot
                or assignment["id"] in assignment_ids):
            raise ValueError("Duplicate current assignment.")
        by_slot[assignment["slot_id"]] = assignment
        assignment_ids.add(assignment["id"])

    shifts = []
    slot_ids = set()
    for slot in data["slots"]:
        if slot["id"] in slot_ids:
            raise ValueError("Duplicate shift slot.")
        slot_ids.add(slot["id"])
        start, end = _instant(slot["start_at"]), _instant(slot["end_at"])
        seconds = (end-start).total_seconds()
        if seconds <= 0 or seconds % 60:
            raise ValueError("Invalid roster shift duration.")
        local_start = start.astimezone(zone)
        local_end = end.astimezone(zone)
        day = business_date(local_start.replace(tzinfo=None), rollover)
        if not first <= day <= first+timedelta(days=6):
            raise ValueError("Shift starts outside the saved business week.")
        minutes = int(seconds // 60)
        meal = 30 if minutes > 360 else 0
        assignment = by_slot.get(slot["id"])
        order = role_orders.get(slot["role_id"], 0)
        if type(order) is not int:
            raise ValueError("Job-type display order must be an integer.")

        shifts.append({
            "id": slot["id"],
            "role_id": slot["role_id"],
            "role_name": slot["role_name"],
            "role_display_order": order,
            "business_date": day.isoformat(),
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "local_start": local_start.isoformat(),
            "local_end": local_end.isoformat(),
            "duration_minutes": minutes,
            "meal_estimate_minutes": meal,
            "estimated_work_minutes": minutes-meal,
            "employee": None if assignment is None else {
                "id": assignment["employee_id"],
                "display_name": assignment["display_name"],
            },
        })

    if set(by_slot) - slot_ids:
        raise ValueError("Assignment references a shift outside this roster.")

    shifts.sort(key=lambda row: (
        row["start_at"], row["role_display_order"],
        row["role_name"].casefold(), row["role_id"],
        row["end_at"], row["id"],
    ))
    return {
        "schema_version": 1,
        "week": {
            "id": week["id"],
            "location_id": week["location_id"],
            "week_start_date": first.isoformat(),
            "timezone": week["timezone"],
            "business_day_start_minute": rollover,
        },
        "shifts": shifts,
    }
