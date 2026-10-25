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

    admin(f"""
        BEGIN;
        SET LOCAL ROLE shift_scheduling_owner;

        INSERT INTO scheduling.employee(
            organization_id,location_id,display_name,active
        )
        SELECT organization_id,id,'Inactive snapshot fixture',false
        FROM scheduling.location WHERE id={literal(location)};

        INSERT INTO scheduling.unavailability_day_lock(
            organization_id,location_id,employee_id,local_date,
            blocked_windows,reason,actor_user_id
        )
        SELECT organization_id,location_id,id,date '2096-01-02',
               '[[297,303],[607,608]]'::jsonb,'cutoff',user_id
        FROM scheduling.employee
        WHERE location_id={literal(location)} AND user_id IS NOT NULL;

        INSERT INTO scheduling.schedule_publication(
            organization_id,location_id,schedule_week_id,
            publication_number,source_week_revision,source_location_revision,
            roster,published_by
        )
        SELECT organization_id,location_id,id,n,revision,1,
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
        FROM scheduling.schedule_week CROSS JOIN generate_series(1,2) n
        WHERE id={literal(week)};

        DO $body$
        BEGIN
            IF (
                SELECT count(*) FROM scheduling.publication_availability pa
                JOIN scheduling.schedule_publication p ON p.id=pa.publication_id
                WHERE p.schedule_week_id={literal(week)}
            )<>32 THEN
                RAISE EXCEPTION 'Expected eight dates per employee and publication';
            END IF;

            IF EXISTS (
                SELECT FROM scheduling.publication_availability pa
                JOIN scheduling.schedule_publication p ON p.id=pa.publication_id
                JOIN scheduling.employee e ON e.id=pa.employee_id
                WHERE p.schedule_week_id={literal(week)}
                  AND e.user_id IS NOT NULL
                  AND pa.local_date=date '2096-01-02'
                  AND pa.blocked_windows<>'[[300,303],[607,608]]'::jsonb
            ) THEN
                RAISE EXCEPTION 'Exact frozen minutes were rounded or not clipped';
            END IF;

            BEGIN
                UPDATE scheduling.publication_availability
                SET blocked_windows='[]'::jsonb;
                RAISE EXCEPTION 'Snapshot update accepted';
            EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
            END;
            BEGIN
                DELETE FROM scheduling.publication_availability;
                RAISE EXCEPTION 'Snapshot deletion accepted';
            EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
            END;

            BEGIN
                UPDATE scheduling.unavailability_day_lock
                SET blocked_windows='[[297,303],[609,610]]'::jsonb
                WHERE location_id={literal(location)};
                RAISE EXCEPTION 'Published frozen-minute change accepted';
            EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
            END;
            BEGIN
                DELETE FROM scheduling.unavailability_day_lock
                WHERE location_id={literal(location)};
                RAISE EXCEPTION 'Published effective availability deletion accepted';
            EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
            END;
        END
        $body$;

        -- Changing only an adjacent-week portion preserves published minutes.
        UPDATE scheduling.unavailability_day_lock
        SET blocked_windows='[[100,101],[297,303],[607,608]]'::jsonb
        WHERE location_id={literal(location)};
        COMMIT;
    """, database)

    admin("""
        CREATE FUNCTION scheduling.reject_final_snapshot_test()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.local_date=date '2096-01-09' THEN
                RAISE EXCEPTION 'Synthetic final snapshot failure'
                    USING ERRCODE='P0002';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER reject_final_snapshot_test
        BEFORE INSERT ON scheduling.publication_availability
        FOR EACH ROW EXECUTE FUNCTION scheduling.reject_final_snapshot_test();
    """, database)
    admin(f"""
        BEGIN;
        SET LOCAL ROLE shift_scheduling_owner;
        DO $body$
        BEGIN
            BEGIN
                INSERT INTO scheduling.schedule_publication(
                    organization_id,location_id,schedule_week_id,
                    publication_number,source_week_revision,
                    source_location_revision,roster,published_by
                )
                SELECT organization_id,location_id,schedule_week_id,3,
                       source_week_revision,source_location_revision,
                       roster,published_by
                FROM scheduling.schedule_publication
                WHERE schedule_week_id={literal(week)}
                  AND publication_number=1;
                RAISE EXCEPTION 'Snapshot failure was not exercised';
            EXCEPTION WHEN no_data_found THEN NULL;
            END;

            IF (SELECT count(*) FROM scheduling.schedule_publication
                WHERE schedule_week_id={literal(week)})<>2 THEN
                RAISE EXCEPTION 'Failed snapshot left a publication';
            END IF;
            IF (
                SELECT count(*) FROM scheduling.publication_availability pa
                JOIN scheduling.schedule_publication p ON p.id=pa.publication_id
                WHERE p.schedule_week_id={literal(week)}
            )<>32 THEN
                RAISE EXCEPTION 'Failed snapshot left partial private records';
            END IF;
        END
        $body$;
        ROLLBACK;
    """, database)

    require(admin("""
        SELECT NOT has_table_privilege(
            'shift_scheduling_app','scheduling.publication_availability','SELECT'
        ) AND NOT has_table_privilege(
            'shift_scheduling_app','scheduling.publication_availability','INSERT'
        ) AND NOT has_table_privilege(
            'shift_scheduling_app','scheduling.publication_availability','UPDATE'
        ) AND NOT has_table_privilege(
            'shift_scheduling_app','scheduling.publication_availability','DELETE'
        ) AND NOT has_function_privilege(
            'shift_scheduling_app',
            'scheduling.capture_publication_availability()','EXECUTE'
        );
    """, database) == "t", "Private snapshot privileges were exposed.")

    print("ALL_EMPLOYEE_PRIVATE_PUBLICATION_SNAPSHOTS=PASS")
    print("EXACT_FROZEN_MINUTES_AND_PARTIAL_DATE_CLIPPING=PASS")
    print("SNAPSHOT_HISTORY_UPDATE_AND_DELETE=DENIED")
    print("PUBLISHED_FROZEN_INTERVAL_MUTATION=DENIED")
    print("ADJACENT_WEEK_FROZEN_PORTION_PRESERVED=PASS")
    print("FINAL_SNAPSHOT_FAILURE_FULL_PUBLICATION_ROLLBACK=PASS")
    print("PRIVATE_SNAPSHOT_RUNTIME_ACCESS=DENIED")
