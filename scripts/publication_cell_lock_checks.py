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

    from datetime import date, timedelta
    from app.auth_routes import _csrf

    first = date(2096, 1, 2)
    last = first+timedelta(days=7)
    admin(f"""
        INSERT INTO scheduling.schedule_publication(
            organization_id,location_id,schedule_week_id,
            publication_number,source_week_revision,source_location_revision,
            roster,published_by
        )
        SELECT organization_id,location_id,id,1,revision,1,
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
    headers = {"X-CSRF-Token": _csrf(token)}
    business = f"/api/v1/me/business-planner/{location}"
    legacy = f"/api/v1/me/planner/{location}"

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        response = client.get(business, params={"start": first.isoformat()})
        require(response.status_code == 200, "Published-edge planner read failed.")
        rows = response.json()["calendar_days"]
        require(rows[0]["publication_locked_windows"] == [[300, 1440]]
                and rows[0]["editable"],
                "First-date publication edge is incorrect.")
        require(rows[-1]["publication_locked_windows"] == [[0, 300]]
                and rows[-1]["editable"],
                "Final-date publication edge is incorrect.")
        require(not rows[1]["editable"]
                and rows[1]["lock_reason"] == "published_week",
                "Full published date was not locked.")

        # The first date's pre-05:00 cells belong to the previous business week.
        response = client.put(business, headers=headers, json={
            "first_date": (first-timedelta(days=7)).isoformat(),
            "expected_location_revision": 1,
            "changes": [{
                "date": first.isoformat(), "expected_revision": 0, "cells": [0],
            }],
        })
        require(response.status_code == 200,
                "Previous-week edge was incorrectly frozen.")

        response = client.put(business, headers=headers, json={
            "first_date": last.isoformat(),
            "expected_location_revision": 1,
            "changes": [{
                "date": last.isoformat(), "expected_revision": 0, "cells": [20],
            }],
        })
        require(response.status_code == 200,
                "Following-week edge was incorrectly frozen.")

        # Old calendar API must also reject a change to a published cell.
        response = client.put(legacy, headers=headers, json={
            "first_date": first.isoformat(),
            "expected_location_revision": 1,
            "changes": [{
                "date": first.isoformat(), "expected_revision": 1,
                "cells": [0, 20],
            }],
        })
        require(response.status_code == 423,
                "Calendar API bypassed published-cell protection.")

        before = admin(
            "SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY employee_id,local_date),"
            "'[]'::jsonb) FROM scheduling.unavailability_date d;",
            database,
        )
        response = client.put(legacy, headers=headers, json={
            "first_date": first.isoformat(),
            "expected_location_revision": 1,
            "changes": [
                {
                    "date": first.isoformat(), "expected_revision": 1,
                    "cells": [0, 1],
                },
                {
                    "date": (first+timedelta(days=1)).isoformat(),
                    "expected_revision": 0, "cells": [20],
                },
            ],
        })
        require(response.status_code == 423, "Mixed locked save was accepted.")
        require(admin(
            "SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY employee_id,local_date),"
            "'[]'::jsonb) FROM scheduling.unavailability_date d;",
            database,
        ) == before, "Failed multi-date save retained an unlocked-date change.")

        weekly = {str(n): [] for n in range(7)}
        weekly[str(first.weekday())] = [0]
        response = client.post(business+"/bulk/preview", headers=headers, json={
            "first_date": first.isoformat(), "last_date": first.isoformat(),
            "expected_location_revision": 1, "weekly_cells": weekly,
        })
        require(response.status_code == 200
                and response.json()["changed_dates"] == []
                and response.json()["skipped_locked_dates"][0]["reason"]
                    == "published_week",
                "Mass preview did not skip the published-cell change.")

    print("FIRST_AND_FINAL_PUBLICATION_DATE_PORTIONS=PASS")
    print("ADJACENT_BUSINESS_WEEK_HOURS_REMAIN_EDITABLE=PASS")
    print("CALENDAR_API_PUBLISHED_CELL_BYPASS=DENIED")
    print("LOCKED_MULTI_DATE_SAVE_FULL_ROLLBACK=PASS")
    print("MASS_EDIT_PREVIEW_PUBLISHED_CELLS=SKIPPED")
