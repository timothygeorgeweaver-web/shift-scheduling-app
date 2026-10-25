"""Assignment constraints and immutable approvals in a disposable database."""

from uuid import uuid4

from psycopg import sql

from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin


def literal(value):
    return sql.Literal(value).as_string()


def verify(database, token, location, role):
    # All synthetic schedule/assignment records below are rolled back.
    week, slot, assignment, approval = [str(uuid4()) for _ in range(4)]
    location_sql = literal(location)
    role_sql = literal(role)

    admin(f"""
        BEGIN;
        SET LOCAL ROLE shift_scheduling_owner;

        CREATE FUNCTION pg_temp.expect_failure(statement text, expected text)
        RETURNS void LANGUAGE plpgsql AS $body$
        DECLARE actual text;
        BEGIN
            BEGIN
                EXECUTE statement;
            EXCEPTION WHEN OTHERS THEN
                GET STACKED DIAGNOSTICS actual=RETURNED_SQLSTATE;
            END;
            IF actual IS DISTINCT FROM expected THEN
                RAISE EXCEPTION 'Expected SQLSTATE %, received %',
                    expected,coalesce(actual,'success');
            END IF;
        END
        $body$;

        INSERT INTO scheduling.schedule_week(
            id,organization_id,location_id,week_start_date,
            week_start_day,timezone,created_by,business_day_start_minute
        )
        SELECT {literal(week)},l.organization_id,l.id,date '2096-01-02',
               0,l.timezone,e.user_id,l.business_day_start_minute
        FROM scheduling.location l
        JOIN scheduling.employee e ON e.location_id=l.id
        WHERE l.id={location_sql} AND e.user_id IS NOT NULL;

        INSERT INTO scheduling.shift_slot(
            id,organization_id,location_id,schedule_week_id,
            role_id,start_at,end_at,created_by
        )
        SELECT {literal(slot)},w.organization_id,w.location_id,w.id,
               {role_sql},timestamptz '2096-01-02 18:00:00+00',
               timestamptz '2096-01-03 02:00:00+00',w.created_by
        FROM scheduling.schedule_week w WHERE w.id={literal(week)};

        INSERT INTO scheduling.shift_assignment(
            id,organization_id,location_id,shift_slot_id,
            employee_id,created_by,updated_by
        )
        SELECT {literal(assignment)},s.organization_id,s.location_id,s.id,
               e.id,e.user_id,e.user_id
        FROM scheduling.shift_slot s
        JOIN scheduling.employee e ON e.location_id=s.location_id
        WHERE s.id={literal(slot)} AND e.user_id IS NOT NULL;

        DO $body$
        BEGIN
            IF NOT EXISTS (
                SELECT FROM scheduling.shift_assignment
                WHERE id={literal(assignment)}
            ) THEN
                RAISE EXCEPTION 'Assignment fixture missing';
            END IF;
        END
        $body$;

        SELECT pg_temp.expect_failure(
            'INSERT INTO scheduling.shift_assignment(
                organization_id,location_id,shift_slot_id,
                employee_id,created_by,updated_by)
             SELECT organization_id,location_id,shift_slot_id,
                    employee_id,created_by,updated_by
             FROM scheduling.shift_assignment
             WHERE id=' || quote_literal({literal(assignment)}),
            '23505'
        );

        INSERT INTO scheduling.assignment_override(
            id,organization_id,location_id,assignment_id,employee_id,
            assignment_revision,rule_code,reason,policy,rule_details,approved_by
        )
        SELECT {literal(approval)},organization_id,location_id,id,employee_id,
               revision,'daily_hours','Approved test coverage',
               '{{"daily_limit_minutes":480}}'::jsonb,
               '{{"estimated_work_minutes":510}}'::jsonb,created_by
        FROM scheduling.shift_assignment WHERE id={literal(assignment)};

        SELECT pg_temp.expect_failure(
            'UPDATE scheduling.assignment_override
             SET reason=''Changed'' WHERE id=' ||
             quote_literal({literal(approval)}),
            '55000'
        );
        SELECT pg_temp.expect_failure(
            'DELETE FROM scheduling.assignment_override WHERE id=' ||
             quote_literal({literal(approval)}),
            '55000'
        );

        SELECT pg_temp.expect_failure(
            'INSERT INTO scheduling.assignment_override(
                organization_id,location_id,assignment_id,employee_id,
                assignment_revision,rule_code,reason,policy,rule_details,
                approved_by)
             SELECT organization_id,location_id,assignment_id,employee_id,
                    assignment_revision,rule_code,reason,policy,rule_details,
                    approved_by
             FROM scheduling.assignment_override
             WHERE id=' || quote_literal({literal(approval)}),
            '23505'
        );

        SELECT pg_temp.expect_failure(
            'INSERT INTO scheduling.assignment_override(
                organization_id,location_id,assignment_id,employee_id,
                assignment_revision,rule_code,reason,policy,rule_details,
                approved_by)
             SELECT organization_id,location_id,assignment_id,employee_id,
                    assignment_revision,''overlap'',reason,policy,rule_details,
                    approved_by
             FROM scheduling.assignment_override
             WHERE id=' || quote_literal({literal(approval)}),
            '23514'
        );

        SELECT pg_temp.expect_failure(
            'INSERT INTO scheduling.assignment_override(
                organization_id,location_id,assignment_id,employee_id,
                assignment_revision,rule_code,reason,policy,rule_details,
                approved_by)
             SELECT organization_id,location_id,assignment_id,employee_id,
                    assignment_revision,''unavailability'',''   '',
                    policy,rule_details,approved_by
             FROM scheduling.assignment_override
             WHERE id=' || quote_literal({literal(approval)}),
            '23514'
        );

        SELECT pg_temp.expect_failure(
            'INSERT INTO scheduling.shift_assignment(
                organization_id,location_id,shift_slot_id,
                employee_id,created_by,updated_by,active)
             SELECT organization_id,gen_random_uuid(),shift_slot_id,
                    employee_id,created_by,updated_by,false
             FROM scheduling.shift_assignment
             WHERE id=' || quote_literal({literal(assignment)}),
            '23503'
        );

        UPDATE scheduling.shift_assignment
        SET active=false,revision=revision+1
        WHERE id={literal(assignment)};

        INSERT INTO scheduling.shift_assignment(
            organization_id,location_id,shift_slot_id,
            employee_id,created_by,updated_by
        )
        SELECT organization_id,location_id,shift_slot_id,
               employee_id,created_by,updated_by
        FROM scheduling.shift_assignment WHERE id={literal(assignment)};

        DO $body$
        BEGIN
            IF (SELECT count(*) FROM scheduling.shift_assignment
                WHERE shift_slot_id={literal(slot)} AND active)<>1
               OR NOT EXISTS (
                   SELECT FROM scheduling.assignment_override
                   WHERE id={literal(approval)} AND assignment_revision=1
               ) THEN
                RAISE EXCEPTION 'Replacement or approval history failed';
            END IF;
        END
        $body$;

        SET LOCAL ROLE shift_scheduling_app;
        SELECT pg_temp.expect_failure(
            'SELECT * FROM scheduling.shift_assignment','42501');
        SELECT pg_temp.expect_failure(
            'SELECT * FROM scheduling.assignment_override','42501');
        ROLLBACK;
    """, database)

    require(admin("""
        SELECT
          (SELECT count(*) FROM scheduling.shift_assignment)=0
          AND (SELECT count(*) FROM scheduling.assignment_override)=0
          AND (
            SELECT count(*)=2 AND bool_and(
                c.relrowsecurity
                AND pg_get_userbyid(c.relowner)='shift_scheduling_owner'
                AND NOT has_table_privilege(
                    'shift_scheduling_app',c.oid,'SELECT')
                AND NOT has_table_privilege(
                    'shift_scheduling_app',c.oid,'INSERT')
                AND NOT has_table_privilege(
                    'shift_scheduling_app',c.oid,'UPDATE')
                AND NOT has_table_privilege(
                    'shift_scheduling_app',c.oid,'DELETE')
            )
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='scheduling'
              AND c.relname IN ('shift_assignment','assignment_override')
          );
    """, database) == "t", "Assignment storage security or rollback failed.")

    print("ONE_ACTIVE_ASSIGNEE_AND_REPLACEMENT_HISTORY=PASS")
    print("SOFT_RULE_APPROVALS_AND_IMMUTABLE_HISTORY=PASS")
    print("HARD_RULE_OVERRIDE_AND_BLANK_REASON=DENIED")
    print("SCOPED_FOREIGN_KEYS_AND_DIRECT_TABLE_ACCESS=PASS")
    print("ASSIGNMENT_FIXTURES=ROLLED_BACK")
