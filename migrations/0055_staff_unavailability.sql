DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;


CREATE FUNCTION scheduling.staff_planner_profile(
    target_location uuid, target_employee uuid
)
RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog
AS $$
    SELECT jsonb_build_object(
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
        'cutoff_time',left(l.availability_cutoff_time::text,5),
        'actor_user_id',u.id
    )
    FROM scheduling.auth_session s
    JOIN scheduling.app_user u ON u.id=s.user_id
    JOIN scheduling.organization_member m ON m.user_id=u.id
    JOIN scheduling.employee e ON e.organization_id=m.organization_id
    JOIN scheduling.location l ON l.id=e.location_id
    JOIN scheduling.organization o ON o.id=e.organization_id
    WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
      AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
      AND u.active AND m.active
      AND e.id=target_employee AND l.id=target_location
      AND EXISTS (
          SELECT FROM scheduling.member_permission p
          WHERE p.organization_id=m.organization_id AND p.user_id=m.user_id
            AND p.permission IN ('administrator','scheduler')
      );
$$;

REVOKE ALL ON FUNCTION scheduling.staff_planner_profile(uuid,uuid)
FROM PUBLIC,shift_scheduling_app;

CREATE FUNCTION scheduling.read_staff_planner(
    target_location uuid, target_employee uuid, first_date date
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    profile jsonb;
    employee_value uuid;
    result_days jsonb := '[]'::jsonb;
    day_value date;
    stored scheduling.unavailability_date%ROWTYPE;
    state jsonb;
BEGIN
    profile := scheduling.staff_planner_profile(
        target_location,target_employee
    );

    IF profile IS NULL THEN
        RAISE EXCEPTION 'Employee profile unavailable' USING ERRCODE='42501';
    END IF;
    IF first_date IS NULL OR NOT isfinite(first_date)
       OR first_date<date '2000-01-01'
       OR first_date>date '9998-12-25' THEN
        RAISE EXCEPTION 'Invalid planner dates' USING ERRCODE='22023';
    END IF;

    employee_value := (profile->>'employee_id')::uuid;
    FOR n IN 0..6 LOOP
        day_value := first_date+n;
        SELECT * INTO stored FROM scheduling.unavailability_date
        WHERE employee_id=employee_value AND local_date=day_value;
        state := scheduling.planner_day_state(
            target_location,employee_value,day_value
        );
        result_days := result_days || jsonb_build_array(
            jsonb_build_object(
                'date',day_value,
                'revision',coalesce(stored.revision,0),
                'blocked_cells',coalesce(stored.blocked_cells,'[]'::jsonb)
            ) || state
        );
    END LOOP;
    RETURN jsonb_build_object('profile',profile,'days',result_days);
END
$$;

CREATE FUNCTION scheduling.save_staff_planner(
    target_location uuid,
    target_employee uuid,
    first_date date,
    expected_location_revision integer,
    changed_dates jsonb
)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog
AS $$
DECLARE
    organization_value uuid;
    employee_value uuid;
    actor_value uuid;
    l scheduling.location%ROWTYPE;
    item jsonb;
    cell jsonb;
    normalized jsonb;
    day_value date;
    visited date[] := ARRAY[]::date[];
    expected integer;
    current_revision integer;
    before_value jsonb;
    after_value jsonb;
    old_cells jsonb;
    state jsonb;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.location WHERE id=target_location;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Employee profile unavailable' USING ERRCODE='42501';
    END IF;

    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;
    SELECT * INTO STRICT l FROM scheduling.location
    WHERE id=target_location FOR UPDATE;

    SELECT e.id INTO employee_value
    FROM scheduling.employee e
    WHERE e.id=target_employee AND e.location_id=target_location
    FOR UPDATE;

    actor_value := (
        scheduling.staff_planner_profile(target_location,target_employee)
        ->>'actor_user_id'
    )::uuid;
    IF actor_value IS NULL THEN
        RAISE EXCEPTION 'Staff planner access denied' USING ERRCODE='42501';
    END IF;

    IF employee_value IS NULL THEN
        RAISE EXCEPTION 'Employee profile unavailable' USING ERRCODE='42501';
    END IF;
    IF expected_location_revision IS NULL
       OR expected_location_revision<>l.revision THEN
        RAISE EXCEPTION 'Location policy changed' USING ERRCODE='40001';
    END IF;
    IF first_date IS NULL OR NOT isfinite(first_date)
       OR first_date<date '2000-01-01'
       OR first_date>date '9998-12-25'
       OR changed_dates IS NULL
       OR jsonb_typeof(changed_dates)<>'array' THEN
        RAISE EXCEPTION 'Invalid planner request' USING ERRCODE='22023';
    END IF;
    IF jsonb_array_length(changed_dates) NOT BETWEEN 1 AND 7 THEN
        RAISE EXCEPTION 'Supply one through seven changed dates'
            USING ERRCODE='22023';
    END IF;

    FOR item IN SELECT value FROM jsonb_array_elements(changed_dates)
    LOOP
        IF jsonb_typeof(item)<>'object' THEN
            RAISE EXCEPTION 'Invalid date record' USING ERRCODE='22023';
        END IF;
        IF NOT (item ?& ARRAY['date','expected_revision','cells'])
           OR EXISTS (
               SELECT FROM jsonb_object_keys(item) k
               WHERE k NOT IN ('date','expected_revision','cells')
           )
           OR jsonb_typeof(item->'date') IS DISTINCT FROM 'string'
           OR (item->>'date') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
           OR jsonb_typeof(item->'expected_revision') IS DISTINCT FROM 'number'
           OR (item->>'expected_revision') !~ '^[0-9]{1,9}$'
           OR jsonb_typeof(item->'cells') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'Invalid date fields' USING ERRCODE='22023';
        END IF;

        day_value := (item->>'date')::date;
        expected := (item->>'expected_revision')::integer;
        IF day_value<first_date OR day_value>first_date+6
           OR day_value=ANY(visited) THEN
            RAISE EXCEPTION 'Duplicate or out-of-view date' USING ERRCODE='22023';
        END IF;
        visited := array_append(visited,day_value);

        state := scheduling.planner_day_state(
            target_location,employee_value,day_value
        );
        IF NOT (state->>'editable')::boolean THEN
            RAISE EXCEPTION 'Date is locked' USING ERRCODE='55000';
        END IF;

        IF jsonb_array_length(item->'cells')>96 THEN
            RAISE EXCEPTION 'Too many cells' USING ERRCODE='22023';
        END IF;
        FOR cell IN SELECT value FROM jsonb_array_elements(item->'cells')
        LOOP
            IF jsonb_typeof(cell)<>'number'
               OR cell::text !~ '^[0-9]{1,2}$' THEN
                RAISE EXCEPTION 'Invalid cell type' USING ERRCODE='22023';
            END IF;
            IF cell::text::integer NOT BETWEEN 0 AND 95 THEN
                RAISE EXCEPTION 'Cell out of range' USING ERRCODE='22023';
            END IF;
        END LOOP;
        SELECT coalesce(jsonb_agg(n ORDER BY n),'[]'::jsonb) INTO normalized
        FROM (
            SELECT DISTINCT value::text::integer AS n
            FROM jsonb_array_elements(item->'cells')
        ) canonical;

        before_value := NULL;
        SELECT to_jsonb(d),d.revision,d.blocked_cells
        INTO before_value,current_revision,old_cells
        FROM scheduling.unavailability_date d
        WHERE d.employee_id=employee_value AND d.local_date=day_value;
        current_revision := coalesce(current_revision,0);
        old_cells := coalesce(old_cells,'[]'::jsonb);

        IF current_revision<>expected OR current_revision=2147483647 THEN
            RAISE EXCEPTION 'Date changed' USING ERRCODE='40001';
        END IF;

        IF old_cells<>normalized THEN
            INSERT INTO scheduling.unavailability_date(
                organization_id,location_id,employee_id,local_date,revision,
                blocked_cells,actor_user_id
            ) VALUES (
                organization_value,target_location,employee_value,day_value,
                current_revision+1,normalized,actor_value
            )
            ON CONFLICT (employee_id,local_date) DO UPDATE
            SET revision=EXCLUDED.revision,
                blocked_cells=EXCLUDED.blocked_cells,
                actor_user_id=EXCLUDED.actor_user_id,
                updated_at=clock_timestamp()
            RETURNING to_jsonb(unavailability_date) INTO after_value;

            INSERT INTO scheduling.audit_event(
                organization_id,location_id,actor_user_id,action,
                entity_type,entity_id,before_data,after_data
            ) VALUES (
                organization_value,target_location,actor_value,
                'unavailability.staff_date_saved','employee',employee_value,
                before_value,after_value
            );
        END IF;
    END LOOP;

    -- A deadline reached during the multi-date operation rolls it all back.
    FOREACH day_value IN ARRAY visited LOOP
        state := scheduling.planner_day_state(
            target_location,employee_value,day_value
        );
        IF NOT (state->>'editable')::boolean THEN
            RAISE EXCEPTION 'Date locked during save' USING ERRCODE='55000';
        END IF;
    END LOOP;
    IF NOT EXISTS (
        SELECT FROM scheduling.auth_session s
        WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
          AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
    ) THEN
        RAISE EXCEPTION 'Session expired' USING ERRCODE='42501';
    END IF;

    RETURN scheduling.read_staff_planner(target_location,target_employee,first_date);
END
$$;


REVOKE ALL ON FUNCTION scheduling.read_staff_planner(uuid,uuid,date)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.read_staff_planner(uuid,uuid,date)
TO shift_scheduling_app;

REVOKE ALL ON FUNCTION
scheduling.save_staff_planner(uuid,uuid,date,integer,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
scheduling.save_staff_planner(uuid,uuid,date,integer,jsonb)
TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0055_staff_unavailability', :'migration_sha256');
