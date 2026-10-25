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

    import psycopg
    from app.auth_routes import _csrf
    from app.database import database_url
    from app.security import session_token_digest

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
        INSERT INTO scheduling.unavailability_date(
            organization_id,location_id,employee_id,local_date,
            revision,blocked_cells,actor_user_id
        )
        SELECT organization_id,location_id,id,date '2096-01-02',
               1,'[40]'::jsonb,user_id
        FROM scheduling.employee WHERE id={literal(employee)};
    """, database)

    base = f"/api/v1/staff/locations/{location}/schedule-weeks/{week}"
    headers = {"X-CSRF-Token": _csrf(token)}
    private_reason = "PRIVATE_PUBLICATION_TEST_REASON"

    def counts():
        return admin("""
            SELECT json_build_array(
                (SELECT count(*) FROM scheduling.schedule_publication),
                (SELECT count(*) FROM scheduling.audit_event),
                (SELECT count(*) FROM scheduling.assignment_review_event),
                (SELECT count(*) FROM scheduling.assignment_override)
            );
        """, database)

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        saved = client.put(base+f"/shifts/{slot}/assignment",
            headers=headers, json={
                "employee_id": employee,
                "expected_week_revision": 1,
                "expected_slot_revision": 1,
                "expected_location_revision": 1,
                "approvals": [{
                    "rule_code": "unavailability", "reason": private_reason,
                }],
            })
        require(saved.status_code == 200, "Approved fixture assignment failed.")
        body = {
            "expected_week_revision": saved.json()["week_revision"],
            "expected_location_revision": 1,
            "expected_publication_number": 0,
        }
        url = base+"/publication-review"
        before = counts()
        require(client.post(url, json=body).status_code == 403,
                "Publication review accepted missing CSRF.")
        require(client.post(url, headers={
            **headers, "Origin": "https://invalid.example",
        }, json=body).status_code == 403,
                "Publication review accepted foreign origin.")

        response = client.post(url, headers=headers, json=body)
        require(response.status_code == 200 and response.json()["can_publish"],
                "Current approved warning did not pass publication review.")
        require(response.json()["warnings"][0]["approved"],
                "Current warning approval was not recognized.")
        require(private_reason not in response.text
                and "calendar_days" not in response.text
                and "blocked_cells" not in response.text,
                "Publication review exposed private approval/availability data.")
        require(response.headers.get("cache-control") == "no-store",
                "Publication review lacked no-store.")
        require(counts() == before, "Publication review wrote records.")

        admin(f"""
            UPDATE scheduling.unavailability_date
            SET blocked_cells='[40,41]'::jsonb,revision=revision+1
            WHERE employee_id={literal(employee)}
              AND local_date=date '2096-01-02';
        """, database)
        before = counts()
        response = client.post(url, headers=headers, json=body)
        require(response.status_code == 200
                and not response.json()["can_publish"]
                and not response.json()["warnings"][0]["approved"],
                "Changed availability reused an old approval.")
        require(counts() == before, "Changed-condition review wrote records.")

        require(client.post(url, headers=headers, json={
            **body, "expected_publication_number": 1,
        }).status_code == 409, "Stale publication number was accepted.")

        # Even a stored clear badge cannot bypass fresh qualification checks.
        admin(f"""
            UPDATE scheduling.employee_qualification SET active=false
            WHERE employee_id={literal(employee)}
              AND job_role_id={literal(role)};
        """, database)
        response = client.post(url, headers=headers, json=body)
        require(response.status_code == 200
                and any("not_qualified" in row["rule_codes"]
                        for row in response.json()["hard_conflicts"]),
                "Publication review missed a fresh hard conflict.")

    parameters = (
        location, week, saved.json()["week_revision"], 1, 0,
    )
    statement = (
        "SELECT scheduling.lock_publication_scope(%s,%s,%s,%s,%s)"
    )
    with psycopg.connect(database_url()) as first:
        first.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)",
            (session_token_digest(token),),
        )
        first.execute(statement, parameters)
        with psycopg.connect(database_url()) as second:
            second.execute(
                "SELECT set_config('scheduling.session_sha256',%s,true)",
                (session_token_digest(token),),
            )
            second.execute("SET LOCAL lock_timeout='150ms'")
            try:
                second.execute(statement, parameters)
            except psycopg.Error as error:
                require(error.sqlstate == "55P03",
                        "Unexpected concurrent-publication lock result.")
                second.rollback()
            else:
                raise SystemExit("STOP: publication scopes did not serialize.")
        first.rollback()

    admin(f"""
        DELETE FROM scheduling.member_permission
        WHERE organization_id=(
            SELECT organization_id FROM scheduling.location
            WHERE id={literal(location)}
        ) AND permission='administrator';
    """, database)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        require(client.post(url, headers=headers, json=body).status_code
                in (403, 404), "Employee-only account accessed publication review.")

    print("FRESH_WHOLE_WEEK_PUBLICATION_REVIEW=PASS")
    print("CHANGED_AVAILABILITY_INVALIDATES_OLD_APPROVAL=PASS")
    print("PRIVATE_REASON_AND_INTERVAL_PROTECTION=PASS")
    print("PUBLICATION_REVIEW_CSRF_STALE_AND_ROLE_GUARDS=PASS")
    print("TWO_CONNECTION_PUBLICATION_SCOPE_SERIALIZATION=PASS")
    print("REVIEW_PUBLICATION_AND_HISTORY_WRITES=NONE")
