"""Internal candidate evaluation; no HTTP endpoint or assignment mutations."""

from datetime import date, datetime

from app.assignment_availability import unavailable_during
from app.assignment_eligibility import evaluate_assignment
from app.assignment_timing import Interval
from app.planner_logic import cells_to_intervals


def _interval(row):
    return Interval(
        datetime.fromisoformat(row["start_at"]),
        datetime.fromisoformat(row["end_at"]),
    )


def evaluate_inputs(data, *, rest_policy):
    """Evaluate fresh inputs while preserving each week's saved labor policy.

    Actual instants determine overlap and elapsed rest. Daily labor for each
    neighboring shift uses that shift's saved timezone and rollover; only
    matching business-date labels contribute to the proposed shift's totals.
    """
    week = data["week"]
    candidate = _interval(data["slot"])
    assigned = [_interval(row) for row in data["assigned"]]
    assigned_policies = [{
        "timezone_name": row["timezone"],
        "rollover_minute": row["business_day_start_minute"],
    } for row in data["assigned"]]

    effective = {}
    for row in data["calendar_days"]:
        day = date.fromisoformat(row["date"])
        if day in effective:
            raise ValueError("Duplicate effective availability date.")
        frozen = row["frozen_blocked"]
        effective[day] = (
            frozen if frozen is not None
            else cells_to_intervals(row["blocked_cells"])
        )
    unavailable = unavailable_during(
        candidate, effective, timezone_name=week["timezone"],
    )

    if set(rest_policy) != {
        "window_start", "window_end", "rest_minutes",
    }:
        raise ValueError("Supply the complete rest policy.")
    result = evaluate_assignment(
        candidate, assigned, unavailable,
        employee_active=data["employee"]["active"],
        role_active=data["role_active"],
        qualified=data["qualified"],
        timezone_name=week["timezone"],
        rollover_minute=week["business_day_start_minute"],
        rest_window_start=rest_policy["window_start"],
        rest_window_end=rest_policy["window_end"],
        rest_minutes=rest_policy["rest_minutes"],
        assigned_policies=assigned_policies,
    )

    # Never include raw personal availability or neighboring shift intervals.
    return {
        "employee_id": data["employee"]["id"],
        "display_name": data["employee"]["display_name"],
        "employee_revision": data["employee"]["revision"],
        "slot_id": data["slot"]["id"],
        "slot_revision": data["slot"]["revision"],
        "week_revision": week["revision"],
        "category": result.category,
        "hard_blocks": list(result.hard_blocks),
        "warnings": list(result.warnings),
        "daily_work": [
            {
                "business_date": day.isoformat(),
                "estimated_minutes_numerator": minutes.numerator,
                "estimated_minutes_denominator": minutes.denominator,
            }
            for day, minutes in result.daily_work
        ],
    }


def read_candidate(conn, slot_id, employee_id, *, rest_policy):
    """Use the caller's authorized transaction and freshly read database state.

    Candidate lists require a consistent transaction snapshot. Assignment
    commits must lock and re-read; this result never authorizes a later save.
    """
    data = conn.execute(
        "SELECT scheduling.read_assignment_inputs(%s,%s) AS result",
        (slot_id, employee_id),
    ).fetchone()["result"]
    return evaluate_inputs(data, rest_policy=rest_policy)


def read_saved_candidate(conn, slot_id, employee_id):
    """Evaluate fresh inputs using the persisted location rest policy."""
    data = conn.execute(
        "SELECT scheduling.read_assignment_inputs(%s,%s) AS result",
        (slot_id, employee_id),
    ).fetchone()["result"]
    saved = data["rest_policy"]
    result = evaluate_inputs(data, rest_policy={
        name: saved[name]
        for name in ("window_start", "window_end", "rest_minutes")
    })
    result["location_revision"] = saved["location_revision"]
    return result
