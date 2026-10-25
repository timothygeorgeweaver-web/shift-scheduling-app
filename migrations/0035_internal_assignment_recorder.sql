DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

-- Internal persistence primitive, deliberately NOT SECURITY DEFINER.
-- Do not grant runtime execution: eligibility decisions must come from
-- the future trusted commit service after fresh rule evaluation.
CREATE FUNCTION scheduling.record_assignment_internal(
    target_slot uuid,
    target_employee uuid,
    expected_week_revision integer,
    expected_slot_revision integer,
    expected_location_revision integer,
    expected_assignment uuid,
    expected_assignment_revision integer,
    replacement_confirmed boolean,
    decision jsonb,
    reasons jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
DECLARE
    inputs jsonb;
    current_value jsonb;
    organization_value uuid;
    location_value uuid;
    actor_value uuid;
    saved scheduling.shift_assignment%ROWTYPE;
    warning_value text;
    reason_value text;
    warnings jsonb;
    result jsonb;
BEGIN
    IF current_user<>'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Internal assignment recorder' USING ERRCODE='42501';
    END IF;

    inputs := scheduling.lock_assignment_scope(
        target_slot,target_employee,expected_week_revision,
        expected_slot_revision,expected_location_revision
    );
    current_value := inputs->'current_assignment';

    IF expected_assignment_revision IS NULL
       OR expected_assignment IS DISTINCT FROM
          (current_value->>'id')::uuid
       OR expected_assignment_revision<>
          coalesce((current_value->>'revision')::integer,0) THEN
        RAISE EXCEPTION 'Assignment changed' USING ERRCODE='40001';
    END IF;

    IF NOT (inputs->'employee'->>'active')::boolean
       OR NOT (inputs->>'role_active')::boolean
       OR NOT (inputs->>'qualified')::boolean THEN
        RAISE EXCEPTION 'Active qualification required' USING ERRCODE='22023';
    END IF;
    IF (current_value->>'employee_id')::uuid IS DISTINCT FROM target_employee
       AND current_value->>'id' IS NOT NULL
       AND replacement_confirmed IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Confirm assignment replacement' USING ERRCODE='22023';
    END IF;

    IF jsonb_typeof(decision) IS DISTINCT FROM 'object'
       OR decision->'hard_blocks' IS DISTINCT FROM '[]'::jsonb
       OR jsonb_typeof(decision->'warnings') IS DISTINCT FROM 'array'
       OR jsonb_typeof(reasons) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'Invalid validated assignment decision'
            USING ERRCODE='22023';
    END IF;
    warnings := decision->'warnings';
    IF jsonb_array_length(warnings)>2
       OR EXISTS (
           SELECT FROM jsonb_array_elements(warnings) x
           WHERE jsonb_typeof(x)<>'string'
              OR x#>>'{}' NOT IN ('unavailability','daily_hours')
       )
       OR (SELECT count(DISTINCT x)
           FROM jsonb_array_elements_text(warnings) x)
          <>jsonb_array_length(warnings)
       OR (SELECT count(*) FROM jsonb_object_keys(reasons))
          <>jsonb_array_length(warnings)
       OR decision->>'category' IS DISTINCT FROM
          (CASE WHEN jsonb_array_length(warnings)=0
                THEN 'eligible' ELSE 'override_required' END) THEN
        RAISE EXCEPTION 'Invalid approval set' USING ERRCODE='22023';
    END IF;

    FOR warning_value IN SELECT jsonb_array_elements_text(warnings)
    LOOP
        reason_value := reasons->>warning_value;
        IF jsonb_typeof(reasons->warning_value) IS DISTINCT FROM 'string'
           OR reason_value IS NULL OR length(btrim(reason_value)) NOT BETWEEN 1 AND 500
           OR reason_value ~ '^[[:space:]]*$' THEN
            RAISE EXCEPTION 'A reason is required for each warning'
                USING ERRCODE='22023';
        END IF;
    END LOOP;

    location_value := (inputs->'week'->>'location_id')::uuid;
    actor_value := (inputs->>'actor_id')::uuid;
    SELECT organization_id INTO STRICT organization_value
    FROM scheduling.location WHERE id=location_value;

    IF expected_assignment IS NOT NULL THEN
        UPDATE scheduling.shift_assignment
        SET active=false,revision=revision+1,
            updated_by=actor_value,updated_at=clock_timestamp()
        WHERE id=expected_assignment;
    END IF;

    INSERT INTO scheduling.shift_assignment(
        organization_id,location_id,shift_slot_id,employee_id,
        created_by,updated_by
    ) VALUES (
        organization_value,location_value,target_slot,target_employee,
        actor_value,actor_value
    ) RETURNING * INTO saved;

    FOR warning_value IN SELECT jsonb_array_elements_text(warnings)
    LOOP
        INSERT INTO scheduling.assignment_override(
            organization_id,location_id,assignment_id,employee_id,
            assignment_revision,rule_code,reason,policy,rule_details,approved_by
        ) VALUES (
            organization_value,location_value,saved.id,target_employee,
            saved.revision,warning_value,btrim(reasons->>warning_value),
            jsonb_build_object(
                'rest',inputs->'rest_policy',
                'timezone',inputs->'week'->>'timezone',
                'business_day_start_minute',
                    inputs->'week'->'business_day_start_minute'
            ),
            jsonb_build_object(
                'rule_code',warning_value,
                'daily_work',coalesce(decision->'daily_work','[]'::jsonb),
                'slot_revision',expected_slot_revision,
                'week_revision',expected_week_revision
            ),actor_value
        );
    END LOOP;

    UPDATE scheduling.shift_slot SET revision=revision+1
    WHERE id=target_slot;
    UPDATE scheduling.schedule_week SET revision=revision+1
    WHERE id=(inputs->'week'->>'id')::uuid;

    result := jsonb_build_object(
        'assignment_id',saved.id,'assignment_revision',saved.revision,
        'employee_id',target_employee,'slot_id',target_slot,
        'slot_revision',expected_slot_revision+1,
        'week_revision',expected_week_revision+1,
        'approved_rules',warnings
    );

    -- Reasons stay in the private immutable approval table.
    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_value,location_value,actor_value,'assignment.saved',
        'employee',target_employee,current_value,result
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

REVOKE ALL ON FUNCTION scheduling.record_assignment_internal(
    uuid,uuid,integer,integer,integer,uuid,integer,boolean,jsonb,jsonb
) FROM PUBLIC,shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0035_internal_assignment_recorder', :'migration_sha256');
