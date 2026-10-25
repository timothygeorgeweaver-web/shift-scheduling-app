DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.lock_publication_scope(
    target_location uuid,
    target_week uuid,
    expected_week_revision integer,
    expected_location_revision integer,
    expected_publication_number integer
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    working jsonb;
    assignment_row scheduling.shift_assignment%ROWTYPE;
    inputs jsonb;
    checks jsonb := '[]'::jsonb;
    approvals jsonb;
    role_orders jsonb;
    inactive_slots jsonb;
    publication_value integer;
    slot_revision_value integer;
BEGIN
    working := scheduling.lock_template_destination(
        target_location,target_week,
        expected_week_revision,expected_location_revision
    );

    SELECT coalesce(max(publication_number),0) INTO publication_value
    FROM scheduling.schedule_publication WHERE schedule_week_id=target_week;
    IF expected_publication_number IS NULL
       OR expected_publication_number<>publication_value THEN
        RAISE EXCEPTION 'Publication changed' USING ERRCODE='40001';
    END IF;

    PERFORM r.id
    FROM scheduling.shift_slot s
    JOIN scheduling.job_role r ON r.id=s.role_id
    WHERE s.schedule_week_id=target_week AND s.active
    ORDER BY r.id,s.id FOR SHARE OF r,s;

    FOR assignment_row IN
        SELECT a.* FROM scheduling.shift_assignment a
        JOIN scheduling.shift_slot s ON s.id=a.shift_slot_id
        WHERE s.schedule_week_id=target_week AND s.active AND a.active
        ORDER BY s.id FOR UPDATE OF a
    LOOP
        SELECT revision INTO STRICT slot_revision_value
        FROM scheduling.shift_slot WHERE id=assignment_row.shift_slot_id;

        inputs := scheduling.lock_assignment_scope(
            assignment_row.shift_slot_id,assignment_row.employee_id,expected_week_revision,
            slot_revision_value,expected_location_revision
        );
        checks := checks || jsonb_build_array(inputs);
    END LOOP;

    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'assignment_id',o.assignment_id,
        'assignment_revision',o.assignment_revision,
        'employee_id',o.employee_id,
        'rule_code',o.rule_code,
        'reason',o.reason,
        'policy',o.policy,
        'rule_details',o.rule_details,
        'changed_since_approval',(
            o.rule_code='unavailability' AND EXISTS (
                SELECT FROM scheduling.assignment_review_event v
                WHERE v.assignment_id=a.id
                  AND v.assignment_revision=a.revision
                  AND v.cause='availability'
                  AND v.created_at>=o.approved_at
            )
        )
    ) ORDER BY o.assignment_id,o.rule_code),'[]'::jsonb)
    INTO approvals
    FROM scheduling.assignment_override o
    JOIN scheduling.shift_assignment a
      ON a.id=o.assignment_id AND a.revision=o.assignment_revision
    JOIN scheduling.shift_slot s ON s.id=a.shift_slot_id
    WHERE s.schedule_week_id=target_week AND s.active AND a.active;

    -- Daily-hours freshness is checked against exact recomputed daily totals.
    -- Replacing a neighboring assignment with identical hours must not cause
    -- a cycle where renewing one approval invalidates the other.
    SELECT coalesce(jsonb_object_agg(r.id::text,r.display_order),'{}'::jsonb)
    INTO role_orders
    FROM scheduling.job_role r WHERE r.location_id=target_location;

    SELECT coalesce(jsonb_agg(s.id ORDER BY s.id),'[]'::jsonb)
    INTO inactive_slots
    FROM scheduling.shift_slot s
    JOIN scheduling.job_role r ON r.id=s.role_id
    WHERE s.schedule_week_id=target_week AND s.active AND NOT r.active;

    RETURN working || jsonb_build_object(
        'assignments',scheduling.read_schedule_assignments(target_week),
        'assignment_inputs',checks,
        'approvals',approvals,
        'role_orders',role_orders,
        'inactive_role_slot_ids',inactive_slots,
        'location_revision',expected_location_revision,
        'publication_number',publication_value
    );
END
$$;

REVOKE ALL ON FUNCTION scheduling.lock_publication_scope(
    uuid,uuid,integer,integer,integer
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.lock_publication_scope(
    uuid,uuid,integer,integer,integer
) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0048_publication_review_scope', :'migration_sha256');
