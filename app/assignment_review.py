"""Reassess existing assignments without changing or removing them."""

from dataclasses import dataclass

from app.assignment_candidates import evaluate_inputs


@dataclass(frozen=True)
class AssignmentReview:
    assignment_id: str
    assignment_revision: int
    employee_id: str
    slot_id: str
    week_revision: int
    location_revision: int
    hard_blocks: tuple[str, ...]
    warnings: tuple[str, ...]
    review_reasons: tuple[str, ...]

    @property
    def needs_review(self):
        return bool(self.hard_blocks or self.warnings or self.review_reasons)


def review_assignment(inputs, *, proposed_rest_policy=None):
    """Review one current assignment after a dependency change.

    Callers provide fresh authorized inputs for the assigned employee.
    Warnings identify approvals requiring review under changed inputs;
    an old approval is not silently reused for a changed policy/situation.
    No assignment, approval or audit records are modified here.
    """
    current = inputs["current_assignment"]
    if current is None:
        raise ValueError("Supply an existing assignment to review.")
    if str(current["employee_id"]) != str(inputs["employee"]["id"]):
        raise ValueError("Review inputs must belong to the assigned employee.")

    policy = (
        proposed_rest_policy if proposed_rest_policy is not None else
        {key: inputs["rest_policy"][key]
         for key in ("window_start", "window_end", "rest_minutes")}
    )
    if set(policy) != {"window_start", "window_end", "rest_minutes"}:
        raise ValueError("Supply the complete rest policy.")

    week = inputs["week"]
    mismatch = any(
        row["timezone"] != week["timezone"]
        or row["business_day_start_minute"]
        != week["business_day_start_minute"]
        for row in inputs["assigned"]
    )

    if mismatch:
        # Do not silently reinterpret historical business-day labor.
        hard, warnings = (), ()
        reasons = ("saved_business_day_policy_mismatch",)
    else:
        result = evaluate_inputs(inputs, rest_policy=policy)
        hard = tuple(result["hard_blocks"])
        warnings = tuple(result["warnings"])
        reasons = ()

    return AssignmentReview(
        assignment_id=str(current["id"]),
        assignment_revision=current["revision"],
        employee_id=str(inputs["employee"]["id"]),
        slot_id=str(inputs["slot"]["id"]),
        week_revision=week["revision"],
        location_revision=inputs["rest_policy"]["location_revision"],
        hard_blocks=hard,
        warnings=warnings,
        review_reasons=reasons,
    )


def review_assignments(snapshots, *, proposed_rest_policy=None):
    """Return stable per-assignment results; reject duplicate identities."""
    results = []
    visited = set()
    for inputs in snapshots:
        result = review_assignment(
            inputs, proposed_rest_policy=proposed_rest_policy,
        )
        if result.assignment_id in visited:
            raise ValueError("Duplicate assignment in review inputs.")
        visited.add(result.assignment_id)
        results.append(result)
    return tuple(sorted(results, key=lambda row: row.assignment_id))
