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

        review = client.post(
            base+"/publication-review", headers=headers, json=body,
        )
        require(review.status_code == 200 and review.json()["can_publish"],
                "Initial publication review failed.")

        admin(f"""
            UPDATE scheduling.employee_qualification SET active=false
            WHERE employee_id={literal(employee)}
              AND job_role_id={literal(role)};
        """, database)
        before = state()
        rejected = client.post(base+"/publish", headers=headers, json=body)
        require(rejected.status_code == 422,
                "Publication accepted a qualification removed after review.")
        require(state() == before,
                "Rejected fresh eligibility check changed publication records.")

        admin(f"""
            UPDATE scheduling.employee_qualification SET active=true
            WHERE employee_id={literal(employee)}
              AND job_role_id={literal(role)};
        """, database)

    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def publish_once():
        with TestClient(app) as concurrent_client:
            concurrent_client.cookies.set(COOKIE_NAME, token)
            barrier.wait(timeout=15)
            return concurrent_client.post(
                base+"/publish", headers=headers, json=body,
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = [executor.submit(publish_once) for _ in range(2)]
        statuses = sorted(attempt.result(timeout=45) for attempt in attempts)

    require(statuses == [201, 409],
            "Competing publication attempts did not produce one winner.")
    require(admin(f"""
        SELECT count(*)=1 AND min(publication_number)=1
        FROM scheduling.schedule_publication
        WHERE schedule_week_id={literal(week)};
    """, database) == "t",
            "Competing saves created an unexpected publication history.")
    require(admin(f"""
        SELECT count(*)=8
        FROM scheduling.publication_availability a
        JOIN scheduling.schedule_publication p ON p.id=a.publication_id
        WHERE p.schedule_week_id={literal(week)};
    """, database) == "t",
            "Winning publication did not retain exactly one snapshot set.")

    print("CHANGED_QUALIFICATION_AFTER_REVIEW_PUBLICATION=DENIED")
    print("REJECTED_PUBLICATION_RECORDS=UNCHANGED")
    print("TWO_CONNECTION_PUBLICATION_ONE_WINNER=PASS")
    print("SINGLE_PUBLICATION_AND_PRIVATE_SNAPSHOT_SET=PASS")
