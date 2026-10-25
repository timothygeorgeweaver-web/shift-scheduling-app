DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.clear_draft_week_assignments(
    target_location uuid,
    target_week uuid,
    expected_week_revision integer,
    expected_location_revision integer
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    scope jsonb;
    item record;
    current_week_revision integer;
    cleared integer := 0;
    actor_value uuid;
    organization_value uuid;
    result jsonb;
BEGIN
    -- Existing scope validates live staff authorization, draft state,
    -- location/week identity and both expected revisions under locks.
    scope := scheduling.lock_template_destination(
        target_location,target_week,
        expected_week_revision,expected_location_revision
    );
    current_week_revision := expected_week_revision;

    FOR item IN
        SELECT a.id,a.revision AS assignment_revision,
               s.id AS slot_id,s.revision AS slot_revision
        FROM scheduling.shift_assignment a
        JOIN scheduling.shift_slot s ON s.id=a.shift_slot_id
        WHERE s.schedule_week_id=target_week
          AND s.location_id=target_location AND s.active AND a.active
        ORDER BY s.id,a.id
        FOR UPDATE OF a,s
    LOOP
        PERFORM scheduling.unassign_draft_shift(
            item.slot_id,item.id,item.assignment_revision,
            current_week_revision,item.slot_revision,
            expected_location_revision
        );
        cleared := cleared+1;
        current_week_revision := current_week_revision+1;
    END LOOP;

    result := jsonb_build_object(
        'week_id',target_week,'cleared_assignments',cleared,
        'week_revision',current_week_revision
    );
    IF cleared=0 THEN
        RETURN result;
    END IF;

    SELECT organization_id INTO STRICT organization_value
    FROM scheduling.location WHERE id=target_location;
    SELECT user_id INTO STRICT actor_value
    FROM scheduling.auth_session
    WHERE token_sha256=current_setting('scheduling.session_sha256',true)
      AND revoked_at IS NULL AND expires_at>clock_timestamp();

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,
        entity_type,entity_id,before_data,after_data
    ) VALUES (
        organization_value,target_location,actor_value,
        'assignment.week_cleared','location',target_location,
        jsonb_build_object(
            'week_id',target_week,'week_revision',expected_week_revision
        ),result
    );
    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION scheduling.clear_draft_week_assignments(
    uuid,uuid,integer,integer
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.clear_draft_week_assignments(
    uuid,uuid,integer,integer
) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0039_clear_week_assignments', :'migration_sha256');
