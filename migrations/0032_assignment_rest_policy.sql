DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

ALTER TABLE scheduling.location
ADD COLUMN rest_window_start_minute integer NOT NULL DEFAULT 1200
    CHECK (rest_window_start_minute BETWEEN 0 AND 1439
           AND rest_window_start_minute % 15=0),
ADD COLUMN rest_window_end_minute integer NOT NULL DEFAULT 360
    CHECK (rest_window_end_minute BETWEEN 0 AND 1439
           AND rest_window_end_minute % 15=0),
ADD COLUMN minimum_rest_minutes integer NOT NULL DEFAULT 720
    CHECK (minimum_rest_minutes BETWEEN 0 AND 1440
           AND minimum_rest_minutes % 15=0);

-- Existing shift instants, assignment records and saved week boundaries
-- remain unchanged. No direct runtime column-write grants are added.

CREATE OR REPLACE FUNCTION scheduling.read_assignment_inputs(
    target_slot uuid,
    target_employee uuid
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    s scheduling.shift_slot%ROWTYPE;
    w scheduling.schedule_week%ROWTYPE;
    e scheduling.employee%ROWTYPE;
    actor_value uuid;
    first_day date;
    last_day date;
    result jsonb;
BEGIN
    SELECT * INTO s FROM scheduling.shift_slot WHERE id=target_slot;
    IF s.id IS NULL OR NOT s.active THEN
        RAISE EXCEPTION 'Assignment inputs unavailable' USING ERRCODE='42501';
    END IF;

    SELECT u.id INTO actor_value
    FROM scheduling.auth_session a
    JOIN scheduling.app_user u ON u.id=a.user_id
    JOIN scheduling.organization_member m
      ON m.user_id=u.id AND m.organization_id=s.organization_id
    JOIN scheduling.member_permission p
      ON p.organization_id=m.organization_id AND p.user_id=m.user_id
    WHERE a.token_sha256=current_setting('scheduling.session_sha256',true)
      AND a.revoked_at IS NULL AND a.expires_at>clock_timestamp()
      AND u.active AND m.active
      AND p.permission IN ('administrator','scheduler')
    LIMIT 1;

    IF actor_value IS NULL THEN
        RAISE EXCEPTION 'Assignment inputs unavailable' USING ERRCODE='42501';
    END IF;

    SELECT * INTO w FROM scheduling.schedule_week
    WHERE id=s.schedule_week_id AND deleted_at IS NULL;
    IF w.id IS NULL OR w.state<>'draft' THEN
        RAISE EXCEPTION 'Draft schedule required' USING ERRCODE='55000';
    END IF;

    SELECT * INTO e FROM scheduling.employee
    WHERE id=target_employee
      AND organization_id=s.organization_id AND location_id=s.location_id;
    IF e.id IS NULL THEN
        RAISE EXCEPTION 'Assignment inputs unavailable' USING ERRCODE='42501';
    END IF;

    first_day := (s.start_at AT TIME ZONE w.timezone)::date;
    last_day := ((s.end_at-interval '1 microsecond')
                 AT TIME ZONE w.timezone)::date;

    SELECT jsonb_build_object(
        'actor_id',actor_value,
        'rest_policy',(
            SELECT jsonb_build_object(
                'window_start',l.rest_window_start_minute,
                'window_end',l.rest_window_end_minute,
                'rest_minutes',l.minimum_rest_minutes,
                'location_revision',l.revision
            )
            FROM scheduling.location l WHERE l.id=s.location_id
        ),
        'week',jsonb_build_object(
            'id',w.id,'revision',w.revision,
            'location_id',w.location_id,'timezone',w.timezone,
            'business_day_start_minute',w.business_day_start_minute
        ),
        'slot',jsonb_build_object(
            'id',s.id,'revision',s.revision,
            'role_id',s.role_id,'start_at',s.start_at,'end_at',s.end_at
        ),
        'employee',jsonb_build_object(
            'id',e.id,'display_name',e.display_name,
            'active',e.active,'revision',e.revision
        ),
        'role_active',r.active,
        'qualified',EXISTS (
            SELECT FROM scheduling.employee_qualification q
            WHERE q.organization_id=s.organization_id
              AND q.location_id=s.location_id
              AND q.employee_id=e.id AND q.job_role_id=s.role_id
              AND q.active
        ),
        'current_assignment',(
            SELECT jsonb_build_object(
                'id',a.id,'employee_id',a.employee_id,'revision',a.revision
            )
            FROM scheduling.shift_assignment a
            WHERE a.shift_slot_id=s.id AND a.active
        ),
        'assigned',coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'assignment_id',a.id,'assignment_revision',a.revision,
                'slot_id',other.id,'slot_revision',other.revision,
                'start_at',other.start_at,'end_at',other.end_at,
                'week_id',ow.id,'week_revision',ow.revision,
                'timezone',ow.timezone,
                'business_day_start_minute',ow.business_day_start_minute
            ) ORDER BY other.start_at,other.id)
            FROM scheduling.shift_assignment a
            JOIN scheduling.shift_slot other
              ON other.id=a.shift_slot_id
             AND other.organization_id=a.organization_id
             AND other.location_id=a.location_id
            JOIN scheduling.schedule_week ow ON ow.id=other.schedule_week_id
            WHERE a.employee_id=e.id AND a.organization_id=s.organization_id
              AND a.active AND other.active AND ow.deleted_at IS NULL
              AND other.id<>s.id
        ),'[]'::jsonb),
        'calendar_days',(
            SELECT jsonb_agg(jsonb_build_object(
                'date',first_day+g.n,
                'revision',coalesce(d.revision,0),
                'blocked_cells',coalesce(d.blocked_cells,'[]'::jsonb),
                'frozen_blocked',k.blocked_windows,
                'frozen_reason',k.reason
            ) ORDER BY g.n)
            FROM generate_series(0,last_day-first_day) g(n)
            LEFT JOIN scheduling.unavailability_date d
              ON d.employee_id=e.id AND d.local_date=first_day+g.n
             AND d.organization_id=s.organization_id
             AND d.location_id=s.location_id
            LEFT JOIN scheduling.unavailability_day_lock k
              ON k.employee_id=e.id AND k.local_date=first_day+g.n
             AND k.organization_id=s.organization_id
             AND k.location_id=s.location_id
        )
    ) INTO result
    FROM scheduling.job_role r
    WHERE r.id=s.role_id AND r.organization_id=s.organization_id
      AND r.location_id=s.location_id;

    IF result IS NULL THEN
        RAISE EXCEPTION 'Assignment inputs unavailable' USING ERRCODE='42501';
    END IF;
    RETURN result;
END
$$;


REVOKE ALL ON FUNCTION scheduling.read_assignment_inputs(uuid,uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.read_assignment_inputs(uuid,uuid)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0032_assignment_rest_policy', :'migration_sha256');
