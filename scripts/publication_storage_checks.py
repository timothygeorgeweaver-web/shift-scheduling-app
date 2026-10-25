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
    from app.database import database_url
    from app.security import session_token_digest

    admin(f"""
        BEGIN;
        SET LOCAL ROLE shift_scheduling_owner;
        INSERT INTO scheduling.schedule_publication(
            organization_id,location_id,schedule_week_id,
            publication_number,source_week_revision,source_location_revision,
            roster,published_by
        )
        SELECT w.organization_id,w.location_id,w.id,n,1,1,
            jsonb_build_object(
                'schema_version',1,
                'week',jsonb_build_object(
                    'id',w.id,'location_id',w.location_id,
                    'week_start_date',w.week_start_date,
                    'timezone',w.timezone,
                    'business_day_start_minute',w.business_day_start_minute
                ),
                'shifts','[]'::jsonb
            ),w.created_by
        FROM scheduling.schedule_week w
        CROSS JOIN generate_series(1,2) n
        WHERE w.id={literal(week)};

        DO $body$
        BEGIN
            BEGIN
                UPDATE scheduling.schedule_publication
                SET source_week_revision=99
                WHERE schedule_week_id={literal(week)};
                RAISE EXCEPTION 'Publication update accepted';
            EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
            END;
            BEGIN
                DELETE FROM scheduling.schedule_publication
                WHERE schedule_week_id={literal(week)};
                RAISE EXCEPTION 'Publication deletion accepted';
            EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
            END;
            BEGIN
                INSERT INTO scheduling.schedule_publication(
                    organization_id,location_id,schedule_week_id,
                    publication_number,source_week_revision,
                    source_location_revision,roster,published_by
                )
                SELECT organization_id,location_id,schedule_week_id,
                       publication_number,source_week_revision,
                       source_location_revision,roster,published_by
                FROM scheduling.schedule_publication
                WHERE schedule_week_id={literal(week)}
                  AND publication_number=1;
                RAISE EXCEPTION 'Duplicate publication number accepted';
            EXCEPTION WHEN unique_violation THEN NULL;
            END;
        END
        $body$;
        COMMIT;
    """, database)

    with psycopg.connect(database_url()) as conn:
        def context():
            conn.execute(
                "SELECT set_config('scheduling.session_sha256',%s,true)",
                (session_token_digest(token),),
            )

        context()
        rows = conn.execute(
            "SELECT scheduling.read_schedule_publication_history(%s)",
            (week,),
        ).fetchone()[0]
        require([row["publication_number"] for row in rows] == [1, 2],
                "Publication history order or persistence failed.")
        require(all("roster" not in row for row in rows),
                "History metadata unexpectedly included roster contents.")
        conn.rollback()

        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            result = conn.execute(
                "SELECT has_table_privilege(current_user,"
                "'scheduling.schedule_publication',%s)",
                (privilege,),
            ).fetchone()[0]
            require(not result, "Direct publication-table privilege granted.")
        conn.rollback()

        conn.execute(
            "SELECT set_config('scheduling.session_sha256','invalid',true)"
        )
        try:
            conn.execute(
                "SELECT scheduling.read_schedule_publication_history(%s)",
                (week,),
            )
        except psycopg.Error as error:
            require(error.sqlstate == "42501",
                    "Unexpected unauthorized history rejection.")
            conn.rollback()
        else:
            raise SystemExit("STOP: invalid session read publication history.")

        context()
        try:
            conn.execute(
                "SELECT scheduling.read_schedule_publication_history(%s)",
                (str(uuid4()),),
            )
        except psycopg.Error as error:
            require(error.sqlstate == "42501",
                    "Unexpected unrelated-week history rejection.")
            conn.rollback()
        else:
            raise SystemExit("STOP: unrelated week history was exposed.")

    admin(f"""
        DELETE FROM scheduling.member_permission
        WHERE organization_id=(
            SELECT organization_id FROM scheduling.location
            WHERE id={literal(location)}
        ) AND permission='administrator';
    """, database)
    with psycopg.connect(database_url()) as conn:
        conn.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)",
            (session_token_digest(token),),
        )
        try:
            conn.execute(
                "SELECT scheduling.read_schedule_publication_history(%s)",
                (week,),
            )
        except psycopg.Error as error:
            require(error.sqlstate == "42501",
                    "Unexpected employee-only history rejection.")
            conn.rollback()
        else:
            raise SystemExit("STOP: employee-only account read staff history.")

    print("SEQUENTIAL_PUBLICATION_HISTORY=PASS")
    print("PUBLISHED_VERSION_UPDATE_AND_DELETE=DENIED")
    print("DUPLICATE_PUBLICATION_NUMBER=DENIED")
    print("STAFF_HISTORY_SESSION_AND_ROLE_AUTHORIZATION=PASS")
    print("DIRECT_PUBLICATION_TABLE_PRIVILEGES=NONE")
