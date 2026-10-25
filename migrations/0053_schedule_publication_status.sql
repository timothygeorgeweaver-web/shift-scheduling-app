DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION scheduling.list_schedule_weeks(
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
            'revision',l.revision,'week_start_day',l.week_start,
            'business_day_start_minute',l.business_day_start_minute
        ),
        'weeks',coalesce((
            SELECT jsonb_agg(
                (to_jsonb(w) || jsonb_build_object(
                    'publication_number',coalesce(latest.publication_number,0),
                    'published_source_week_revision',latest.source_week_revision,
                    'published_at',latest.published_at,
                    'has_unpublished_changes',coalesce(
                        w.revision<>latest.source_week_revision,false
                    )
                )) ORDER BY w.week_start_date,w.id
            )
            FROM scheduling.schedule_week w
            LEFT JOIN LATERAL (
                SELECT p.publication_number,p.source_week_revision,p.published_at
                FROM scheduling.schedule_publication p
                WHERE p.schedule_week_id=w.id
                ORDER BY p.publication_number DESC LIMIT 1
            ) latest ON true
            WHERE w.location_id=target_location AND w.deleted_at IS NULL
              AND w.week_start_date BETWEEN range_start AND range_end
        ),'[]'::jsonb)
    );
END
$$;


INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0053_schedule_publication_status', :'migration_sha256');
