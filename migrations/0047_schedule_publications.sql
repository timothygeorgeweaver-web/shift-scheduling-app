DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.schedule_publication (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    schedule_week_id uuid NOT NULL,
    publication_number integer NOT NULL CHECK (publication_number>0),
    source_week_revision integer NOT NULL CHECK (source_week_revision>0),
    source_location_revision integer NOT NULL CHECK (source_location_revision>0),
    roster jsonb NOT NULL,
    published_by uuid NOT NULL,
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (organization_id,location_id,id),
    UNIQUE (schedule_week_id,publication_number),
    FOREIGN KEY (organization_id,location_id,schedule_week_id)
        REFERENCES scheduling.schedule_week(organization_id,location_id,id),
    FOREIGN KEY (organization_id,published_by)
        REFERENCES scheduling.organization_member(organization_id,user_id),
    CHECK ((
        jsonb_typeof(roster)='object'
        AND roster->>'schema_version'='1'
        AND jsonb_typeof(roster->'week')='object'
        AND roster->'week'->>'id'=schedule_week_id::text
        AND roster->'week'->>'location_id'=location_id::text
        AND jsonb_typeof(roster->'shifts')='array'
    ) IS TRUE)
);

ALTER TABLE scheduling.schedule_publication ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON scheduling.schedule_publication
FROM PUBLIC,shift_scheduling_app;

CREATE FUNCTION scheduling.preserve_schedule_publication()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'Published roster versions are immutable'
        USING ERRCODE='55000';
END
$$;

REVOKE ALL ON FUNCTION scheduling.preserve_schedule_publication()
FROM PUBLIC,shift_scheduling_app;

CREATE TRIGGER schedule_publication_immutable
BEFORE UPDATE OR DELETE ON scheduling.schedule_publication
FOR EACH ROW EXECUTE FUNCTION scheduling.preserve_schedule_publication();

CREATE FUNCTION scheduling.read_schedule_publication_history(target_week uuid)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
BEGIN
    PERFORM scheduling.read_schedule_week(target_week);
    RETURN coalesce((
        SELECT jsonb_agg(jsonb_build_object(
            'id',p.id,
            'publication_number',p.publication_number,
            'source_week_revision',p.source_week_revision,
            'source_location_revision',p.source_location_revision,
            'published_by',p.published_by,
            'published_at',p.published_at
        ) ORDER BY p.publication_number)
        FROM scheduling.schedule_publication p
        WHERE p.schedule_week_id=target_week
    ),'[]'::jsonb);
END
$$;

REVOKE ALL ON FUNCTION
scheduling.read_schedule_publication_history(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.read_schedule_publication_history(uuid) TO shift_scheduling_app;

-- No runtime publication writer or employee roster access is enabled yet.
INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0047_schedule_publications', :'migration_sha256');
