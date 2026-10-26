DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

ALTER FUNCTION scheduling.queue_assignment_review()
SECURITY DEFINER;

ALTER FUNCTION scheduling.queue_assignment_review()
SET search_path=pg_catalog;

REVOKE ALL ON FUNCTION scheduling.queue_assignment_review()
FROM PUBLIC,shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0054_assignment_review_trigger_security', :'migration_sha256');
