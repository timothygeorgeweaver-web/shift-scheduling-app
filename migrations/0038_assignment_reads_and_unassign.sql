DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.read_schedule_assignments(target_week uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
BEGIN
    -- Reuse existing staff authorization and deleted-week protection.
    PERFORM scheduling.read_schedule_week(target_week);
    RETURN coalesce((
        SELECT jsonb_agg(jsonb_build_object(
            'id',a.id,'revision',a.revision,
            'slot_id',s.id,'employee_id',e.id,
            'display_name',e.display_name,'employee_active',e.active
        ) ORDER BY s.start_at,s.id)
        FROM scheduling.shift_assignment a
        JOIN scheduling.shift_slot s ON s.id=a.shift_slot_id
        JOIN scheduling.employee e
          ON e.id=a.employee_id
         AND e.organization_id=a.organization_id
         AND e.location_id=a.location_id
        WHERE s.schedule_week_id=target_week AND s.active AND a.active
    ),'[]'::jsonb);
END
$$;

CREATE FUNCTION scheduling.unassign_draft_shift(
    target_slot uuid,
    expected_assignment uuid,
    expected_assignment_revision integer,
    expected_week_revision integer,
    expected_slot_revision integer,
    expected_location_revision integer
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    employee_value uuid;
    inputs jsonb;
    previous scheduling.shift_assignment%ROWTYPE;
    actor_value uuid;
    result jsonb;
BEGIN
    SELECT employee_id INTO employee_value
    FROM scheduling.shift_assignment
    WHERE id=expected_assignment AND shift_slot_id=target_slot AND active;
    IF employee_value IS NULL THEN
        RAISE EXCEPTION 'Assignment unavailable' USING ERRCODE='42501';
    END IF;

    inputs := scheduling.lock_assignment_scope(
        target_slot,employee_value,expected_week_revision,
        expected_slot_revision,expected_location_revision
    );
    SELECT * INTO previous FROM scheduling.shift_assignment
    WHERE id=expected_assignment AND shift_slot_id=target_slot AND active
    FOR UPDATE;
    IF previous.id IS NULL
       OR expected_assignment_revision IS NULL
       OR previous.revision<>expected_assignment_revision
       OR previous.revision=2147483647 THEN
        RAISE EXCEPTION 'Assignment changed' USING ERRCODE='40001';
    END IF;
    IF expected_week_revision=2147483647
       OR expected_slot_revision=2147483647 THEN
        RAISE EXCEPTION 'Schedule revision exhausted' USING ERRCODE='40001';
    END IF;

    actor_value := (inputs->>'actor_id')::uuid;
    UPDATE scheduling.shift_assignment
    SET active=false,revision=revision+1,
        updated_by=actor_value,updated_at=clock_timestamp()
    WHERE id=previous.id;
    UPDATE scheduling.shift_slot SET revision=revision+1 WHERE id=target_slot;
    UPDATE scheduling.schedule_week SET revision=revision+1
    WHERE id=(inputs->'week'->>'id')::uuid;

    result := jsonb_build_object(
        'assignment_id',previous.id,
        'assignment_revision',previous.revision+1,
        'slot_id',target_slot,'employee_id',employee_value,
        'active',false,'slot_revision',expected_slot_revision+1,
        'week_revision',expected_week_revision+1
    );
    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        previous.organization_id,previous.location_id,actor_value,
        'assignment.unassigned','employee',employee_value,
        jsonb_build_object(
            'assignment_id',previous.id,'assignment_revision',previous.revision,
            'slot_id',target_slot,'employee_id',employee_value,'active',true
        ),result
    );

    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session
        WHERE token_sha256=current_setting('scheduling.session_sha256',true)
          AND revoked_at IS NULL AND expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;
    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION scheduling.read_schedule_assignments(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.read_schedule_assignments(uuid)
TO shift_scheduling_app;
REVOKE ALL ON FUNCTION scheduling.unassign_draft_shift(
    uuid,uuid,integer,integer,integer,integer
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.unassign_draft_shift(
    uuid,uuid,integer,integer,integer,integer
) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0038_assignment_reads_and_unassign', :'migration_sha256');
