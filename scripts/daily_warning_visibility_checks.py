"""Read-only candidate HTTP certification in a disposable database."""

from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql

from app.auth_routes import COOKIE_NAME
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin


def literal(value):
    return sql.Literal(value).as_string()


def verify(database, token, location, role):
    week, slot = str(uuid4()), str(uuid4())
    admin(f"""
        BEGIN;
        SET LOCAL ROLE shift_scheduling_owner;
        INSERT INTO scheduling.schedule_week(
            id,organization_id,location_id,week_start_date,
            week_start_day,timezone,created_by,business_day_start_minute
        )
        SELECT {literal(week)},l.organization_id,l.id,date '2096-01-02',
               0,l.timezone,e.user_id,l.business_day_start_minute
        FROM scheduling.location l
        JOIN scheduling.employee e ON e.location_id=l.id
        WHERE l.id={literal(location)} AND e.user_id IS NOT NULL;

        INSERT INTO scheduling.shift_slot(
            id,organization_id,location_id,schedule_week_id,
            role_id,start_at,end_at,created_by
        )
        SELECT {literal(slot)},w.organization_id,w.location_id,w.id,
               {literal(role)},timestamptz '2096-01-02 18:00:00+00',
               timestamptz '2096-01-03 02:00:00+00',w.created_by
        FROM scheduling.schedule_week w WHERE w.id={literal(week)};
        COMMIT;
    """, database)

    from app.auth_routes import _csrf

    employee = admin(
        "SELECT id FROM scheduling.employee WHERE location_id=" +
        literal(location), database,
    )
    second_slot = str(uuid4())
    admin(f"""
        INSERT INTO scheduling.employee_qualification(
            organization_id,location_id,employee_id,job_role_id,active
        )
        SELECT organization_id,location_id,id,{literal(role)},true
        FROM scheduling.employee WHERE id={literal(employee)};

        INSERT INTO scheduling.shift_slot(
            id,organization_id,location_id,schedule_week_id,role_id,
            start_at,end_at,created_by
        )
        SELECT {literal(second_slot)},organization_id,location_id,
               schedule_week_id,role_id,
               start_at-interval '2 hours',start_at,created_by
        FROM scheduling.shift_slot WHERE id={literal(slot)};
    """, database)
    detail_url = (
        f"/api/v1/staff/locations/{location}/schedule-weeks/{week}"
    )
    headers = {"X-CSRF-Token": _csrf(token)}

    def history_counts():
        return admin("""
            SELECT json_build_array(
                (SELECT count(*) FROM scheduling.audit_event),
                (SELECT count(*) FROM scheduling.assignment_review_event),
                (SELECT count(*) FROM scheduling.assignment_override)
            );
        """, database)

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        first = client.put(
            detail_url+f"/shifts/{slot}/assignment",
            headers=headers, json={
                "employee_id": employee,
                "expected_week_revision": 1,
                "expected_slot_revision": 1,
                "expected_location_revision": 1,
            },
        )
        require(first.status_code == 200, "Initial assignment failed.")
        second = client.put(
            detail_url+f"/shifts/{second_slot}/assignment",
            headers=headers, json={
                "employee_id": employee,
                "expected_week_revision": first.json()["week_revision"],
                "expected_slot_revision": 1,
                "expected_location_revision": 1,
                "approvals": [{
                    "rule_code": "daily_hours",
                    "reason": "Synthetic additional coverage",
                }],
            },
        )
        require(second.status_code == 200,
                "Approved second assignment failed.")

        before = history_counts()
        response = client.get(detail_url)
        require(response.status_code == 200, "Week detail read failed.")
        rows = response.json()["assignments"]
        require(len(rows) == 2 and all(
            row["review_status"] == "needs_review"
            and "daily_hours" in row["review_warnings"]
            for row in rows
        ), "Both daily-hours shifts must display the violation.")
        require(history_counts() == before,
                "Automatic warning display wrote history.")

        # Removing the short assignment resolves the remaining shift's total.
        current = second.json()
        response = client.post(
            detail_url+f"/shifts/{second_slot}/assignment/remove",
            headers=headers, json={
                "expected_assignment_id": current["assignment_id"],
                "expected_assignment_revision": current["assignment_revision"],
                "expected_week_revision": current["week_revision"],
                "expected_slot_revision": current["slot_revision"],
                "expected_location_revision": 1,
            },
        )
        require(response.status_code == 200, "Unassignment failed.")
        before = history_counts()
        rows = client.get(detail_url).json()["assignments"]
        require(len(rows) == 1 and rows[0]["review_status"] == "clear"
                and rows[0]["review_warnings"] == [],
                "Resolved daily-hours violation did not clear.")
        require(history_counts() == before,
                "Resolved-warning display wrote history.")

    print("BOTH_SHIFTS_SHOW_DAILY_HOURS_VIOLATION=PASS")
    print("RESOLVED_DAILY_HOURS_WARNING_CLEARS=PASS")
    print("WARNING_DISPLAY_HISTORY_WRITES=NONE")
