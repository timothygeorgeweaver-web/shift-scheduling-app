"""Pure eligibility rules over fresh, authorized inputs; no assignment writes."""

from dataclasses import dataclass
from datetime import date, timezone
from fractions import Fraction

from app.assignment_timing import Interval, overlaps, rest_required, violates_rest
from app.business_day_labor import allocate_shift_labor

UTC = timezone.utc


@dataclass(frozen=True)
class Eligibility:
    hard_blocks: tuple[str, ...]
    warnings: tuple[str, ...]
    daily_work: tuple[tuple[date, Fraction], ...]

    @property
    def category(self):
        if self.hard_blocks:
            return "blocked"
        return "override_required" if self.warnings else "eligible"


def evaluate_assignment(candidate, assigned=(), unavailable=(), *,
                        employee_active, role_active, qualified,
                        timezone_name, rollover_minute=300,
                        rest_window_start=1200, rest_window_end=360,
                        rest_minutes=720):
    """Evaluate one proposed interval under one explicit business-day policy.

    assigned contains this employee's other active assignments across roles
    and week boundaries, excluding the assignment being replaced/edited.
    unavailable contains effective blocked intervals over the full candidate.
    Persistence must supply fresh scoped inputs and revalidate at commit.
    """
    if not isinstance(candidate, Interval):
        raise ValueError("Supply a validated candidate interval.")
    assigned, unavailable = tuple(assigned), tuple(unavailable)
    if any(not isinstance(value, Interval) for value in assigned+unavailable):
        raise ValueError("Supply validated assignment and unavailability intervals.")
    if any(type(value) is not bool for value in (employee_active, role_active, qualified)):
        raise ValueError("Active and qualification flags must be boolean.")

    rest = dict(timezone_name=timezone_name, window_start=rest_window_start,
                window_end=rest_window_end, rest_minutes=rest_minutes)
    rest_required(candidate, **rest)
    hard, warnings = [], []
    if not employee_active:
        hard.append("inactive_employee")
    if not role_active:
        hard.append("inactive_role")
    if not qualified:
        hard.append("not_qualified")
    if any(overlaps(candidate, other) for other in assigned):
        hard.append("overlap")
    else:
        previous = [other for other in assigned
                    if other.end.astimezone(UTC) <= candidate.start.astimezone(UTC)]
        following = [other for other in assigned
                     if other.start.astimezone(UTC) >= candidate.end.astimezone(UTC)]
        if previous and violates_rest(
            max(previous, key=lambda value: value.end.astimezone(UTC)), candidate, **rest,
        ):
            hard.append("rest_before")
        if following and violates_rest(
            candidate, min(following, key=lambda value: value.start.astimezone(UTC)), **rest,
        ):
            hard.append("rest_after")

    if any(overlaps(candidate, block) for block in unavailable):
        warnings.append("unavailability")
    policy = dict(timezone_name=timezone_name, rollover_minute=rollover_minute)
    daily = {row.business_date: row.estimated_work_minutes for row in
             allocate_shift_labor(candidate.start, candidate.end, **policy)}
    for other in assigned:
        for row in allocate_shift_labor(other.start, other.end, **policy):
            if row.business_date in daily:
                daily[row.business_date] += row.estimated_work_minutes
    if any(minutes > 480 for minutes in daily.values()):
        warnings.append("daily_hours")
    return Eligibility(tuple(hard), tuple(warnings), tuple(sorted(daily.items())))
