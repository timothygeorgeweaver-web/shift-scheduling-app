"""Certify rollover storage without changing existing business records."""

import re

from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin

TABLES = ("location", "schedule_week", "schedule_template")


def existing_values(database, table):
    return admin(f"""
        SELECT md5(coalesce(string_agg(
            (to_jsonb(t)-'business_day_start_minute')::text,
            E'\\n'
            ORDER BY (to_jsonb(t)-'business_day_start_minute')::text COLLATE "C"
        ),''))
        FROM scheduling.{table} t;
    """, database)


def verify(test_db):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None,
        "Rollover storage certification requires a disposable database.",
    )
    before = {table: existing_values(test_db, table) for table in TABLES}
    install("0022_business_day_storage", test_db, rehearse=True)

    for table in TABLES:
        require(existing_values(test_db, table) == before[table],
                "Migration changed existing calendar times or records.")
        expected = 300 if table == "location" else 0
        require(admin(f"""
            SELECT count(*)>0
              AND bool_and(business_day_start_minute={expected})
            FROM scheduling.{table};
        """, test_db) == "t", "Unexpected stored rollover baseline.")

        # Every invalid write is caught within a rolled-back subtransaction.
        admin(f"""
            BEGIN;
            SET LOCAL ROLE shift_scheduling_owner;
            DO $test$
            DECLARE
                invalid_value integer;
            BEGIN
                FOREACH invalid_value IN ARRAY ARRAY[-15,1,301,1440] LOOP
                    BEGIN
                        UPDATE scheduling.{table}
                        SET business_day_start_minute=invalid_value;
                        RAISE EXCEPTION 'Invalid rollover was accepted';
                    EXCEPTION WHEN check_violation THEN
                        NULL;
                    END;
                END LOOP;
                BEGIN
                    UPDATE scheduling.{table}
                    SET business_day_start_minute=NULL;
                    RAISE EXCEPTION 'Null rollover was accepted';
                EXCEPTION WHEN not_null_violation THEN
                    NULL;
                END;
            END
            $test$;
            ROLLBACK;
        """, test_db)
        require(existing_values(test_db, table) == before[table],
                "Constraint certification changed existing records.")

    print("LOCATION_ROLLOVER_DEFAULT_05_00=PASS")
    print("EXISTING_WEEK_AND_TEMPLATE_MIDNIGHT_SEMANTICS=PRESERVED")
    print("ROLLOVER_QUARTER_HOUR_AND_NULL_CONSTRAINTS=PASS")
    print("EXISTING_SHIFT_AND_CALENDAR_VALUES=UNCHANGED")
