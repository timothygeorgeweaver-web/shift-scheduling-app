"""Shared assignment reassessment inside the caller's transaction."""

from dataclasses import asdict
from psycopg.types.json import Jsonb
from app.assignment_review import review_assignment


def review_week_locked(conn, location_id, week_id,
                       expected_week_revision, expected_location_revision):
    scope = conn.execute(
        "SELECT scheduling.lock_template_destination(%s,%s,%s,%s) AS result",
        (
            location_id, week_id, expected_week_revision,
            expected_location_revision,
        ),
    ).fetchone()["result"]
    assignments = conn.execute(
        "SELECT scheduling.read_schedule_assignments(%s) AS result",
        (week_id,),
    ).fetchone()["result"]
    slots = {row["id"]: row for row in scope["slots"]}
    reviewed = 0

    for assignment in assignments:
        if assignment["review_status"] == "clear":
            continue
        slot = slots[assignment["slot_id"]]
        locked = conn.execute(
            """
            WITH locked AS MATERIALIZED (
                SELECT scheduling.lock_assignment_scope(
                    %s,%s,%s,%s,%s
                ) AS inputs
            )
            SELECT inputs,md5(inputs::text) AS fingerprint FROM locked
            """,
            (
                assignment["slot_id"], assignment["employee_id"],
                expected_week_revision, slot["revision"],
                expected_location_revision,
            ),
        ).fetchone()
        report = review_assignment(locked["inputs"])
        conn.execute(
            "SELECT scheduling.store_assignment_review(%s,%s,%s)",
            (
                assignment["id"], locked["fingerprint"],
                Jsonb(asdict(report)),
            ),
        )
        reviewed += 1

    current = conn.execute(
        "SELECT scheduling.read_schedule_assignments(%s) AS result",
        (week_id,),
    ).fetchone()["result"]
    return {
        "reviewed_count": reviewed,
        "needs_review_count": sum(
            row["review_status"] != "clear" for row in current
        ),
        "assignments": current,
    }


def project_review_indicators(conn, assignments):
    """Read-only checks for flagged assignments in the displayed week.

    Clear assignments require no additional input read or evaluation.
    This projection writes no review history or audit events.
    """
    projected = []
    for assignment in assignments:
        row = dict(assignment)
        if row["review_status"] != "clear":
            inputs = conn.execute(
                "SELECT scheduling.read_assignment_inputs(%s,%s) AS result",
                (row["slot_id"], row["employee_id"]),
            ).fetchone()["result"]
            report = review_assignment(inputs)
            row.update(
                review_status="needs_review" if report.needs_review else "clear",
                review_hard_blocks=list(report.hard_blocks),
                review_warnings=list(report.warnings),
                review_reasons=list(report.review_reasons),
            )
        projected.append(row)
    return projected
