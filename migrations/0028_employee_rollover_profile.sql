DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION scheduling.my_unavailability_profiles()
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
        'business_day_start_minute',l.business_day_start_minute,
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

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0028_employee_rollover_profile', :'migration_sha256');
