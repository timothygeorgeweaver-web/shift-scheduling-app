DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

-- The trusted application service computes eligibility. HTTP clients cannot
-- submit decision/fingerprint fields. The fingerprint binds that evaluation
-- to the current authorized inputs; it is not a cryptographic authorization
-- token and does not replace server-side rule evaluation.
CREATE FUNCTION scheduling.commit_assignment_evaluated(
    target_slot uuid,
    target_employee uuid,
    expected_week_revision integer,
    expected_slot_revision integer,
    expected_location_revision integer,
    expected_assignment uuid,
    expected_assignment_revision integer,
    replacement_confirmed boolean,
    expected_fingerprint text,
    decision jsonb,
    reasons jsonb
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    inputs jsonb;
BEGIN
    inputs := scheduling.lock_assignment_scope(
        target_slot,target_employee,expected_week_revision,
        expected_slot_revision,expected_location_revision
    );
    IF expected_fingerprint IS NULL
       OR expected_fingerprint IS DISTINCT FROM md5(inputs::text) THEN
        RAISE EXCEPTION 'Eligibility inputs changed' USING ERRCODE='40001';
    END IF;
    IF decision->>'employee_id' IS DISTINCT FROM target_employee::text
       OR decision->>'slot_id' IS DISTINCT FROM target_slot::text THEN
        RAISE EXCEPTION 'Invalid server assignment decision'
            USING ERRCODE='22023';
    END IF;

    RETURN scheduling.record_assignment_internal(
        target_slot,target_employee,expected_week_revision,
        expected_slot_revision,expected_location_revision,
        expected_assignment,expected_assignment_revision,
        replacement_confirmed,decision,reasons
    );
END
$$;

REVOKE ALL ON FUNCTION scheduling.commit_assignment_evaluated(
    uuid,uuid,integer,integer,integer,uuid,integer,boolean,text,jsonb,jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.commit_assignment_evaluated(
    uuid,uuid,integer,integer,integer,uuid,integer,boolean,text,jsonb,jsonb
) TO shift_scheduling_app;

-- The internal recorder and all assignment tables remain inaccessible
-- directly to the application role.
INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0037_assignment_commit_bridge', :'migration_sha256');
