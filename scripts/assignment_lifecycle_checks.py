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


    from app.security import session_token_digest

    employee = admin(
        "SELECT id FROM scheduling.employee WHERE location_id=" +
        literal(location) + ";", database,
    )
    digest = session_token_digest(token)
    admin(f"""
        BEGIN;
        SET LOCAL ROLE shift_scheduling_owner;
        SELECT set_config('scheduling.session_sha256',{literal(digest)},true);

        INSERT INTO scheduling.employee_qualification(
            organization_id,location_id,employee_id,job_role_id,active
        )
        SELECT organization_id,location_id,id,{literal(role)},true
        FROM scheduling.employee WHERE id={literal(employee)};

        CREATE TEMP TABLE recorder_before AS
        SELECT
            (SELECT count(*) FROM scheduling.audit_event) AS audit_count,
            (SELECT revision FROM scheduling.schedule_week
             WHERE id={literal(week)}) AS week_revision,
            (SELECT revision FROM scheduling.shift_slot
             WHERE id={literal(slot)}) AS slot_revision;

        CREATE FUNCTION scheduling.reject_retirement_test()
        RETURNS trigger LANGUAGE plpgsql AS $test$
        BEGIN
            IF NEW.action='assignment.retired'
               AND current_setting('scheduling.fail_retirement',true)='on' THEN
                RAISE EXCEPTION 'Synthetic retirement audit failure';
            END IF;
            RETURN NEW;
        END
        $test$;
        CREATE TRIGGER reject_retirement_test
        BEFORE INSERT ON scheduling.audit_event
        FOR EACH ROW EXECUTE FUNCTION scheduling.reject_retirement_test();

        DO $body$
        DECLARE result jsonb; rejected boolean; audit_before bigint;
        BEGIN
            result := scheduling.record_assignment_internal(
                {literal(slot)},{literal(employee)},1,1,1,NULL,0,false,
                '{{"category":"override_required","hard_blocks":[],
                   "warnings":["daily_hours"],"daily_work":[]}}'::jsonb,
                '{{"daily_hours":"Synthetic approved coverage"}}'::jsonb
            );
            IF (SELECT count(*) FROM scheduling.shift_assignment
                WHERE shift_slot_id={literal(slot)} AND active)<>1
               OR NOT EXISTS (
                   SELECT FROM scheduling.assignment_override
                   WHERE assignment_id=(result->>'assignment_id')::uuid
                     AND rule_code='daily_hours'
               )
               OR (SELECT revision FROM scheduling.schedule_week
                   WHERE id={literal(week)})<>2
               OR (SELECT revision FROM scheduling.shift_slot
                   WHERE id={literal(slot)})<>2 THEN
                RAISE EXCEPTION 'Atomic assignment persistence failed';
            END IF;

            rejected := false;
            BEGIN
                UPDATE scheduling.shift_slot
                SET end_at=end_at+interval '15 minutes'
                WHERE id={literal(slot)};
            EXCEPTION WHEN SQLSTATE '55000' THEN
                rejected := true;
            END;
            IF NOT rejected THEN
                RAISE EXCEPTION 'Assigned shift timing changed without revalidation';
            END IF;

            SELECT count(*) INTO audit_before FROM scheduling.audit_event;
            PERFORM set_config('scheduling.fail_retirement','on',true);
            rejected := false;
            BEGIN
                UPDATE scheduling.shift_slot SET active=false
                WHERE id={literal(slot)};
            EXCEPTION WHEN raise_exception THEN
                IF SQLERRM<>'Synthetic retirement audit failure' THEN
                    RAISE;
                END IF;
                rejected := true;
            END;

            IF NOT rejected
               OR NOT EXISTS (
                   SELECT FROM scheduling.shift_slot
                   WHERE id={literal(slot)} AND active
               )
               OR NOT EXISTS (
                   SELECT FROM scheduling.shift_assignment
                   WHERE shift_slot_id={literal(slot)} AND active
               )
               OR (SELECT count(*) FROM scheduling.audit_event)<>audit_before THEN
                RAISE EXCEPTION 'Retirement failure left partial state';
            END IF;

            PERFORM set_config('scheduling.fail_retirement','off',true);
            UPDATE scheduling.shift_slot SET active=false
            WHERE id={literal(slot)};
            IF EXISTS (
                SELECT FROM scheduling.shift_assignment
                WHERE shift_slot_id={literal(slot)} AND active
            ) OR NOT EXISTS (
                SELECT FROM scheduling.assignment_override
                WHERE assignment_id=(result->>'assignment_id')::uuid
            ) OR NOT EXISTS (
                SELECT FROM scheduling.audit_event
                WHERE action='assignment.retired'
                  AND after_data->>'assignment_id'=result->>'assignment_id'
            ) THEN
                RAISE EXCEPTION 'Assignment retirement or history retention failed';
            END IF;
        END
        $body$;
        ROLLBACK;
    """, database)

    # Fail the audit insertion after assignment and approval inserts.
    admin("""
        CREATE FUNCTION scheduling.reject_assignment_audit_test()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.action='assignment.saved' THEN
                RAISE EXCEPTION 'Synthetic assignment audit failure';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER reject_assignment_audit_test
        BEFORE INSERT ON scheduling.audit_event
        FOR EACH ROW EXECUTE FUNCTION scheduling.reject_assignment_audit_test();
    """, database)

    admin(f"""
        BEGIN;
        SET LOCAL ROLE shift_scheduling_owner;
        SELECT set_config('scheduling.session_sha256',{literal(digest)},true);
        INSERT INTO scheduling.employee_qualification(
            organization_id,location_id,employee_id,job_role_id,active
        )
        SELECT organization_id,location_id,id,{literal(role)},true
        FROM scheduling.employee WHERE id={literal(employee)};

        DO $body$
        DECLARE rejected boolean := false; audit_before bigint;
        BEGIN
            SELECT count(*) INTO audit_before FROM scheduling.audit_event;
            BEGIN
                PERFORM scheduling.record_assignment_internal(
                    {literal(slot)},{literal(employee)},1,1,1,NULL,0,false,
                    '{{"category":"override_required","hard_blocks":[],
                       "warnings":["daily_hours"]}}'::jsonb,
                    '{{"daily_hours":"Synthetic approved coverage"}}'::jsonb
                );
            EXCEPTION WHEN raise_exception THEN
                IF SQLERRM<>'Synthetic assignment audit failure' THEN
                    RAISE;
                END IF;
                rejected := true;
            END;
            IF NOT rejected
               OR EXISTS (SELECT FROM scheduling.shift_assignment)
               OR EXISTS (SELECT FROM scheduling.assignment_override)
               OR (SELECT count(*) FROM scheduling.audit_event)<>audit_before
               OR (SELECT revision FROM scheduling.schedule_week
                   WHERE id={literal(week)})<>1
               OR (SELECT revision FROM scheduling.shift_slot
                   WHERE id={literal(slot)})<>1 THEN
                RAISE EXCEPTION 'Assignment audit failure retained partial state';
            END IF;
        END
        $body$;
        ROLLBACK;
    """, database)

    require(admin("""
        SELECT NOT p.prosecdef
          AND pg_get_userbyid(p.proowner)='shift_scheduling_owner'
          AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
          AND NOT has_function_privilege(
              'shift_scheduling_app',p.oid,'EXECUTE')
          AND NOT EXISTS (
              SELECT FROM aclexplode(
                  coalesce(p.proacl,acldefault('f',p.proowner))
              ) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE'
          )
        FROM pg_proc p WHERE p.oid=
          'scheduling.record_assignment_internal(uuid,uuid,integer,integer,integer,uuid,integer,boolean,jsonb,jsonb)'::regprocedure;
    """, database) == "t", "Internal recorder is exposed to runtime execution.")

    print("ASSIGNMENT_APPROVAL_AND_REVISION_PERSISTENCE=PASS")
    print("AUDIT_FAILURE_ROLLS_BACK_ASSIGNMENT_APPROVAL_AND_REVISIONS=PASS")
    print("INTERNAL_RECORDER_RUNTIME_EXECUTION=DENIED")
    print("SYNTHETIC_ASSIGNMENTS=ROLLED_BACK")

    print("SHIFT_REMOVAL_RETIRES_ASSIGNMENT_AND_PRESERVES_APPROVALS=PASS")
    print("RETIREMENT_AUDIT_FAILURE_FULL_ROLLBACK=PASS")
    print("ASSIGNED_SHIFT_TIMING_WITHOUT_REVALIDATION=DENIED")
