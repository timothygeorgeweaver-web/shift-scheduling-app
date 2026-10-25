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
    admin(f"""
        INSERT INTO scheduling.employee_qualification(
            organization_id,location_id,employee_id,job_role_id,active
        )
        SELECT organization_id,location_id,id,{literal(role)},true
        FROM scheduling.employee WHERE id={literal(employee)};
    """, database)

    base = (
        f"/api/v1/staff/locations/{location}/schedule-weeks/{week}"
        f"/shifts/{slot}"
    )
    headers = {"X-CSRF-Token": _csrf(token)}

    def state():
        return admin("""
            SELECT json_build_array(
                (SELECT jsonb_agg(to_jsonb(t) ORDER BY id)
                 FROM scheduling.shift_slot t),
                (SELECT jsonb_agg(to_jsonb(t) ORDER BY id)
                 FROM scheduling.schedule_week t),
                (SELECT jsonb_agg(to_jsonb(t) ORDER BY id)
                 FROM scheduling.shift_assignment t),
                (SELECT jsonb_agg(to_jsonb(t) ORDER BY id)
                 FROM scheduling.assignment_override t),
                (SELECT count(*) FROM scheduling.audit_event),
                (SELECT count(*) FROM scheduling.assignment_review_event)
            );
        """, database)

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        response = client.put(base+"/assignment", headers=headers, json={
            "employee_id": employee,
            "expected_week_revision": 1,
            "expected_slot_revision": 1,
            "expected_location_revision": 1,
        })
        require(response.status_code == 200, "Initial assignment failed.")
        original = response.json()
        body = {
            "role_id": role, "local_date": "2096-01-02",
            "start_minute": 660, "end_minute": 1080,
            "expected_week_revision": original["week_revision"],
            "expected_shift_revision": original["slot_revision"],
            "expected_location_revision": 1,
            "expected_assignment_id": original["assignment_id"],
            "expected_assignment_revision": original["assignment_revision"],
        }
        before = state()
        require(client.patch(base+"/assigned-edit", json=body).status_code == 403,
                "Assigned edit accepted missing CSRF.")
        require(client.patch(base+"/assigned-edit", headers={
            **headers, "Origin": "https://invalid.example",
        }, json=body).status_code == 403, "Foreign origin accepted.")
        require(state() == before, "Rejected request changed records.")

        preview = client.post(
            base+"/assigned-preview", headers=headers, json=body,
        )
        require(preview.status_code == 200
                and preview.json()["category"] == "eligible",
                "Eligible assigned-edit preview failed.")
        require(state() == before, "Preview wrote assignment or audit records.")

        response = client.patch(base+"/assigned-edit", headers=headers, json=body)
        require(response.status_code == 200, "Eligible assigned edit failed.")
        current = response.json()
        require(current["employee_id"] == employee,
                "Assigned edit changed employee.")
        require(current["assignment_id"] != original["assignment_id"],
                "Assignment renewal history was not created.")
        require(admin(
            "SELECT NOT active FROM scheduling.shift_assignment WHERE id=" +
            literal(original["assignment_id"]), database,
        ) == "t", "Prior assignment history was not retained.")
        require(client.patch(base+"/assigned-edit", headers=headers,
                             json=body).status_code == 409,
                "Stale assigned edit was accepted.")

        body.update(
            expected_week_revision=current["week_revision"],
            expected_shift_revision=current["slot_revision"],
            expected_assignment_id=current["assignment_id"],
            expected_assignment_revision=current["assignment_revision"],
        )
        admin(f"""
            INSERT INTO scheduling.unavailability_date(
                organization_id,location_id,employee_id,local_date,
                revision,blocked_cells,actor_user_id
            )
            SELECT organization_id,location_id,id,date '2096-01-02',
                   1,'[44]'::jsonb,user_id
            FROM scheduling.employee WHERE id={literal(employee)};
        """, database)
        before = state()
        preview = client.post(
            base+"/assigned-preview", headers=headers, json=body,
        )
        require(preview.status_code == 200
                and "unavailability" in preview.json()["warnings"],
                "Proposed-edit preview omitted availability warning.")
        require(state() == before, "Warning preview wrote records.")
        require(client.patch(base+"/assigned-edit", headers=headers,
                             json=body).status_code == 422,
                "Availability warning accepted without fresh approval.")
        require(state() == before, "Rejected warning changed records.")
        body["approvals"] = [{
            "rule_code": "unavailability",
            "reason": "Employee agreed to this changed shift",
        }]

        admin("""
            CREATE FUNCTION scheduling.reject_assigned_edit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='assignment.saved' THEN
                    RAISE EXCEPTION 'Synthetic assigned edit audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_assigned_edit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_assigned_edit_test();
        """, database)
        before = state()
        require(client.patch(base+"/assigned-edit", headers=headers,
                             json=body).status_code == 503,
                "Final assignment audit failure was not exercised.")
        require(state() == before,
                "Failed edit retained shift, assignment, approval or review changes.")
        admin("""
            DROP TRIGGER reject_assigned_edit_test ON scheduling.audit_event;
            DROP FUNCTION scheduling.reject_assigned_edit_test();
        """, database)
        response = client.patch(base+"/assigned-edit", headers=headers, json=body)
        require(response.status_code == 200, "Approved assigned edit failed.")
        require(response.json()["approved_rules"] == ["unavailability"],
                "Fresh warning approval was not persisted.")


        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        current = response.json()
        body.update(
            expected_week_revision=current["week_revision"],
            expected_shift_revision=current["slot_revision"],
            expected_assignment_id=current["assignment_id"],
            expected_assignment_revision=current["assignment_revision"],
        )
        barrier = Barrier(2)

        def competing_edit():
            with TestClient(app) as other_client:
                other_client.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                result = other_client.patch(
                    base+"/assigned-edit", headers=headers, json=body,
                )
                return result.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(competing_edit) for _ in range(2)]
            statuses = sorted(future.result(timeout=30) for future in futures)
        require(statuses == [200, 409],
                "Competing assigned edits did not produce one save and one conflict.")

        detail_url = (
            f"/api/v1/staff/locations/{location}/schedule-weeks/{week}"
        )
        detail = client.get(detail_url)
        require(detail.status_code == 200, "Concurrent result read failed.")
        require(len(detail.json()["assignments"]) == 1
                and detail.json()["assignments"][0]["employee_id"] == employee,
                "Competing edits did not retain exactly one assigned employee.")
        require(admin(
            "SELECT count(*)=1 FROM scheduling.shift_assignment "
            "WHERE shift_slot_id=" + literal(slot) + " AND active",
            database,
        ) == "t", "Competing edits left multiple active assignments.")

        # A different saved rollover in another week must not block assignment.
        far_week, far_slot = str(uuid4()), str(uuid4())
        admin(f"""
            INSERT INTO scheduling.schedule_week(
                id,organization_id,location_id,week_start_date,
                week_start_day,timezone,created_by,business_day_start_minute
            )
            SELECT {literal(far_week)},organization_id,location_id,
                   week_start_date+21,week_start_day,timezone,created_by,0
            FROM scheduling.schedule_week WHERE id={literal(week)};
            INSERT INTO scheduling.shift_slot(
                id,organization_id,location_id,schedule_week_id,role_id,
                start_at,end_at,created_by
            )
            SELECT {literal(far_slot)},organization_id,location_id,
                   {literal(far_week)},role_id,
                   start_at+interval '21 days',end_at+interval '21 days',created_by
            FROM scheduling.shift_slot WHERE id={literal(slot)};
        """, database)
        far_url = (
            f"/api/v1/staff/locations/{location}/schedule-weeks/{far_week}"
            f"/shifts/{far_slot}/assignment"
        )
        require(client.put(far_url, headers=headers, json={
            "employee_id": employee,
            "expected_week_revision": 1,
            "expected_slot_revision": 1,
            "expected_location_revision": 1,
        }).status_code == 200,
                "Mixed saved-policy assignment failed through HTTP.")

        revision = detail.json()["week"]["revision"]
        admin("""
            CREATE FUNCTION scheduling.reject_assigned_week_delete_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='schedule.week_deleted' THEN
                    RAISE EXCEPTION 'Synthetic final week-delete audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_assigned_week_delete_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_assigned_week_delete_test();
        """, database)
        before = state()
        failed = client.post(
            detail_url+"/remove", headers=headers,
            json={"expected_week_revision": revision},
        )
        require(failed.status_code == 503,
                "Assigned-week deletion audit failure was not exercised.")
        require(state() == before,
                "Failed week deletion retained shift or assignment changes.")
        admin("""
            DROP TRIGGER reject_assigned_week_delete_test ON scheduling.audit_event;
            DROP FUNCTION scheduling.reject_assigned_week_delete_test();
        """, database)

        approval_count = admin(
            "SELECT count(*) FROM scheduling.assignment_override;",
            database,
        )
        removed = client.post(
            detail_url+"/remove", headers=headers,
            json={"expected_week_revision": revision},
        )
        require(removed.status_code == 200, "Assigned week deletion failed.")
        require(admin(
            "SELECT NOT EXISTS ("
            "SELECT FROM scheduling.shift_assignment a "
            "JOIN scheduling.shift_slot s ON s.id=a.shift_slot_id "
            "WHERE s.schedule_week_id=" + literal(week) + " AND a.active)",
            database,
        ) == "t", "Deleted week retained an active assignment.")
        require(admin(
            "SELECT count(*) FROM scheduling.assignment_override;",
            database,
        ) == approval_count, "Week deletion removed immutable approvals.")
        require(client.get(detail_url).status_code == 404,
                "Deleted week remained available.")

    print("ASSIGNED_SHIFT_HTTP_EDIT_AND_EMPLOYEE_RETENTION=PASS")
    print("RENEWED_ASSIGNMENT_AND_PRIOR_HISTORY=PASS")
    print("CSRF_ORIGIN_STALE_AND_FRESH_APPROVAL_GUARDS=PASS")
    print("FINAL_AUDIT_FAILURE_FULL_EDIT_ROLLBACK=PASS")

    print("TWO_CONNECTION_ASSIGNED_EDIT_ONE_WINNER=PASS")
    print("MIXED_SAVED_POLICY_HTTP_ASSIGNMENT=PASS")
    print("ASSIGNED_WEEK_REMOVAL_AND_APPROVAL_RETENTION=PASS")
    print("FINAL_WEEK_DELETE_AUDIT_FULL_ROLLBACK=PASS")
