DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

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
            'review_status',coalesce(review.status,
                CASE WHEN EXISTS (
                    SELECT FROM scheduling.assignment_override approved
                    WHERE approved.assignment_id=a.id
                      AND approved.assignment_revision=a.revision
                ) THEN 'pending' ELSE 'clear' END),
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
VALUES ('0046_assignment_warning_visibility', :'migration_sha256');
