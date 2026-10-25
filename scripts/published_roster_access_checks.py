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
    unfilled_slot = str(uuid4())
    admin(f"""
        INSERT INTO scheduling.employee_qualification(
            organization_id,location_id,employee_id,job_role_id,active
        )
        SELECT organization_id,location_id,id,{literal(role)},true
        FROM scheduling.employee WHERE id={literal(employee)};
        INSERT INTO scheduling.shift_slot(
            id,organization_id,location_id,schedule_week_id,
            role_id,start_at,end_at,created_by
        )
        SELECT {literal(unfilled_slot)},organization_id,location_id,
               schedule_week_id,role_id,start_at,end_at,created_by
        FROM scheduling.shift_slot WHERE id={literal(slot)};
    """, database)
    base = f"/api/v1/staff/locations/{location}/schedule-weeks/{week}"
    headers = {"X-CSRF-Token": _csrf(token)}

    def state():
        return admin("""
            SELECT json_build_array(
                (SELECT jsonb_agg(to_jsonb(t) ORDER BY id)
                 FROM scheduling.schedule_publication t),
                (SELECT jsonb_agg(to_jsonb(t) ORDER BY publication_id,employee_id,local_date)
                 FROM scheduling.publication_availability t),
                (SELECT jsonb_agg(to_jsonb(t) ORDER BY id)
                 FROM scheduling.shift_assignment t),
                (SELECT jsonb_agg(to_jsonb(t) ORDER BY id)
                 FROM scheduling.shift_slot t),
                (SELECT jsonb_agg(to_jsonb(t) ORDER BY id)
                 FROM scheduling.schedule_week t),
                (SELECT count(*) FROM scheduling.audit_event),
                (SELECT count(*) FROM scheduling.assignment_review_event)
            );
        """, database)

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        assigned = client.put(base+f"/shifts/{slot}/assignment",
            headers=headers, json={
                "employee_id": employee,
                "expected_week_revision": 1,
                "expected_slot_revision": 1,
                "expected_location_revision": 1,
            })
        require(assigned.status_code == 200, "Publication assignment fixture failed.")
        body = {
            "expected_week_revision": assigned.json()["week_revision"],
            "expected_location_revision": 1,
            "expected_publication_number": 0,
            "confirm_publish": True,
            "confirm_unfilled": True,
        }
        before = state()
        require(client.post(base+"/publish", json=body).status_code == 403,
                "Publication accepted missing CSRF.")
        require(client.post(base+"/publish", headers={
            **headers, "Origin": "https://invalid.example",
        }, json=body).status_code == 403, "Publication accepted foreign origin.")
        require(client.post(base+"/publish", headers=headers, json={
            **body, "confirm_publish": False,
        }).status_code == 422, "Publication confirmation was not required.")
        require(client.post(base+"/publish", headers=headers, json={
            **body, "confirm_unfilled": False,
        }).status_code == 422, "Unfilled confirmation was not required.")
        require(client.post(base+"/publish", headers=headers, json={
            **body, "reviewed": {"can_publish": True},
        }).status_code == 422, "Client publication decision was accepted.")
        require(state() == before, "Rejected publication wrote records.")

        admin("""
            CREATE FUNCTION scheduling.reject_publication_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='schedule.published' THEN
                    RAISE EXCEPTION 'Synthetic final publication audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_publication_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_publication_audit_test();
        """, database)
        require(client.post(base+"/publish", headers=headers,
                            json=body).status_code == 503,
                "Final publication audit failure was not exercised.")
        require(state() == before,
                "Failed publication retained roster, availability or audit changes.")
        admin("""
            DROP TRIGGER reject_publication_audit_test ON scheduling.audit_event;
            DROP FUNCTION scheduling.reject_publication_audit_test();
        """, database)

        first = client.post(base+"/publish", headers=headers, json=body)
        require(first.status_code == 201
                and first.json()["publication_number"] == 1
                and first.json()["unfilled_count"] == 1,
                "Confirmed initial publication failed.")
        require(admin(f"""
            SELECT count(*)=8 FROM scheduling.publication_availability
            WHERE publication_id={literal(first.json()["id"])};
        """, database) == "t", "Publication availability snapshots are incomplete.")
        require(client.post(base+"/publish", headers=headers,
                            json=body).status_code == 409,
                "Repeated stale publication was accepted.")

        from app.security import new_session_token, session_token_digest

        viewer, viewer_employee, other_org, other_location, draft_week = [
            str(uuid4()) for _ in range(5)
        ]
        viewer_token = new_session_token()
        admin(f"""
            INSERT INTO scheduling.app_user(id,email)
            VALUES ({literal(viewer)},{literal(viewer+'@example.invalid')});
            INSERT INTO scheduling.organization_member(organization_id,user_id)
            SELECT organization_id,{literal(viewer)}
            FROM scheduling.location WHERE id={literal(location)};
            INSERT INTO scheduling.employee(
                id,organization_id,location_id,display_name,user_id
            )
            SELECT {literal(viewer_employee)},organization_id,id,
                   'Employee-only roster viewer',{literal(viewer)}
            FROM scheduling.location WHERE id={literal(location)};
            INSERT INTO scheduling.auth_session(token_sha256,user_id,expires_at)
            VALUES ({literal(session_token_digest(viewer_token))},
                    {literal(viewer)},now()+interval '1 hour');
            INSERT INTO scheduling.organization(id,name)
            VALUES ({literal(other_org)},'Unrelated roster business');
            INSERT INTO scheduling.location(id,organization_id,name,timezone)
            VALUES ({literal(other_location)},{literal(other_org)},
                    'Unrelated roster location','America/Los_Angeles');
            INSERT INTO scheduling.schedule_week(
                id,organization_id,location_id,week_start_date,
                week_start_day,timezone,created_by,business_day_start_minute
            )
            SELECT {literal(draft_week)},organization_id,location_id,
                   week_start_date+14,week_start_day,timezone,created_by,
                   business_day_start_minute
            FROM scheduling.schedule_week WHERE id={literal(week)};
        """, database)

        employee_base = f"/api/v1/me/published-schedules/{location}"
        employee_url = employee_base+f"/{week}"

        def viewer_get(path, params=None):
            with TestClient(app) as viewer_client:
                viewer_client.cookies.set(COOKIE_NAME, viewer_token)
                return viewer_client.get(path, params=params)

        with TestClient(app) as anonymous:
            require(anonymous.get(employee_url).status_code == 401,
                    "Anonymous user read a published roster.")
        official = viewer_get(employee_url)
        require(official.status_code == 200
                and official.json()["publication"]["publication_number"] == 1,
                "Employee-only account could not read the official roster.")
        require(any(
            row["employee"] and row["employee"]["id"] == employee
            for row in official.json()["roster"]["shifts"]
        ), "Employee roster hid other team members.")
        require(official.headers.get("cache-control") == "no-store",
                "Employee roster lacked no-store.")
        require(viewer_get(
            f"/api/v1/me/published-schedules/{other_location}/{week}"
        ).status_code == 404, "Employee crossed a business/location boundary.")
        require(viewer_get(employee_base+f"/{draft_week}").status_code == 404,
                "Employee read an unpublished draft.")
        listed = viewer_get(employee_base, {
            "start": "2096-01-01", "end": "2096-01-31",
        })
        require(listed.status_code == 200 and len(listed.json()) == 1,
                "Published list included a draft or duplicate version.")
        require(viewer_get(base+"/publications").status_code in (403, 404),
                "Employee-only account accessed staff publication history.")

        original_roster = admin(
            "SELECT roster::text FROM scheduling.schedule_publication WHERE id=" +
            literal(first.json()["id"]), database,
        )
        edited = client.patch(base+f"/shifts/{unfilled_slot}", headers=headers, json={
            "role_id": role, "local_date": "2096-01-02",
            "start_minute": 720, "end_minute": 1080,
            "expected_week_revision": body["expected_week_revision"],
            "expected_shift_revision": 1,
            "note": "Unpublished amendment fixture",
        })
        require(edited.status_code == 200, "Working amendment failed.")
        require(admin(
            "SELECT roster::text FROM scheduling.schedule_publication WHERE id=" +
            literal(first.json()["id"]), database,
        ) == original_roster, "Working edit changed the official snapshot.")

        require(viewer_get(employee_url).json() == official.json(),
                "Unpublished amendment leaked into employee roster.")
        body.update(
            expected_week_revision=edited.json()["week"]["revision"],
            expected_publication_number=1,
        )
        second = client.post(base+"/publish", headers=headers, json=body)
        require(second.status_code == 201
                and second.json()["publication_number"] == 2,
                "Amendment republication failed.")
        require(admin(
            "SELECT roster::text FROM scheduling.schedule_publication WHERE id=" +
            literal(first.json()["id"]), database,
        ) == original_roster, "Republication changed the prior official snapshot.")

        latest = viewer_get(employee_url)
        require(latest.status_code == 200
                and latest.json()["publication"]["publication_number"] == 2,
                "Employee did not receive the republished version.")
        require("Unpublished amendment fixture" not in latest.text
                and "blocked_windows" not in latest.text
                and "approval" not in latest.text,
                "Employee roster exposed internal fields.")
        history = client.get(base+"/publications")
        require(history.status_code == 200 and
                [row["publication_number"] for row in history.json()] == [1, 2],
                "Staff publication history failed.")
        prior = client.get(base+"/publications/1")
        require(prior.status_code == 200
                and prior.json()["roster"] == official.json()["roster"],
                "Staff could not reconstruct the original official version.")

        admin(f"""
            UPDATE scheduling.employee SET active=false
            WHERE id={literal(viewer_employee)};
        """, database)
        require(viewer_get(employee_url).status_code == 404,
                "Inactive employee retained roster access.")
        admin(f"""
            UPDATE scheduling.employee SET active=true
            WHERE id={literal(viewer_employee)};
            UPDATE scheduling.auth_session SET revoked_at=clock_timestamp()
            WHERE token_sha256={literal(session_token_digest(viewer_token))};
        """, database)
        require(viewer_get(employee_url).status_code == 401,
                "Revoked employee session retained roster access.")

        before = state()
        removed = client.post(base+"/remove", headers=headers, json={
            "expected_week_revision": body["expected_week_revision"],
        })
        require(removed.status_code == 423, "Published week deletion was accepted.")
        require(state() == before,
                "Denied published-week deletion changed shifts or assignments.")

        admin(f"""
            DELETE FROM scheduling.member_permission
            WHERE organization_id=(
                SELECT organization_id FROM scheduling.location
                WHERE id={literal(location)}
            ) AND permission='administrator';
        """, database)
        body["expected_publication_number"] = 2
        require(client.post(base+"/publish", headers=headers, json=body).status_code
                in (403, 404), "Employee-only account could publish.")

    print("CONFIRMED_PUBLICATION_AND_UNFILLED_GUARDS=PASS")
    print("PUBLICATION_PRIVATE_SNAPSHOTS_AND_FINAL_AUDIT_ATOMICITY=PASS")
    print("STALE_AND_CLIENT_DECISION_REJECTION=PASS")
    print("WORKING_AMENDMENT_OFFICIAL_ROSTER_ISOLATION=PASS")
    print("REPUBLISH_SEQUENTIAL_VERSION_AND_PRIOR_HISTORY=PASS")
    print("PUBLISHED_WEEK_DELETION_AND_ROLE_GUARDS=PASS")

    print("EMPLOYEE_ONLY_FULL_TEAM_OFFICIAL_ROSTER=PASS")
    print("DRAFT_AMENDMENT_AND_PRIVATE_DATA_PROTECTION=PASS")
    print("REPUBLISH_EMPLOYEE_UPDATE_AND_STAFF_VERSION_HISTORY=PASS")
    print("CROSS_BUSINESS_INACTIVE_AND_REVOKED_ACCESS=DENIED")
