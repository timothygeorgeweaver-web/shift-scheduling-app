DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;

    IF EXISTS (SELECT FROM scheduling.unavailability_recurring)
       OR EXISTS (SELECT FROM scheduling.unavailability_exception) THEN
        RAISE EXCEPTION 'Legacy entries exist; preserve and inspect before retirement';
    END IF;
END
$$;

REVOKE ALL ON FUNCTION
scheduling.read_my_unavailability(uuid,date,integer)
FROM PUBLIC,shift_scheduling_app;

REVOKE ALL ON FUNCTION
scheduling.write_my_unavailability(uuid,text,date,integer,integer,jsonb)
FROM PUBLIC,shift_scheduling_app;

-- Keep the shared live employee-profile function used by the new planner.
-- Retain legacy tables, function definitions, frozen snapshots and audits.
DO $$
BEGIN
    IF has_function_privilege(
        'shift_scheduling_app',
        'scheduling.read_my_unavailability(uuid,date,integer)','EXECUTE'
    ) OR has_function_privilege(
        'shift_scheduling_app',
        'scheduling.write_my_unavailability(uuid,text,date,integer,integer,jsonb)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'Legacy function access remains available';
    END IF;
END
$$;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0014_retire_legacy_unavailability', :'migration_sha256');
