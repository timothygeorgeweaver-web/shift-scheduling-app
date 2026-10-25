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
        literal(location) + ";", database,
    )
    url = (
        f"/api/v1/staff/locations/{location}/schedule-weeks/{week}"
        f"/shifts/{slot}/assignment"
    )
    headers = {"X-CSRF-Token": _csrf(token)}

    def counts():
        return admin("""
            SELECT json_build_array(
                (SELECT count(*) FROM scheduling.shift_assignment),
                (SELECT count(*) FROM scheduling.assignment_override),
                (SELECT count(*) FROM scheduling.audit_event),
                (SELECT json_agg(json_build_array(id,revision,active)
                    ORDER BY id) FROM scheduling.shift_assignment),
                (SELECT json_agg(json_build_array(id,revision)
                    ORDER BY id) FROM scheduling.shift_slot),
                (SELECT json_agg(json_build_array(id,revision)
                    ORDER BY id) FROM scheduling.schedule_week)
            );
        """, database)

    body = {
        "employee_id": employee,
        "expected_week_revision": 1,
        "expected_slot_revision": 1,
        "expected_location_revision": 1,
    }
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        require(client.put(url, json=body).status_code == 403,
                "Assignment save accepted missing CSRF.")
        require(client.put(url, headers={
            **headers, "Origin": "https://invalid.example",
        }, json=body).status_code == 403,
                "Assignment save accepted foreign origin.")
        require(client.put(url, headers=headers, json={
            **body, "decision": {"category": "eligible"},
        }).status_code == 422, "Client-supplied eligibility decision accepted.")

        before = counts()
        require(client.put(url, headers=headers, json=body).status_code == 422,
                "Unqualified assignment accepted.")
        require(counts() == before, "Rejected hard block changed records.")

        admin(f"""
            INSERT INTO scheduling.employee_qualification(
                organization_id,location_id,employee_id,job_role_id,active
            )
            SELECT organization_id,location_id,id,{literal(role)},true
            FROM scheduling.employee WHERE id={literal(employee)};
        """, database)

        response = client.put(url, headers=headers, json=body)
        require(response.status_code == 200, "Eligible assignment save failed.")
        first = response.json()
        require(first["week_revision"] == 2 and first["slot_revision"] == 2,
                "Assignment save did not advance schedule revisions.")
        require(client.put(url, headers=headers, json=body).status_code == 409,
                "Stale assignment save accepted.")

        admin(f"""
            INSERT INTO scheduling.unavailability_date(
                organization_id,location_id,employee_id,local_date,
                revision,blocked_cells,actor_user_id
            )
            SELECT organization_id,location_id,id,date '2096-01-02',
                   1,'[40]'::jsonb,user_id
            FROM scheduling.employee WHERE id={literal(employee)};
        """, database)
        body.update(
            expected_week_revision=2,
            expected_slot_revision=2,
            expected_assignment_id=first["assignment_id"],
            expected_assignment_revision=first["assignment_revision"],
        )
        before = counts()
        require(client.put(url, headers=headers, json=body).status_code == 422,
                "Unavailability warning saved without a reason.")
        require(counts() == before, "Missing approval changed records.")
        body["approvals"] = [{
            "rule_code": "unavailability",
            "reason": "Employee approved this test shift",
        }]
        response = client.put(url, headers=headers, json=body)
        require(response.status_code == 200, "Reasoned warning approval failed.")
        current = response.json()
        require(current["approved_rules"] == ["unavailability"],
                "Approved rule was not recorded.")
        require(admin(f"""
            SELECT count(*)=1 FROM scheduling.assignment_override
            WHERE assignment_id={literal(current["assignment_id"])};
        """, database) == "t", "Immutable approval record missing.")

        body.update(
            expected_week_revision=current["week_revision"],
            expected_slot_revision=current["slot_revision"],
            expected_assignment_id=current["assignment_id"],
            expected_assignment_revision=current["assignment_revision"],
        )
        admin("""
            CREATE FUNCTION scheduling.reject_commit_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='assignment.saved' THEN
                    RAISE EXCEPTION 'Synthetic commit audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_commit_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_commit_audit_test();
        """, database)
        before = counts()
        require(client.put(url, headers=headers, json=body).status_code == 503,
                "Commit audit failure was not exercised.")
        require(counts() == before,
                "Failed commit retained assignment, approval or revision changes.")
        admin(
            "DROP TRIGGER reject_commit_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_commit_audit_test();", database,
        )

        # A second overlapping slot must be rejected using fresh assignments.
        other = str(uuid4())
        admin(f"""
            INSERT INTO scheduling.shift_slot(
                id,organization_id,location_id,schedule_week_id,role_id,
                start_at,end_at,created_by
            )
            SELECT {literal(other)},organization_id,location_id,
                   schedule_week_id,role_id,start_at,end_at,created_by
            FROM scheduling.shift_slot WHERE id={literal(slot)};
        """, database)
        overlap = {
            "employee_id": employee,
            "expected_week_revision": current["week_revision"],
            "expected_slot_revision": 1,
            "expected_location_revision": 1,
            "approvals": body["approvals"],
        }
        before = counts()
        require(client.put(url.replace(slot, other, 1),
            headers=headers, json=overlap).status_code == 422,
            "Fresh overlapping assignment was accepted.")
        require(counts() == before, "Overlap rejection changed records.")


        # Add a second valid assignment on the following calendar day.
        later = str(uuid4())
        admin(f"""
            INSERT INTO scheduling.shift_slot(
                id,organization_id,location_id,schedule_week_id,role_id,
                start_at,end_at,created_by
            )
            SELECT {literal(later)},organization_id,location_id,
                   schedule_week_id,role_id,
                   start_at+interval '1 day',end_at+interval '1 day',created_by
            FROM scheduling.shift_slot WHERE id={literal(slot)};
        """, database)
        response = client.put(url.replace(slot, later, 1), headers=headers, json={
            "employee_id": employee,
            "expected_week_revision": current["week_revision"],
            "expected_slot_revision": 1,
            "expected_location_revision": 1,
        })
        require(response.status_code == 200,
                "Second-day assignment fixture failed.")
        second = response.json()
        clear_url = (
            f"/api/v1/staff/locations/{location}/schedule-weeks/{week}"
            "/assignments/clear"
        )
        clear_body = {
            "expected_week_revision": second["week_revision"],
            "expected_location_revision": 1,
        }
        require(client.post(clear_url, json=clear_body).status_code == 403,
                "Clear assignments accepted missing CSRF.")
        before = counts()
        require(client.post(clear_url, headers=headers, json={
            **clear_body, "expected_week_revision": 1,
        }).status_code == 409, "Stale whole-week clearing accepted.")
        require(counts() == before, "Stale clear changed records.")

        # Fail the summary audit after both individual unassignments.
        admin("""
            CREATE FUNCTION scheduling.reject_clear_summary_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='assignment.week_cleared' THEN
                    RAISE EXCEPTION 'Synthetic clear-summary audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_clear_summary_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_clear_summary_test();
        """, database)
        before = counts()
        require(client.post(clear_url, headers=headers, json=clear_body)
                .status_code == 503, "Clear-summary failure not exercised.")
        require(counts() == before,
                "Failed whole-week clear retained partial unassignments.")
        admin(
            "DROP TRIGGER reject_clear_summary_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_clear_summary_test();", database,
        )

        active_slots = admin(f"""
            SELECT count(*) FROM scheduling.shift_slot
            WHERE schedule_week_id={literal(week)} AND active;
        """, database)
        approval_count = admin(
            "SELECT count(*) FROM scheduling.assignment_override;", database,
        )
        response = client.post(clear_url, headers=headers, json=clear_body)
        require(response.status_code == 200
                and response.json()["cleared_assignments"] == 2,
                "Whole-week clearing did not remove both assignments.")
        require(admin(f"""
            SELECT count(*) FROM scheduling.shift_assignment a
            JOIN scheduling.shift_slot s ON s.id=a.shift_slot_id
            WHERE s.schedule_week_id={literal(week)} AND a.active;
        """, database) == "0", "Live assignments remained after clearing.")
        require(admin(f"""
            SELECT count(*) FROM scheduling.shift_slot
            WHERE schedule_week_id={literal(week)} AND active;
        """, database) == active_slots, "Clearing removed schedule shifts.")
        require(admin(
            "SELECT count(*) FROM scheduling.assignment_override;", database,
        ) == approval_count, "Clearing removed immutable approval history.")

        # Clearing an already-unassigned week is a genuine no-op.
        clear_body["expected_week_revision"] = response.json()["week_revision"]
        before = counts()
        response = client.post(clear_url, headers=headers, json=clear_body)
        require(response.status_code == 200
                and response.json()["cleared_assignments"] == 0
                and counts() == before, "Empty clear was not a no-op.")

        admin(f"""
            DELETE FROM scheduling.member_permission
            WHERE organization_id=(
                SELECT organization_id FROM scheduling.location
                WHERE id={literal(location)}
            ) AND permission='administrator';
        """, database)
        require(client.put(url, headers=headers, json=body).status_code == 403,
                "Employee-only account could assign shifts.")

    print("ASSIGNMENT_HTTP_ELIGIBLE_SAVE_AND_STALE_DENIAL=PASS")
    print("SERVER_ONLY_ELIGIBILITY_AND_FRESH_OVERLAP_DENIAL=PASS")
    print("PER_RULE_APPROVAL_PERSISTENCE_AND_REQUIRED_REASON=PASS")
    print("HTTP_ASSIGNMENT_APPROVAL_AUDIT_AND_REVISION_ROLLBACK=PASS")
    print("ASSIGNMENT_CSRF_ORIGIN_AND_STAFF_AUTHORIZATION=PASS")

    print("WHOLE_WEEK_CLEAR_PRESERVES_SHIFTS_AND_APPROVAL_HISTORY=PASS")
    print("FINAL_SUMMARY_FAILURE_ROLLS_BACK_ALL_UNASSIGNMENTS=PASS")
    print("CLEAR_CSRF_STALE_REVISION_AND_EMPTY_NO_OP=PASS")
