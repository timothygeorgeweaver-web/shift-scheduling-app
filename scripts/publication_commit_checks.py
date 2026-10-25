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
