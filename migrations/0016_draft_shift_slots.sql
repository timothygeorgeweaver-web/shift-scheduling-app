DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.shift_slot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    schedule_week_id uuid NOT NULL,
    role_id uuid NOT NULL,
    start_at timestamptz NOT NULL,
    end_at timestamptz NOT NULL,
    note text NOT NULL DEFAULT '' CHECK (length(note)<=500),
    active boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    duration_minutes integer GENERATED ALWAYS AS (
        (extract(epoch FROM (end_at-start_at))/60)::integer
    ) STORED,
    meal_estimate_minutes integer GENERATED ALWAYS AS (
        CASE WHEN end_at-start_at>interval '6 hours' THEN 30 ELSE 0 END
    ) STORED,
    estimated_work_minutes integer GENERATED ALWAYS AS (
        (extract(epoch FROM (end_at-start_at))/60)::integer
        - CASE WHEN end_at-start_at>interval '6 hours' THEN 30 ELSE 0 END
    ) STORED,
    CHECK (
        isfinite(start_at) AND isfinite(end_at) AND end_at>start_at
        AND extract(epoch FROM (end_at-start_at)) % 60=0
    ),
    UNIQUE (organization_id,location_id,id),
    FOREIGN KEY (organization_id,location_id,schedule_week_id)
      REFERENCES scheduling.schedule_week(organization_id,location_id,id),
    FOREIGN KEY (organization_id,location_id,role_id)
      REFERENCES scheduling.job_role(organization_id,location_id,id),
    FOREIGN KEY (organization_id,created_by)
      REFERENCES scheduling.organization_member(organization_id,user_id)
);
CREATE INDEX shift_slot_week_time_idx
ON scheduling.shift_slot(schedule_week_id,start_at,id);
ALTER TABLE scheduling.shift_slot ENABLE ROW LEVEL SECURITY;
-- No runtime table grants or direct assignment/state-change access.

CREATE FUNCTION scheduling.read_schedule_week(target_week uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    w scheduling.schedule_week%ROWTYPE;
BEGIN
    SELECT * INTO w FROM scheduling.schedule_week WHERE id=target_week;
    IF w.id IS NULL OR NOT scheduling.staff_read_allowed(w.organization_id) THEN
        RAISE EXCEPTION 'Schedule unavailable' USING ERRCODE='42501';
    END IF;
    RETURN jsonb_build_object(
        'week',to_jsonb(w),
        'slots',coalesce((
            SELECT jsonb_agg(
                to_jsonb(s)||jsonb_build_object(
                    'role_name',r.name,
                    'local_start',s.start_at AT TIME ZONE w.timezone,
                    'local_end',s.end_at AT TIME ZONE w.timezone
                )
                ORDER BY s.start_at,s.role_id,s.id
            )
            FROM scheduling.shift_slot s
            JOIN scheduling.job_role r ON r.id=s.role_id
            WHERE s.schedule_week_id=w.id AND s.active
        ),'[]'::jsonb)
    );
END
$$;

CREATE FUNCTION scheduling.create_draft_shift(
    target_week uuid,
    target_role uuid,
    expected_week_revision integer,
    shift_start timestamptz,
    shift_end timestamptz,
    shift_note text DEFAULT ''
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    actor_value uuid;
    w scheduling.schedule_week%ROWTYPE;
    local_start timestamp;
    local_end timestamp;
    matches integer;
    endpoint timestamp;
    result jsonb;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.schedule_week WHERE id=target_week;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Schedule unavailable' USING ERRCODE='42501';
    END IF;

    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    SELECT * INTO STRICT w FROM scheduling.schedule_week
    WHERE id=target_week FOR UPDATE;

    SELECT u.id INTO actor_value
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

    IF actor_value IS NULL THEN
        RAISE EXCEPTION 'Scheduler access required' USING ERRCODE='42501';
    END IF;
    IF w.state<>'draft' THEN
        RAISE EXCEPTION 'Schedule is not a draft' USING ERRCODE='55000';
    END IF;
    IF expected_week_revision IS NULL
       OR expected_week_revision<>w.revision OR w.revision=2147483647 THEN
        RAISE EXCEPTION 'Schedule changed' USING ERRCODE='40001';
    END IF;

    PERFORM 1 FROM scheduling.job_role
    WHERE id=target_role AND organization_id=w.organization_id
      AND location_id=w.location_id AND active
    FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Active role unavailable' USING ERRCODE='22023';
    END IF;

    IF shift_start IS NULL OR shift_end IS NULL
       OR NOT isfinite(shift_start) OR NOT isfinite(shift_end)
       OR shift_end<=shift_start
       OR shift_note IS NULL OR length(shift_note)>500 THEN
        RAISE EXCEPTION 'Invalid shift' USING ERRCODE='22023';
    END IF;
    local_start := shift_start AT TIME ZONE w.timezone;
    local_end := shift_end AT TIME ZONE w.timezone;

    IF local_start::date NOT BETWEEN w.week_start_date AND w.week_start_date+6
       OR local_end::date NOT BETWEEN local_start::date AND local_start::date+1
       OR local_end<=local_start
       OR extract(second FROM local_start)<>0
       OR extract(second FROM local_end)<>0
       OR extract(epoch FROM (shift_end-shift_start)) % 60<>0 THEN
        RAISE EXCEPTION 'Invalid local shift dates or minutes'
            USING ERRCODE='22023';
    END IF;

    -- Independently reject ambiguous local endpoints, including direct
    -- database calls supplying one arbitrarily chosen UTC interpretation.
    FOREACH endpoint IN ARRAY ARRAY[local_start,local_end] LOOP
        SELECT count(*) INTO matches
        FROM (
            SELECT (endpoint AT TIME ZONE 'UTC')
                   + n*interval '1 minute' AS candidate
            FROM generate_series(-1560,1560) g(n)
        ) possible
        WHERE candidate AT TIME ZONE w.timezone=endpoint;
        IF matches<>1 THEN
            RAISE EXCEPTION 'Ambiguous or nonexistent local shift endpoint'
                USING ERRCODE='22023';
        END IF;
    END LOOP;

    INSERT INTO scheduling.shift_slot(
        organization_id,location_id,schedule_week_id,role_id,
        start_at,end_at,note,created_by
    ) VALUES (
        w.organization_id,w.location_id,w.id,target_role,
        shift_start,shift_end,shift_note,actor_value
    ) RETURNING to_jsonb(shift_slot) INTO result;

    UPDATE scheduling.schedule_week
    SET revision=revision+1 WHERE id=w.id;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        w.organization_id,w.location_id,actor_value,
        'schedule.shift_created','location',w.location_id,NULL,result
    );

    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session
        WHERE token_sha256=current_setting('scheduling.session_sha256',true)
          AND revoked_at IS NULL AND expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;
    RETURN scheduling.read_schedule_week(w.id);
END
$$;

REVOKE ALL ON FUNCTION scheduling.read_schedule_week(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION scheduling.create_draft_shift(
    uuid,uuid,integer,timestamptz,timestamptz,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.read_schedule_week(uuid)
TO shift_scheduling_app;
GRANT EXECUTE ON FUNCTION scheduling.create_draft_shift(
    uuid,uuid,integer,timestamptz,timestamptz,text
) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0016_draft_shift_slots', :'migration_sha256');
