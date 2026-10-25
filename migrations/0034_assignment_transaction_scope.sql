DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.lock_assignment_scope(
    target_slot uuid,
    target_employee uuid,
    expected_week_revision integer,
    expected_slot_revision integer,
    expected_location_revision integer
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    inputs jsonb;
    organization_value uuid;
    location_value uuid;
    week_value uuid;
BEGIN
    -- Authorize before acquiring the scheduling transaction locks.
    inputs := scheduling.read_assignment_inputs(target_slot,target_employee);
    location_value := (inputs->'week'->>'location_id')::uuid;
    week_value := (inputs->'week'->>'id')::uuid;

    SELECT organization_id INTO STRICT organization_value
    FROM scheduling.location WHERE id=location_value;

    -- Shared with week, shift, availability and policy mutations.
    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    PERFORM 1 FROM scheduling.location
    WHERE id=location_value FOR SHARE;
    PERFORM 1 FROM scheduling.schedule_week
    WHERE id=week_value FOR UPDATE;
    PERFORM 1 FROM scheduling.shift_slot
    WHERE id=target_slot FOR UPDATE;

    -- Qualification writes already lock the employee row. Locking it also
    -- serializes future assignment saves for this employee across weeks.
    PERFORM 1 FROM scheduling.employee
    WHERE id=target_employee AND organization_id=organization_value
      AND location_id=location_value FOR UPDATE;

    PERFORM 1 FROM scheduling.job_role
    WHERE id=(inputs->'slot'->>'role_id')::uuid FOR SHARE;

    PERFORM 1
    FROM scheduling.auth_session s
    JOIN scheduling.app_user u ON u.id=s.user_id
    JOIN scheduling.organization_member m
      ON m.user_id=u.id AND m.organization_id=organization_value
    JOIN scheduling.member_permission p
      ON p.organization_id=m.organization_id AND p.user_id=m.user_id
    WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
      AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
      AND u.active AND m.active
      AND p.permission IN ('administrator','scheduler')
    FOR SHARE OF s,u,m,p;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Scheduler access required' USING ERRCODE='42501';
    END IF;

    -- Discard the pre-lock snapshot and obtain current eligibility inputs.
    inputs := scheduling.read_assignment_inputs(target_slot,target_employee);

    IF expected_week_revision IS NULL
       OR expected_slot_revision IS NULL
       OR expected_location_revision IS NULL
       OR expected_week_revision<>(inputs->'week'->>'revision')::integer
       OR expected_slot_revision<>(inputs->'slot'->>'revision')::integer
       OR expected_location_revision<>
          (inputs->'rest_policy'->>'location_revision')::integer THEN
        RAISE EXCEPTION 'Assignment inputs changed' USING ERRCODE='40001';
    END IF;
    RETURN inputs;
END
$$;

REVOKE ALL ON FUNCTION
scheduling.lock_assignment_scope(uuid,uuid,integer,integer,integer)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.lock_assignment_scope(uuid,uuid,integer,integer,integer)
TO shift_scheduling_app;

-- Locks last until the caller's transaction ends. This function performs
-- no assignment mutation and cannot substitute for eligibility validation.
INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0034_assignment_transaction_scope', :'migration_sha256');
