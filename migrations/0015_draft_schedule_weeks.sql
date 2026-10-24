DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.schedule_week (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    week_start_date date NOT NULL CHECK (
        week_start_date BETWEEN date '2000-01-01' AND date '9998-12-25'
    ),
    week_start_day smallint NOT NULL CHECK (week_start_day BETWEEN 0 AND 6),
    timezone text NOT NULL,
    state text NOT NULL DEFAULT 'draft'
        CHECK (state IN ('draft','published','archived')),
    revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        extract(isodow FROM week_start_date)::integer-1=week_start_day
    ),
    UNIQUE (location_id,week_start_date),
    UNIQUE (organization_id,location_id,id),
    FOREIGN KEY (organization_id,location_id)
        REFERENCES scheduling.location(organization_id,id),
    FOREIGN KEY (organization_id,created_by)
        REFERENCES scheduling.organization_member(organization_id,user_id)
);
ALTER TABLE scheduling.schedule_week ENABLE ROW LEVEL SECURITY;
-- No direct runtime table access or state-change grants.

CREATE FUNCTION scheduling.list_schedule_weeks(
    target_location uuid, range_start date, range_end date
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    l scheduling.location%ROWTYPE;
BEGIN
    SELECT * INTO l FROM scheduling.location WHERE id=target_location;
    IF l.id IS NULL OR NOT scheduling.staff_read_allowed(l.organization_id) THEN
        RAISE EXCEPTION 'Schedule location unavailable' USING ERRCODE='42501';
    END IF;
    IF range_start IS NULL OR range_end IS NULL
       OR NOT isfinite(range_start) OR NOT isfinite(range_end)
       OR range_start<date '2000-01-01' OR range_end>date '9998-12-31'
       OR range_end<range_start OR range_end-range_start>365 THEN
        RAISE EXCEPTION 'Invalid schedule range' USING ERRCODE='22023';
    END IF;

    RETURN jsonb_build_object(
        'location',jsonb_build_object(
            'id',l.id,'name',l.name,'timezone',l.timezone,
            'revision',l.revision,'week_start_day',l.week_start
        ),
        'weeks',coalesce((
            SELECT jsonb_agg(to_jsonb(w) ORDER BY w.week_start_date,w.id)
            FROM scheduling.schedule_week w
            WHERE w.location_id=target_location
              AND w.week_start_date BETWEEN range_start AND range_end
        ),'[]'::jsonb)
    );
END
$$;

CREATE FUNCTION scheduling.create_blank_schedule_week(
    target_location uuid, target_start date, expected_location_revision integer
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    actor_value uuid;
    l scheduling.location%ROWTYPE;
    result jsonb;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.location WHERE id=target_location;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Schedule location unavailable' USING ERRCODE='42501';
    END IF;

    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    SELECT * INTO STRICT l FROM scheduling.location
    WHERE id=target_location FOR UPDATE;

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
    IF expected_location_revision IS NULL
       OR expected_location_revision<>l.revision THEN
        RAISE EXCEPTION 'Location changed' USING ERRCODE='40001';
    END IF;
    IF target_start IS NULL OR NOT isfinite(target_start)
       OR target_start<date '2000-01-01'
       OR target_start>date '9998-12-25'
       OR extract(isodow FROM target_start)::integer-1<>l.week_start THEN
        RAISE EXCEPTION 'Choose the configured week-start date'
            USING ERRCODE='22023';
    END IF;

    -- Serialize creation and reject overlapping weeks even after a later
    -- administrator change to the location's configured week-start day.
    IF EXISTS (
        SELECT FROM scheduling.schedule_week
        WHERE location_id=target_location
          AND week_start_date<=target_start+6
          AND week_start_date+6>=target_start
    ) THEN
        RAISE EXCEPTION 'A schedule already covers these dates'
            USING ERRCODE='40001';
    END IF;

    INSERT INTO scheduling.schedule_week(
        organization_id,location_id,week_start_date,week_start_day,
        timezone,created_by
    ) VALUES (
        organization_value,target_location,target_start,l.week_start,
        l.timezone,actor_value
    ) RETURNING to_jsonb(schedule_week) INTO result;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_value,target_location,actor_value,
        'schedule.week_created','location',target_location,NULL,result
    );
    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION
scheduling.list_schedule_weeks(uuid,date,date) FROM PUBLIC;
REVOKE ALL ON FUNCTION
scheduling.create_blank_schedule_week(uuid,date,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.list_schedule_weeks(uuid,date,date) TO shift_scheduling_app;
GRANT EXECUTE ON FUNCTION
scheduling.create_blank_schedule_week(uuid,date,integer) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0015_draft_schedule_weeks', :'migration_sha256');
