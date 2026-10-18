DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.unavailability_recurring (
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision>0),
    effective_from date NOT NULL,
    weekly_windows jsonb NOT NULL CHECK (jsonb_typeof(weekly_windows)='object'),
    actor_user_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (employee_id,revision),
    FOREIGN KEY (organization_id,location_id,employee_id)
      REFERENCES scheduling.employee(organization_id,location_id,id),
    FOREIGN KEY (organization_id,actor_user_id)
      REFERENCES scheduling.organization_member(organization_id,user_id)
);

CREATE INDEX unavailability_recurring_effective_idx
ON scheduling.unavailability_recurring(
    employee_id,effective_from DESC,revision DESC
);

CREATE TABLE scheduling.unavailability_exception (
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    local_date date NOT NULL,
    revision integer NOT NULL CHECK (revision>0),
    present boolean NOT NULL DEFAULT true,
    windows jsonb NOT NULL CHECK (jsonb_typeof(windows)='array'),
    actor_user_id uuid NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (employee_id,local_date),
    CHECK (present OR windows='[]'::jsonb),
    FOREIGN KEY (organization_id,location_id,employee_id)
      REFERENCES scheduling.employee(organization_id,location_id,id),
    FOREIGN KEY (organization_id,actor_user_id)
      REFERENCES scheduling.organization_member(organization_id,user_id)
);

CREATE TABLE scheduling.unavailability_day_lock (
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    local_date date NOT NULL,
    blocked_windows jsonb NOT NULL CHECK (jsonb_typeof(blocked_windows)='array'),
    reason text NOT NULL CHECK (reason IN ('cutoff','publication')),
    actor_user_id uuid NOT NULL,
    frozen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (employee_id,local_date),
    FOREIGN KEY (organization_id,location_id,employee_id)
      REFERENCES scheduling.employee(organization_id,location_id,id),
    FOREIGN KEY (organization_id,actor_user_id)
      REFERENCES scheduling.organization_member(organization_id,user_id)
);

ALTER TABLE scheduling.unavailability_recurring ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduling.unavailability_exception ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduling.unavailability_day_lock ENABLE ROW LEVEL SECURITY;

-- No runtime table grants or policies. Controlled owner functions project
-- only the live session's own active employee profiles and records.
CREATE FUNCTION scheduling.my_unavailability_profiles()
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog
AS $$
    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'employee_id',e.id,
        'display_name',e.display_name,
        'organization_id',e.organization_id,
        'location_id',l.id,
        'business_name',o.name,
        'location_name',l.name,
        'timezone',l.timezone,
        'location_revision',l.revision,
        'week_start',l.week_start,
        'cutoff_days_before',l.availability_cutoff_days,
        'cutoff_time',left(l.availability_cutoff_time::text,5)
    ) ORDER BY o.name,l.name,e.id),'[]'::jsonb)
    FROM scheduling.auth_session s
    JOIN scheduling.app_user u ON u.id=s.user_id
    JOIN scheduling.organization_member m ON m.user_id=u.id
    JOIN scheduling.employee e
      ON e.organization_id=m.organization_id AND e.user_id=u.id
    JOIN scheduling.location l ON l.id=e.location_id
    JOIN scheduling.organization o ON o.id=e.organization_id
    WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
      AND s.revoked_at IS NULL AND s.expires_at>now()
      AND u.active AND m.active AND e.active;
$$;

CREATE FUNCTION scheduling.read_my_unavailability(
    target_location uuid,
    target_start date,
    day_count integer DEFAULT 7
)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    profile jsonb;
    employee_id_value uuid;
    first_origin date;
    last_date date;
BEGIN
    SELECT x.value INTO profile
    FROM jsonb_array_elements(scheduling.my_unavailability_profiles()) x(value)
    WHERE x.value->>'location_id'=target_location::text;

    IF profile IS NULL THEN
        RAISE EXCEPTION 'Employee profile unavailable' USING ERRCODE='42501';
    END IF;
    IF target_start IS NULL OR day_count IS NULL
       OR day_count NOT BETWEEN 1 AND 35
       OR target_start<date '0001-01-01'
       OR target_start>date '9999-12-31'-(day_count-1) THEN
        RAISE EXCEPTION 'Invalid date range' USING ERRCODE='22023';
    END IF;

    employee_id_value := (profile->>'employee_id')::uuid;
    first_origin := greatest(target_start-1,date '0001-01-01');
    last_date := target_start+(day_count-1);

    RETURN jsonb_build_object(
        'profile',profile,
        'pattern_revision',coalesce((
            SELECT max(r.revision) FROM scheduling.unavailability_recurring r
            WHERE r.employee_id=employee_id_value
        ),0),
        'recurring',coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'origin_date',d.local_date,
                'revision',coalesce(r.revision,0),
                'windows',coalesce(r.weekly_windows->(
                    (extract(isodow FROM d.local_date)::integer-1)::text
                ),'[]'::jsonb)
            ) ORDER BY d.local_date)
            FROM (
                SELECT first_origin+g.n AS local_date
                FROM generate_series(0,last_date-first_origin) g(n)
            ) d
            LEFT JOIN LATERAL (
                SELECT p.revision,p.weekly_windows
                FROM scheduling.unavailability_recurring p
                WHERE p.employee_id=employee_id_value
                  AND p.effective_from<=d.local_date
                ORDER BY p.effective_from DESC,p.revision DESC LIMIT 1
            ) r ON true
        ),'[]'::jsonb),
        'exceptions',coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'date',x.local_date,'revision',x.revision,
                'present',x.present,'windows',x.windows
            ) ORDER BY x.local_date)
            FROM scheduling.unavailability_exception x
            WHERE x.employee_id=employee_id_value
              AND x.local_date BETWEEN first_origin AND last_date
        ),'[]'::jsonb),
        'locks',coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'date',k.local_date,'reason',k.reason,
                'blocked',k.blocked_windows
            ) ORDER BY k.local_date)
            FROM scheduling.unavailability_day_lock k
            WHERE k.employee_id=employee_id_value
              AND k.local_date BETWEEN target_start AND last_date
        ),'[]'::jsonb)
    );
END
$$;

REVOKE ALL ON FUNCTION scheduling.my_unavailability_profiles() FROM PUBLIC;
REVOKE ALL ON FUNCTION scheduling.read_my_unavailability(uuid,date,integer)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.my_unavailability_profiles()
TO shift_scheduling_app;
GRANT EXECUTE ON FUNCTION scheduling.read_my_unavailability(uuid,date,integer)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0010_private_unavailability_storage', :'migration_sha256');
