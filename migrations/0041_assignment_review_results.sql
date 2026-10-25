DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.store_assignment_review(
    target_assignment uuid,
    expected_fingerprint text,
    report jsonb
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    a scheduling.shift_assignment%ROWTYPE;
    inputs jsonb;
    latest scheduling.assignment_review_event%ROWTYPE;
    status_value text;
    details_value jsonb;
    event_value bigint;
    result jsonb;
BEGIN
    SELECT * INTO a FROM scheduling.shift_assignment
    WHERE id=target_assignment AND active;
    IF a.id IS NULL THEN
        RAISE EXCEPTION 'Assignment unavailable' USING ERRCODE='42501';
    END IF;

    inputs := scheduling.read_assignment_inputs(a.shift_slot_id,a.employee_id);
    inputs := scheduling.lock_assignment_scope(
        a.shift_slot_id,a.employee_id,
        (inputs->'week'->>'revision')::integer,
        (inputs->'slot'->>'revision')::integer,
        (inputs->'rest_policy'->>'location_revision')::integer
    );
    IF expected_fingerprint IS NULL
       OR expected_fingerprint IS DISTINCT FROM md5(inputs::text)
       OR (inputs->'current_assignment'->>'id')::uuid
          IS DISTINCT FROM target_assignment THEN
        RAISE EXCEPTION 'Review inputs changed' USING ERRCODE='40001';
    END IF;

    IF report->>'assignment_id' IS DISTINCT FROM a.id::text
       OR report->>'employee_id' IS DISTINCT FROM a.employee_id::text
       OR report->>'slot_id' IS DISTINCT FROM a.shift_slot_id::text
       OR (report->>'assignment_revision')::integer
          IS DISTINCT FROM (inputs->'current_assignment'->>'revision')::integer
       OR jsonb_typeof(report->'hard_blocks') IS DISTINCT FROM 'array'
       OR jsonb_typeof(report->'warnings') IS DISTINCT FROM 'array'
       OR jsonb_typeof(report->'review_reasons') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Invalid server review report' USING ERRCODE='22023';
    END IF;

    status_value := CASE
        WHEN jsonb_array_length(report->'hard_blocks')
           + jsonb_array_length(report->'warnings')
           + jsonb_array_length(report->'review_reasons')>0
        THEN 'needs_review' ELSE 'clear' END;
    details_value := jsonb_build_object(
        'input_fingerprint',expected_fingerprint,'report',report
    );

    SELECT * INTO latest FROM scheduling.assignment_review_event
    WHERE assignment_id=a.id
      AND assignment_revision=(inputs->'current_assignment'->>'revision')::integer
    ORDER BY id DESC LIMIT 1;

    IF latest.status=status_value AND latest.details=details_value THEN
        RETURN jsonb_build_object(
            'assignment_id',a.id,'status',status_value,'event_id',latest.id
        );
    END IF;

    INSERT INTO scheduling.assignment_review_event(
        organization_id,location_id,assignment_id,assignment_revision,
        cause,status,details,actor_user_id
    ) VALUES (
        a.organization_id,a.location_id,a.id,
        (inputs->'current_assignment'->>'revision')::integer,
        'manual_recheck',status_value,details_value,
        (inputs->>'actor_id')::uuid
    ) RETURNING id INTO event_value;

    result := jsonb_build_object(
        'assignment_id',a.id,'status',status_value,'event_id',event_value,
        'hard_blocks',report->'hard_blocks',
        'warnings',report->'warnings',
        'review_reasons',report->'review_reasons'
    );
    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        a.organization_id,a.location_id,(inputs->>'actor_id')::uuid,
        'assignment.reviewed','employee',a.employee_id,NULL,result
    );
    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION scheduling.store_assignment_review(uuid,text,jsonb)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.store_assignment_review(uuid,text,jsonb)
TO shift_scheduling_app;

CREATE OR REPLACE FUNCTION scheduling.read_schedule_assignments(target_week uuid)
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
            'display_name',e.display_name,'employee_active',e.active,
            'review_status',coalesce(review.status,'clear'),
            'review_hard_blocks',coalesce(
                review.details->'report'->'hard_blocks','[]'::jsonb),
            'review_warnings',coalesce(
                review.details->'report'->'warnings','[]'::jsonb),
            'review_reasons',coalesce(
                review.details->'report'->'review_reasons','[]'::jsonb)
        ) ORDER BY s.start_at,s.id)
        FROM scheduling.shift_assignment a
        JOIN scheduling.shift_slot s ON s.id=a.shift_slot_id
        JOIN scheduling.employee e
          ON e.id=a.employee_id
         AND e.organization_id=a.organization_id
         AND e.location_id=a.location_id
        LEFT JOIN LATERAL (
            SELECT v.status,v.details
            FROM scheduling.assignment_review_event v
            WHERE v.assignment_id=a.id AND v.assignment_revision=a.revision
            ORDER BY v.id DESC LIMIT 1
        ) review ON true
        WHERE s.schedule_week_id=target_week AND s.active AND a.active
    ),'[]'::jsonb);
END
$$;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0041_assignment_review_results', :'migration_sha256');
