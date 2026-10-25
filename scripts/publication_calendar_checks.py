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

    def add_publication(number):
        admin(f"""
            INSERT INTO scheduling.schedule_publication(
                organization_id,location_id,schedule_week_id,
                publication_number,source_week_revision,source_location_revision,
                roster,published_by
            )
            SELECT organization_id,location_id,id,{number},revision,1,
                jsonb_build_object(
                    'schema_version',1,
                    'week',jsonb_build_object(
                        'id',id,'location_id',location_id,
                        'week_start_date',week_start_date,
                        'timezone',timezone,
                        'business_day_start_minute',business_day_start_minute
                    ),
                    'shifts','[]'::jsonb
                ),created_by
            FROM scheduling.schedule_week WHERE id={literal(week)};
        """, database)

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        def row():
            response = client.get(
                f"/api/v1/staff/locations/{location}/schedule-weeks",
                params={"start": "2096-01-01", "end": "2096-01-31"},
            )
            require(response.status_code == 200, "Calendar metadata read failed.")
            return next(item for item in response.json()["weeks"]
                        if item["id"] == week)

        require(row()["publication_number"] == 0,
                "Unpublished week has a publication number.")
        add_publication(1)
        current = row()
        require(current["publication_number"] == 1
                and not current["has_unpublished_changes"],
                "Initial publication metadata is incorrect.")
        admin(
            "UPDATE scheduling.schedule_week SET revision=revision+1 "
            "WHERE id=" + literal(week), database,
        )
        require(row()["has_unpublished_changes"],
                "Working amendment was not identified.")
        add_publication(2)
        current = row()
        require(current["publication_number"] == 2
                and not current["has_unpublished_changes"],
                "Republished calendar metadata is incorrect.")

    print("CALENDAR_DRAFT_AND_PUBLISHED_VERSION_METADATA=PASS")
    print("WORKING_REVISION_CHANGE_AND_REPUBLICATION_STATUS=PASS")
