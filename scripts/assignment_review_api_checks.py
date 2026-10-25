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


        assignment_before = admin(f"""
            SELECT to_jsonb(a)::text FROM scheduling.shift_assignment a
            WHERE id={literal(current["assignment_id"])};
        """, database)

        admin(f"""
            BEGIN;
            SET LOCAL ROLE shift_scheduling_owner;

            UPDATE scheduling.employee_qualification
            SET active=false,revision=revision+1
            WHERE employee_id={literal(employee)} AND job_role_id={literal(role)};
            UPDATE scheduling.employee_qualification
            SET active=true,revision=revision+1
            WHERE employee_id={literal(employee)} AND job_role_id={literal(role)};

            UPDATE scheduling.employee SET active=false
            WHERE id={literal(employee)};
            UPDATE scheduling.employee SET active=true
            WHERE id={literal(employee)};

            UPDATE scheduling.location SET minimum_rest_minutes=600
            WHERE id={literal(location)};
            UPDATE scheduling.location SET minimum_rest_minutes=720
            WHERE id={literal(location)};

            UPDATE scheduling.unavailability_date
            SET blocked_cells='[40,41]'::jsonb,revision=revision+1
            WHERE employee_id={literal(employee)}
              AND local_date=date '2096-01-02';

            DO $body$
            DECLARE review_before bigint;
            BEGIN
                IF (
                    SELECT count(DISTINCT cause)
                    FROM scheduling.assignment_review_event
                    WHERE assignment_id={literal(current["assignment_id"])}
                      AND status='pending'
                      AND cause IN (
                          'qualification','employee_status','rest_policy','availability'
                      )
                )<>4 THEN
                    RAISE EXCEPTION 'Assignment dependency changes were not queued';
                END IF;

                SELECT count(*) INTO review_before
                FROM scheduling.assignment_review_event;
                BEGIN
                    UPDATE scheduling.employee_qualification SET active=false
                    WHERE employee_id={literal(employee)}
                      AND job_role_id={literal(role)};
                    RAISE EXCEPTION 'Synthetic dependent-change rollback'
                        USING ERRCODE='P0002';
                EXCEPTION WHEN no_data_found THEN
                    NULL;
                END;
                IF (SELECT count(*) FROM scheduling.assignment_review_event)
                   <>review_before THEN
                    RAISE EXCEPTION 'Rolled-back change retained review events';
                END IF;

                BEGIN
                    DELETE FROM scheduling.assignment_review_event
                    WHERE assignment_id={literal(current["assignment_id"])};
                    RAISE EXCEPTION 'Review history deletion accepted';
                EXCEPTION WHEN SQLSTATE '55000' THEN
                    NULL;
                END;
            END
            $body$;
            COMMIT;
        """, database)

        require(admin(f"""
            SELECT to_jsonb(a)::text FROM scheduling.shift_assignment a
            WHERE id={literal(current["assignment_id"])};
        """, database) == assignment_before,
                "Review marking modified an existing assignment.")

        admin("""
            BEGIN;
            SET LOCAL ROLE shift_scheduling_app;
            DO $body$
            BEGIN
                BEGIN
                    PERFORM 1 FROM scheduling.assignment_review_event;
                    RAISE EXCEPTION 'Private review table access accepted';
                EXCEPTION WHEN insufficient_privilege THEN
                    NULL;
                END;
            END
            $body$;
            ROLLBACK;
        """, database)


        review_url = (
            f"/api/v1/staff/locations/{location}/schedule-weeks/{week}"
            "/assignments/review"
        )
        review_body = {
            "expected_week_revision": current["week_revision"],
            "expected_location_revision": 1,
        }
        require(client.post(review_url, json=review_body).status_code == 403,
                "Review accepted missing CSRF.")
        response = client.post(review_url, headers=headers, json=review_body)
        require(response.status_code == 200
                and response.json()["needs_review_count"] == 1,
                "Changed availability was not classified for review.")

        admin(f"""
            UPDATE scheduling.unavailability_date
            SET blocked_cells='[]'::jsonb,revision=revision+1
            WHERE employee_id={literal(employee)};
        """, database)
        response = client.post(review_url, headers=headers, json=review_body)
        require(response.status_code == 200
                and response.json()["needs_review_count"] == 0,
                "Resolved availability did not clear the review flag.")
        before_review = admin(
            "SELECT count(*) FROM scheduling.assignment_review_event;", database,
        )
        response = client.post(review_url, headers=headers, json=review_body)
        require(response.status_code == 200
                and response.json()["reviewed_count"] == 0
                and admin("SELECT count(*) FROM scheduling.assignment_review_event;",
                          database) == before_review,
                "Already-clear review was not a no-op.")

        admin(f"""
            UPDATE scheduling.unavailability_date
            SET blocked_cells='[40]'::jsonb,revision=revision+1
            WHERE employee_id={literal(employee)};
        """, database)
        admin("""
            CREATE FUNCTION scheduling.reject_review_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='assignment.reviewed' THEN
                    RAISE EXCEPTION 'Synthetic review audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_review_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_review_audit_test();
        """, database)
        before = counts()
        before_review = admin(
            "SELECT count(*) FROM scheduling.assignment_review_event;", database,
        )
        require(client.post(review_url, headers=headers, json=review_body)
                .status_code == 503, "Review audit failure was not exercised.")
        require(counts() == before and admin(
            "SELECT count(*) FROM scheduling.assignment_review_event;", database,
        ) == before_review, "Failed review retained partial results.")
        admin(
            "DROP TRIGGER reject_review_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_review_audit_test();", database,
        )
        require(admin(f"""
            SELECT to_jsonb(a)::text FROM scheduling.shift_assignment a
            WHERE id={literal(current["assignment_id"])};
        """, database) == assignment_before,
                "Reassessment changed the assignment itself.")

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

    print("DEPENDENCY_CHANGES_QUEUE_PERSISTENT_ASSIGNMENT_REVIEW=PASS")
    print("EXISTING_ASSIGNMENT_VALUES=PRESERVED")
    print("DEPENDENCY_ROLLBACK_ALSO_ROLLS_BACK_REVIEW_EVENTS=PASS")
    print("REVIEW_HISTORY_IMMUTABILITY_AND_PRIVATE_ACCESS=PASS")

    print("REVIEW_HTTP_PENDING_CONFLICT_AND_RESOLVED_STATES=PASS")
    print("REVIEW_AUDIT_FAILURE_ROLLS_BACK_RESULTS=PASS")
    print("CLEAR_REVIEW_NO_OP_AND_ASSIGNMENT_PRESERVATION=PASS")
