DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE FUNCTION scheduling.lock_template_destination(
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
    organization_value uuid;
    actor_value uuid;
    l scheduling.location%ROWTYPE;
    w scheduling.schedule_week%ROWTYPE;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.location WHERE id=target_location;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Location unavailable' USING ERRCODE='42501';
    END IF;
    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    SELECT * INTO STRICT l FROM scheduling.location
    WHERE id=target_location FOR SHARE;

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
        RAISE EXCEPTION 'Staff access required' USING ERRCODE='42501';
    END IF;
    IF expected_location_revision IS NULL
       OR expected_location_revision<>l.revision THEN
        RAISE EXCEPTION 'Location changed' USING ERRCODE='40001';
    END IF;

    SELECT * INTO w FROM scheduling.schedule_week
    WHERE id=target_week AND location_id=target_location
      AND organization_id=organization_value AND deleted_at IS NULL
    FOR UPDATE;
    IF w.id IS NULL THEN
        RAISE EXCEPTION 'Week unavailable' USING ERRCODE='42501';
    END IF;
    IF w.state<>'draft' THEN
        RAISE EXCEPTION 'Only drafts can receive templates' USING ERRCODE='55000';
    END IF;
    IF expected_week_revision IS NULL OR expected_week_revision<>w.revision
       OR w.revision=2147483647 THEN
        RAISE EXCEPTION 'Week changed' USING ERRCODE='40001';
    END IF;
    RETURN scheduling.read_schedule_week(w.id);
END
$$;

REVOKE ALL ON FUNCTION
scheduling.lock_template_destination(uuid,uuid,integer,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.lock_template_destination(uuid,uuid,integer,integer)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0027_template_destination_scope', :'migration_sha256');
