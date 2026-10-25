DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

-- The trusted server evaluates eligibility. HTTP clients cannot supply
-- review results or fingerprints. This bridge binds that evaluation to
-- freshly locked inputs and commits the official version and audit together.
CREATE FUNCTION scheduling.commit_schedule_publication(
    target_location uuid,
    target_week uuid,
    expected_week_revision integer,
    expected_location_revision integer,
    expected_publication_number integer,
    publication_confirmed boolean,
    unfilled_confirmed boolean,
    expected_fingerprint text,
    reviewed jsonb
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    inputs jsonb;
    unfilled jsonb;
    actor_value uuid;
    saved scheduling.schedule_publication%ROWTYPE;
    result jsonb;
BEGIN
    inputs := scheduling.lock_publication_scope(
        target_location,target_week,expected_week_revision,
        expected_location_revision,expected_publication_number
    );
    IF expected_fingerprint IS NULL
       OR expected_fingerprint IS DISTINCT FROM md5(inputs::text) THEN
        RAISE EXCEPTION 'Publication inputs changed' USING ERRCODE='40001';
    END IF;
    IF expected_publication_number>=2147483647 THEN
        RAISE EXCEPTION 'Publication revision exhausted' USING ERRCODE='40001';
    END IF;

    SELECT coalesce(jsonb_agg(
        slot->>'id' ORDER BY slot->>'id'
    ),'[]'::jsonb) INTO unfilled
    FROM jsonb_array_elements(inputs->'slots') slot
    WHERE NOT EXISTS (
        SELECT FROM jsonb_array_elements(inputs->'assignments') assigned
        WHERE assigned->>'slot_id'=slot->>'id'
    );

    IF publication_confirmed IS DISTINCT FROM true
       OR reviewed->'can_publish' IS DISTINCT FROM 'true'::jsonb
       OR reviewed->'hard_conflicts' IS DISTINCT FROM '[]'::jsonb
       OR inputs->'inactive_role_slot_ids' IS DISTINCT FROM '[]'::jsonb
       OR jsonb_typeof(reviewed->'warnings') IS DISTINCT FROM 'array'
       OR reviewed->'unfilled_slot_ids' IS DISTINCT FROM unfilled THEN
        RAISE EXCEPTION 'Publication requires a valid confirmed review'
            USING ERRCODE='22023';
    END IF;
    IF EXISTS (
        SELECT FROM jsonb_array_elements(reviewed->'warnings') warning
        WHERE warning->'approved' IS DISTINCT FROM 'true'::jsonb
    ) THEN
        RAISE EXCEPTION 'Current warning approvals required'
            USING ERRCODE='22023';
    END IF;
    IF jsonb_array_length(unfilled)>0
       AND unfilled_confirmed IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Confirm Unfilled publication' USING ERRCODE='22023';
    END IF;

    IF reviewed->'roster'->'week'->>'id'
          IS DISTINCT FROM target_week::text
       OR reviewed->'roster'->'week'->>'location_id'
          IS DISTINCT FROM target_location::text
       OR reviewed->'roster'->'week'->>'week_start_date'
          IS DISTINCT FROM inputs->'week'->>'week_start_date'
       OR reviewed->'roster'->'week'->>'timezone'
          IS DISTINCT FROM inputs->'week'->>'timezone'
       OR reviewed->'roster'->'week'->'business_day_start_minute'
          IS DISTINCT FROM inputs->'week'->'business_day_start_minute'
       OR jsonb_array_length(reviewed->'roster'->'shifts')
          IS DISTINCT FROM jsonb_array_length(inputs->'slots') THEN
        RAISE EXCEPTION 'Invalid server roster snapshot' USING ERRCODE='22023';
    END IF;

    SELECT user_id INTO actor_value FROM scheduling.auth_session
    WHERE token_sha256=current_setting('scheduling.session_sha256',true)
      AND revoked_at IS NULL AND expires_at>clock_timestamp();
    IF actor_value IS NULL THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;

    INSERT INTO scheduling.schedule_publication(
        organization_id,location_id,schedule_week_id,
        publication_number,source_week_revision,source_location_revision,
        roster,published_by
    )
    SELECT organization_id,target_location,target_week,
           expected_publication_number+1,expected_week_revision,
           expected_location_revision,reviewed->'roster',actor_value
    FROM scheduling.location WHERE id=target_location
    RETURNING * INTO saved;

    -- AFTER INSERT captures private availability. Any capture failure also
    -- rolls back this publication; its existence activates exact cell locks.
    result := jsonb_build_object(
        'id',saved.id,'week_id',target_week,
        'publication_number',saved.publication_number,
        'source_week_revision',saved.source_week_revision,
        'source_location_revision',saved.source_location_revision,
        'published_by',saved.published_by,'published_at',saved.published_at,
        'unfilled_count',jsonb_array_length(unfilled)
    );
    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        saved.organization_id,target_location,actor_value,'schedule.published',
        'location',target_location,
        jsonb_build_object('publication_number',expected_publication_number),
        result
    );
    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session
        WHERE token_sha256=current_setting('scheduling.session_sha256',true)
          AND revoked_at IS NULL AND expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;
    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION scheduling.commit_schedule_publication(
    uuid,uuid,integer,integer,integer,boolean,boolean,text,jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.commit_schedule_publication(
    uuid,uuid,integer,integer,integer,boolean,boolean,text,jsonb
) TO shift_scheduling_app;

CREATE FUNCTION scheduling.guard_published_week_deletion()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
DECLARE
    removing boolean;
BEGIN
    removing := TG_OP='DELETE';
    IF TG_OP='UPDATE' THEN
        removing := NEW.deleted_at IS NOT NULL
                    AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at;
    END IF;
    IF removing AND EXISTS (
        SELECT FROM scheduling.schedule_publication
        WHERE schedule_week_id=OLD.id
    ) THEN
        RAISE EXCEPTION 'Published weeks cannot be deleted'
            USING ERRCODE='55000';
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION scheduling.guard_published_week_deletion()
FROM PUBLIC,shift_scheduling_app;

CREATE TRIGGER published_week_deletion
BEFORE UPDATE OR DELETE ON scheduling.schedule_week
FOR EACH ROW EXECUTE FUNCTION scheduling.guard_published_week_deletion();

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0051_commit_schedule_publication', :'migration_sha256');
