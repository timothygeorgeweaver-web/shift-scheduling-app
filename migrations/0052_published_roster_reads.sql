DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.employee_roster_snapshot(snapshot jsonb)
RETURNS jsonb
LANGUAGE sql IMMUTABLE
SET search_path=pg_catalog
AS $$
    SELECT jsonb_build_object(
        'schema_version',snapshot->'schema_version',
        'week',jsonb_build_object(
            'id',snapshot->'week'->'id',
            'location_id',snapshot->'week'->'location_id',
            'week_start_date',snapshot->'week'->'week_start_date',
            'timezone',snapshot->'week'->'timezone',
            'business_day_start_minute',
                snapshot->'week'->'business_day_start_minute'
        ),
        'shifts',coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'id',item->'id',
                'role_id',item->'role_id',
                'role_name',item->'role_name',
                'role_display_order',item->'role_display_order',
                'business_date',item->'business_date',
                'start_at',item->'start_at',
                'end_at',item->'end_at',
                'local_start',item->'local_start',
                'local_end',item->'local_end',
                'duration_minutes',item->'duration_minutes',
                'meal_estimate_minutes',item->'meal_estimate_minutes',
                'estimated_work_minutes',item->'estimated_work_minutes',
                'employee',CASE WHEN jsonb_typeof(item->'employee')='object'
                    THEN jsonb_build_object(
                        'id',item->'employee'->'id',
                        'display_name',item->'employee'->'display_name'
                    ) ELSE NULL END
            ) ORDER BY position)
            FROM jsonb_array_elements(snapshot->'shifts')
                WITH ORDINALITY AS shifts(item,position)
        ),'[]'::jsonb)
    );
$$;
REVOKE ALL ON FUNCTION scheduling.employee_roster_snapshot(jsonb)
FROM PUBLIC,shift_scheduling_app;

CREATE FUNCTION scheduling.list_my_published_weeks(
    target_location uuid,range_start date,range_end date
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM jsonb_array_elements(
            scheduling.my_unavailability_profiles()
        ) profile
        WHERE profile->>'location_id'=target_location::text
    ) THEN
        RAISE EXCEPTION 'Published schedules unavailable' USING ERRCODE='42501';
    END IF;
    IF range_start IS NULL OR range_end IS NULL
       OR NOT isfinite(range_start) OR NOT isfinite(range_end)
       OR range_start<date '2000-01-01' OR range_end>date '9998-12-31'
       OR range_end<range_start OR range_end-range_start>365 THEN
        RAISE EXCEPTION 'Invalid published schedule range' USING ERRCODE='22023';
    END IF;

    RETURN coalesce((
        SELECT jsonb_agg(jsonb_build_object(
            'week_id',latest.schedule_week_id,
            'publication_id',latest.id,
            'publication_number',latest.publication_number,
            'published_at',latest.published_at,
            'week_start_date',latest.roster->'week'->'week_start_date',
            'timezone',latest.roster->'week'->'timezone',
            'business_day_start_minute',
                latest.roster->'week'->'business_day_start_minute'
        ) ORDER BY latest.roster->'week'->>'week_start_date')
        FROM (
            SELECT DISTINCT ON (p.schedule_week_id) p.*
            FROM scheduling.schedule_publication p
            WHERE p.location_id=target_location
              AND (p.roster->'week'->>'week_start_date')::date
                  BETWEEN range_start AND range_end
            ORDER BY p.schedule_week_id,p.publication_number DESC
        ) latest
    ),'[]'::jsonb);
END
$$;

CREATE FUNCTION scheduling.read_my_published_week(
    target_location uuid,target_week uuid
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    published scheduling.schedule_publication%ROWTYPE;
BEGIN
    IF NOT EXISTS (
        SELECT FROM jsonb_array_elements(
            scheduling.my_unavailability_profiles()
        ) profile
        WHERE profile->>'location_id'=target_location::text
    ) THEN
        RAISE EXCEPTION 'Published schedule unavailable' USING ERRCODE='42501';
    END IF;
    SELECT * INTO published FROM scheduling.schedule_publication
    WHERE location_id=target_location AND schedule_week_id=target_week
    ORDER BY publication_number DESC LIMIT 1;
    IF published.id IS NULL THEN
        RAISE EXCEPTION 'Published schedule unavailable' USING ERRCODE='42501';
    END IF;
    RETURN jsonb_build_object(
        'publication',jsonb_build_object(
            'id',published.id,'week_id',published.schedule_week_id,
            'publication_number',published.publication_number,
            'published_at',published.published_at
        ),
        'roster',scheduling.employee_roster_snapshot(published.roster)
    );
END
$$;

CREATE FUNCTION scheduling.read_staff_published_version(
    target_week uuid,target_number integer
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    published scheduling.schedule_publication%ROWTYPE;
BEGIN
    PERFORM scheduling.read_schedule_week(target_week);
    SELECT * INTO published FROM scheduling.schedule_publication
    WHERE schedule_week_id=target_week AND publication_number=target_number;
    IF published.id IS NULL THEN
        RAISE EXCEPTION 'Publication unavailable' USING ERRCODE='42501';
    END IF;
    RETURN jsonb_build_object(
        'publication',jsonb_build_object(
            'id',published.id,'week_id',published.schedule_week_id,
            'publication_number',published.publication_number,
            'published_at',published.published_at,
            'published_by',published.published_by
        ),
        'roster',scheduling.employee_roster_snapshot(published.roster)
    );
END
$$;

REVOKE ALL ON FUNCTION scheduling.list_my_published_weeks(uuid,date,date)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.list_my_published_weeks(uuid,date,date)
TO shift_scheduling_app;
REVOKE ALL ON FUNCTION scheduling.read_my_published_week(uuid,uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.read_my_published_week(uuid,uuid)
TO shift_scheduling_app;
REVOKE ALL ON FUNCTION scheduling.read_staff_published_version(uuid,integer)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.read_staff_published_version(uuid,integer)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0052_published_roster_reads', :'migration_sha256');
