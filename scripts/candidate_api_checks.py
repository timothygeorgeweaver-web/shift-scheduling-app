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

    url = (
        f"/api/v1/staff/locations/{location}/schedule-weeks/{week}"
        f"/shifts/{slot}/candidates"
    )
    with TestClient(app) as client:
        require(client.get(url).status_code == 401,
                "Anonymous candidate access accepted.")
        client.cookies.set(COOKIE_NAME, token)

        def read():
            before = admin(
                "SELECT count(*) FROM scheduling.audit_event;", database
            )
            response = client.get(url)
            require(response.status_code == 200, "Candidate HTTP read failed.")
            require(response.headers.get("cache-control") == "no-store",
                    "Candidate data could be cached.")
            require(admin(
                "SELECT count(*) FROM scheduling.audit_event;", database
            ) == before, "Candidate read wrote audit events.")
            body = response.json()
            flattened = [
                row for rows in body["groups"].values() for row in rows
            ]
            require(len(flattened) == 1, "Unexpected candidate fixture count.")
            row = flattened[0]
            require(not ({"calendar_days", "assigned", "unavailable"}
                         & set(row)),
                    "Candidate response exposed private interval inputs.")
            return body, row

        body, candidate = read()
        require(candidate["category"] == "blocked"
                and "not_qualified" in candidate["hard_blocks"],
                "Missing qualification did not block assignment.")

        admin(f"""
            BEGIN; SET LOCAL ROLE shift_scheduling_owner;
            INSERT INTO scheduling.employee_qualification(
                organization_id,location_id,employee_id,job_role_id,active
            )
            SELECT organization_id,location_id,id,{literal(role)},true
            FROM scheduling.employee WHERE location_id={literal(location)};
            COMMIT;
        """, database)
        body, candidate = read()
        require(candidate["category"] == "eligible",
                "Fresh qualification did not produce an eligible candidate.")

        admin(f"""
            BEGIN; SET LOCAL ROLE shift_scheduling_owner;
            INSERT INTO scheduling.unavailability_date(
                organization_id,location_id,employee_id,local_date,
                revision,blocked_cells,actor_user_id
            )
            SELECT organization_id,location_id,id,date '2096-01-02',
                   1,'[40]'::jsonb,user_id
            FROM scheduling.employee WHERE location_id={literal(location)};
            COMMIT;
        """, database)
        body, candidate = read()
        require(candidate["category"] == "override_required"
                and candidate["warnings"] == ["unavailability"],
                "Fresh unavailability did not produce a warning.")

        admin(f"""
            BEGIN; SET LOCAL ROLE shift_scheduling_owner;
            UPDATE scheduling.location
            SET minimum_rest_minutes=600,revision=revision+1
            WHERE id={literal(location)};
            UPDATE scheduling.employee
            SET active=false,revision=revision+1
            WHERE location_id={literal(location)};
            COMMIT;
        """, database)
        body, candidate = read()
        require(candidate["category"] == "blocked"
                and "inactive_employee" in candidate["hard_blocks"]
                and candidate["location_revision"] == 2,
                "Candidate state or saved policy revision was stale.")

        require(client.get(url.replace(location, str(uuid4()), 1))
                .status_code == 404, "Foreign location candidate read accepted.")
        require(client.get(url.replace(slot, str(uuid4()), 1))
                .status_code == 404, "Unknown shift candidate read accepted.")

        # Keep the account active but remove its staff permissions.
        admin(f"""
            DELETE FROM scheduling.member_permission
            WHERE organization_id=(
                SELECT organization_id FROM scheduling.location
                WHERE id={literal(location)}
            );
        """, database)
        require(client.get(url).status_code == 403,
                "Employee-only account could read candidate data.")

    require(admin("""
        SELECT (SELECT count(*) FROM scheduling.shift_assignment)=0
           AND (SELECT count(*) FROM scheduling.assignment_override)=0;
    """, database) == "t", "Read-only candidate checks created assignments.")

    print("CANDIDATE_HTTP_HARD_BLOCK_ELIGIBLE_AND_WARNING_GROUPS=PASS")
    print("FRESH_QUALIFICATION_AVAILABILITY_AND_POLICY_REVISION=PASS")
    print("CANDIDATE_AUTHORIZATION_PRIVACY_AND_NO_STORE=PASS")
    print("CANDIDATE_READS_ASSIGNMENT_WRITES=NONE")
