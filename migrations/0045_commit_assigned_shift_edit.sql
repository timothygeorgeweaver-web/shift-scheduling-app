DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

-- Trusted application bridge: decisions come from server-side evaluation.
-- The fingerprint binds the decision to the locked proposed inputs.
CREATE FUNCTION scheduling.commit_assigned_shift_edit(
    target_slot uuid,
    target_employee uuid,
    expected_week_revision integer,
    expected_slot_revision integer,
    expected_location_revision integer,
    expected_assignment uuid,
    expected_assignment_revision integer,
    target_role uuid,
    proposed_start timestamptz,
    proposed_end timestamptz,
    proposed_note text,
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
    week_value uuid;
    result jsonb;
BEGIN
    inputs := scheduling.read_assigned_shift_proposal(
        target_slot,target_employee,expected_week_revision,
        expected_slot_revision,expected_location_revision,
        target_role,proposed_start,proposed_end
    );
    IF expected_fingerprint IS NULL
       OR expected_fingerprint IS DISTINCT FROM md5(inputs::text)
       OR expected_assignment IS DISTINCT FROM
          (inputs->'current_assignment'->>'id')::uuid
       OR expected_assignment_revision IS DISTINCT FROM
          (inputs->'current_assignment'->>'revision')::integer THEN
        RAISE EXCEPTION 'Assigned shift inputs changed' USING ERRCODE='40001';
    END IF;
    IF expected_week_revision>2147483644
       OR expected_slot_revision>2147483644 THEN
        RAISE EXCEPTION 'Schedule revision exhausted' USING ERRCODE='40001';
    END IF;
    IF decision->>'employee_id' IS DISTINCT FROM target_employee::text
       OR decision->>'slot_id' IS DISTINCT FROM target_slot::text THEN
        RAISE EXCEPTION 'Invalid server decision' USING ERRCODE='22023';
    END IF;
    week_value := (inputs->'week'->>'id')::uuid;

    -- Retire the prior assignment while retaining its immutable approvals.
    -- All three operations are in this transaction; no intermediate state
    -- is committed or exposed to other transactions.
    PERFORM scheduling.unassign_draft_shift(
        target_slot,expected_assignment,expected_assignment_revision,
        expected_week_revision,expected_slot_revision,expected_location_revision
    );
    PERFORM scheduling.change_draft_shift(
        week_value,target_slot,expected_slot_revision+1,false,target_role,
        expected_week_revision+1,proposed_start,proposed_end,proposed_note
    );
    result := scheduling.record_assignment_internal(
        target_slot,target_employee,expected_week_revision+2,
        expected_slot_revision+2,expected_location_revision,
        NULL,0,false,decision,reasons
    );
    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION scheduling.commit_assigned_shift_edit(
    uuid,uuid,integer,integer,integer,uuid,integer,uuid,
    timestamptz,timestamptz,text,text,jsonb,jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.commit_assigned_shift_edit(
    uuid,uuid,integer,integer,integer,uuid,integer,uuid,
    timestamptz,timestamptz,text,text,jsonb,jsonb
) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0045_commit_assigned_shift_edit', :'migration_sha256');
