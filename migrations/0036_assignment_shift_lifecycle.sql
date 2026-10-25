DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.guard_assigned_shift_change()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
DECLARE
    a scheduling.shift_assignment%ROWTYPE;
    actor_value uuid;
BEGIN
    IF NEW.active
       AND (NEW.role_id IS DISTINCT FROM OLD.role_id
            OR NEW.start_at IS DISTINCT FROM OLD.start_at
            OR NEW.end_at IS DISTINCT FROM OLD.end_at)
       AND EXISTS (
           SELECT FROM scheduling.shift_assignment
           WHERE shift_slot_id=OLD.id AND active
       ) THEN
        RAISE EXCEPTION 'Assigned shift changes require eligibility revalidation'
            USING ERRCODE='55000';
    END IF;

    IF NOT NEW.active THEN
        FOR a IN
            SELECT * FROM scheduling.shift_assignment
            WHERE shift_slot_id=OLD.id AND active
            ORDER BY id FOR UPDATE
        LOOP
            SELECT u.id INTO actor_value
            FROM scheduling.auth_session s
            JOIN scheduling.app_user u ON u.id=s.user_id
            JOIN scheduling.organization_member m
              ON m.user_id=u.id AND m.organization_id=OLD.organization_id
            JOIN scheduling.member_permission p
              ON p.organization_id=m.organization_id AND p.user_id=m.user_id
            WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
              AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
              AND u.active AND m.active
              AND p.permission IN ('administrator','scheduler')
            LIMIT 1;
            IF actor_value IS NULL THEN
                RAISE EXCEPTION 'Scheduler access required'
                    USING ERRCODE='42501';
            END IF;

            UPDATE scheduling.shift_assignment
            SET active=false,revision=revision+1,
                updated_by=actor_value,updated_at=clock_timestamp()
            WHERE id=a.id;

            INSERT INTO scheduling.audit_event(
                organization_id,location_id,actor_user_id,action,
                entity_type,entity_id,before_data,after_data
            ) VALUES (
                a.organization_id,a.location_id,actor_value,
                'assignment.retired','employee',a.employee_id,
                jsonb_build_object(
                    'assignment_id',a.id,'assignment_revision',a.revision,
                    'slot_id',OLD.id,'employee_id',a.employee_id
                ),
                jsonb_build_object(
                    'assignment_id',a.id,'assignment_revision',a.revision+1,
                    'slot_id',OLD.id,'employee_id',a.employee_id,
                    'active',false,'reason','shift_removed'
                )
            );
        END LOOP;
    END IF;
    RETURN NEW;
END
$$;

REVOKE ALL ON FUNCTION scheduling.guard_assigned_shift_change()
FROM PUBLIC,shift_scheduling_app;

CREATE TRIGGER assigned_shift_lifecycle
BEFORE UPDATE OF active,role_id,start_at,end_at ON scheduling.shift_slot
FOR EACH ROW EXECUTE FUNCTION scheduling.guard_assigned_shift_change();

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0036_assignment_shift_lifecycle', :'migration_sha256');
