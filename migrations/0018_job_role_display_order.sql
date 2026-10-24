DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

ALTER TABLE scheduling.job_role
ADD COLUMN display_order integer NOT NULL DEFAULT 0
CHECK (display_order BETWEEN 0 AND 9999);

GRANT INSERT (display_order), UPDATE (display_order)
ON scheduling.job_role TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0018_job_role_display_order', :'migration_sha256');
