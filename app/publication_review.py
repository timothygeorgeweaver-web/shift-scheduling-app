"""Publication safety over fresh authorized inputs; no database writes."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.assignment_candidates import evaluate_inputs
from app.published_roster import build_roster_snapshot


class PublishWeek(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_week_revision: StrictInt = Field(ge=1, le=2147483646)
    expected_location_revision: StrictInt = Field(ge=1, le=2147483646)
    expected_publication_number: StrictInt = Field(ge=0, le=2147483646)
    confirm_publish: StrictBool = False
    confirm_unfilled: StrictBool = False


class PublicationRejected(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def approval_is_current(approval, inputs, decision, code):
    """Check private evidence supplied by the protected database reader.

    changed_since_approval must be explicitly false, based on relevant
    dependency events after approval. A caller must never obtain this flag
    or eligibility decisions from a client request.
    """
    current = inputs["current_assignment"]
    if (
        current is None
        or approval.get("assignment_id") != current["id"]
        or approval.get("assignment_revision") != current["revision"]
        or approval.get("employee_id") != inputs["employee"]["id"]
        or approval.get("rule_code") != code
        or approval.get("changed_since_approval") is not False
    ):
        return False
    reason = approval.get("reason")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        return False

    saved = approval.get("policy", {})
    rest = saved.get("rest", {})
    actual = inputs["rest_policy"]
    # Unrelated location-revision changes do not change the rest rule.
    if any(rest.get(key) != actual[key] for key in (
        "window_start", "window_end", "rest_minutes",
    )):
        return False
    if (
        saved.get("timezone") != inputs["week"]["timezone"]
        or saved.get("business_day_start_minute")
        != inputs["week"]["business_day_start_minute"]
    ):
        return False
    if code == "daily_hours":
        return (
            approval.get("rule_details", {}).get("daily_work")
            == decision["daily_work"]
        )
    return code == "unavailability"


def review_publication(data):
    """Recheck all assignments from one authorized, consistent snapshot.

    data contains the working week/slots/assignment summaries, fresh
    assignment_inputs and private approvals. Storage and HTTP integration
    must supply all these under the publication transaction's locks.
    """
    roster = build_roster_snapshot(
        data, role_orders=data.get("role_orders"),
    )
    assignments = {row["slot_id"]: row for row in data["assignments"]}
    slots = {row["id"]: row for row in data["slots"]}
    visited = set()
    conflicts, warnings = [], []

    for inputs in data["assignment_inputs"]:
        slot_id = inputs["slot"]["id"]
        if slot_id in visited or slot_id not in assignments:
            raise ValueError("Duplicate or unrelated publication assignment inputs.")
        visited.add(slot_id)
        current = inputs["current_assignment"]
        summary = assignments[slot_id]
        slot = slots[slot_id]
        if (
            current is None
            or current["id"] != summary["id"]
            or current["revision"] != summary["revision"]
            or current["employee_id"] != summary["employee_id"]
            or inputs["employee"]["id"] != summary["employee_id"]
            or inputs["week"]["id"] != data["week"]["id"]
            or inputs["week"]["revision"] != data["week"]["revision"]
            or inputs["week"]["timezone"] != data["week"]["timezone"]
            or inputs["week"]["business_day_start_minute"]
            != data["week"]["business_day_start_minute"]
            or any(inputs["slot"][key] != slot[key] for key in (
                "revision", "role_id", "start_at", "end_at",
            ))
        ):
            raise ValueError("Publication assignment inputs changed.")

        policy = inputs["rest_policy"]
        decision = evaluate_inputs(inputs, rest_policy={
            key: policy[key]
            for key in ("window_start", "window_end", "rest_minutes")
        })
        if decision["hard_blocks"]:
            conflicts.append({
                "slot_id": slot_id,
                "employee_id": summary["employee_id"],
                "rule_codes": decision["hard_blocks"],
            })
        for code in decision["warnings"]:
            approved = any(
                approval_is_current(approval, inputs, decision, code)
                for approval in data["approvals"]
            )
            warnings.append({
                "slot_id": slot_id,
                "employee_id": summary["employee_id"],
                "rule_code": code,
                "approved": approved,
            })

    if visited != set(assignments):
        raise ValueError("Fresh inputs are required for every assigned shift.")

    return {
        "roster": roster,
        "unfilled_slot_ids": sorted(set(slots)-set(assignments)),
        "hard_conflicts": conflicts,
        "warnings": warnings,
        "can_publish": not conflicts and all(
            row["approved"] for row in warnings
        ),
    }


def validate_publication_confirmation(
    request, review, *, week_revision, location_revision, publication_number,
):
    if not isinstance(request, PublishWeek):
        raise ValueError("Supply a validated publication request.")
    if (
        request.expected_week_revision != week_revision
        or request.expected_location_revision != location_revision
        or request.expected_publication_number != publication_number
    ):
        raise PublicationRejected("stale", "The schedule or publication changed.")
    if review["hard_conflicts"]:
        raise PublicationRejected("hard_block", "Resolve hard assignment conflicts.")
    if not review["can_publish"]:
        raise PublicationRejected(
            "approval_required", "Renew the required warning approvals.",
        )
    if not request.confirm_publish:
        raise PublicationRejected("confirmation_required", "Confirm publication.")
    if review["unfilled_slot_ids"] and not request.confirm_unfilled:
        raise PublicationRejected(
            "unfilled_confirmation_required",
            "Confirm publication with Unfilled shifts.",
        )
