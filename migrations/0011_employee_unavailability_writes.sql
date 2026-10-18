DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

-- Full interval history remains in immutable audit events, but a coworker's
-- staff-history query must not reveal another employee's personal intervals.
CREATE POLICY private_unavailability_audit
ON scheduling.audit_event AS RESTRICTIVE
FOR SELECT TO shift_scheduling_app
USING (
    action NOT LIKE 'unavailability.%'
    OR actor_user_id = (
        SELECT s.user_id
        FROM scheduling.auth_session s
        JOIN scheduling.app_user u ON u.id=s.user_id
        WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
          AND s.revoked_at IS NULL
          AND s.expires_at>clock_timestamp()
          AND u.active
    )
);

-- Private helper. Check every possible modern UTC minute offset rather than
-- allowing PostgreSQL to silently choose one side of an ambiguous DST time.
CREATE FUNCTION scheduling.unavailability_cutoff(
    target_location uuid, target_day date
)
RETURNS timestamptz
LANGUAGE plpgsql STABLE
SET search_path=pg_catalog
AS $$
DECLARE
    l scheduling.location%ROWTYPE;
    week_date date;
    local_cutoff timestamp;
    resolved timestamptz;
    matches integer;
BEGIN
    SELECT * INTO STRICT l FROM scheduling.location
    WHERE id=target_location;

    week_date := target_day - (
        (extract(isodow FROM target_day)::integer-1-l.week_start+7)%7
    );
    local_cutoff := (week_date-l.availability_cutoff_days)
                    + l.availability_cutoff_time;

    SELECT count(*),min(candidate) INTO matches,resolved
    FROM (
        SELECT (local_cutoff AT TIME ZONE 'UTC')
               + g.n*interval '1 minute' AS candidate
        FROM generate_series(-1560,1560) g(n)
    ) candidates
    WHERE candidate AT TIME ZONE l.timezone=local_cutoff;

    IF matches<>1 THEN
        RAISE EXCEPTION 'Cutoff time is ambiguous or nonexistent'
            USING ERRCODE='22023';
    END IF;
    RETURN resolved;
END
$$;
REVOKE ALL ON FUNCTION
scheduling.unavailability_cutoff(uuid,date) FROM PUBLIC;

CREATE FUNCTION scheduling.write_my_unavailability(
    target_location uuid,
    operation text,
    target_day date,
    expected_revision integer,
    expected_location_revision integer,
    supplied_windows jsonb
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
    current_revision integer;
    previous_state jsonb;
    result jsonb;
    group_windows jsonb;
    window_value jsonb;
    start_min integer;
    end_min integer;
    next_day boolean;
    end_absolute integer;
    cutoff_value timestamptz;
    check_time timestamptz;
BEGIN
    SELECT organization_id INTO organization_value
    FROM scheduling.location WHERE id=target_location;
    IF organization_value IS NULL THEN
        RAISE EXCEPTION 'Employee profile unavailable' USING ERRCODE='42501';
    END IF;

    -- Same organization lock order as administrator policy/account functions.
    PERFORM 1 FROM scheduling.organization
    WHERE id=organization_value FOR UPDATE;

    SELECT * INTO STRICT l FROM scheduling.location
    WHERE id=target_location FOR UPDATE;

    -- Independently derive and lock the live employee identity. No employee ID
    -- is accepted from the caller.
    SELECT e.id,u.id INTO employee_value,actor_value
    FROM scheduling.auth_session s
    JOIN scheduling.app_user u ON u.id=s.user_id
    JOIN scheduling.organization_member m
      ON m.user_id=u.id AND m.organization_id=organization_value
    JOIN scheduling.employee e
      ON e.organization_id=m.organization_id AND e.user_id=u.id
    WHERE s.token_sha256=current_setting('scheduling.session_sha256',true)
      AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp()
      AND u.active AND m.active AND e.active
      AND e.location_id=target_location
    FOR UPDATE OF e FOR SHARE OF s,u,m;

    IF employee_value IS NULL THEN
        RAISE EXCEPTION 'Employee profile unavailable' USING ERRCODE='42501';
    END IF;
    IF expected_location_revision IS NULL
       OR expected_location_revision<>l.revision THEN
        RAISE EXCEPTION 'Location policy changed' USING ERRCODE='40001';
    END IF;
    IF operation IS NULL OR operation NOT IN ('recurring','exception','delete')
       OR target_day IS NULL OR NOT isfinite(target_day)
       OR target_day>date '9998-12-31'
       OR expected_revision IS NULL OR expected_revision<0
       OR supplied_windows IS NULL THEN
        RAISE EXCEPTION 'Invalid unavailability request' USING ERRCODE='22023';
    END IF;

    check_time := clock_timestamp();
    IF target_day<(check_time AT TIME ZONE l.timezone)::date THEN
        RAISE EXCEPTION 'Date is locked' USING ERRCODE='55000';
    END IF;

    cutoff_value := scheduling.unavailability_cutoff(target_location,target_day);
    IF check_time>=cutoff_value OR EXISTS (
        SELECT FROM scheduling.unavailability_day_lock
        WHERE employee_id=employee_value AND local_date=target_day
    ) THEN
        RAISE EXCEPTION 'Date is locked' USING ERRCODE='55000';
    END IF;

    IF operation='recurring' THEN
        IF jsonb_typeof(supplied_windows)<>'object' THEN
            RAISE EXCEPTION 'Invalid weekly pattern' USING ERRCODE='22023';
        END IF;
        IF EXISTS (
            SELECT FROM jsonb_object_keys(supplied_windows) k
            WHERE k !~ '^[0-6]$'
        ) THEN
            RAISE EXCEPTION 'Invalid weekday' USING ERRCODE='22023';
        END IF;
    ELSE
        IF jsonb_typeof(supplied_windows)<>'array'
           OR (operation='delete' AND supplied_windows<>'[]'::jsonb) THEN
            RAISE EXCEPTION 'Invalid date replacement' USING ERRCODE='22023';
        END IF;
    END IF;

    FOR group_windows IN
        SELECT value FROM jsonb_each(
            CASE WHEN operation='recurring' THEN supplied_windows
                 ELSE jsonb_build_object('date',supplied_windows) END
        )
    LOOP
        IF jsonb_typeof(group_windows)<>'array' THEN
            RAISE EXCEPTION 'Intervals must be an array' USING ERRCODE='22023';
        END IF;
        IF jsonb_array_length(group_windows)>32 THEN
            RAISE EXCEPTION 'Too many intervals' USING ERRCODE='22023';
        END IF;
        FOR window_value IN SELECT value FROM jsonb_array_elements(group_windows)
        LOOP
            IF jsonb_typeof(window_value)<>'object' THEN
                RAISE EXCEPTION 'Invalid interval' USING ERRCODE='22023';
            END IF;
            IF NOT (window_value ? 'start' AND window_value ? 'end')
               OR EXISTS (
                   SELECT FROM jsonb_object_keys(window_value) k
                   WHERE k NOT IN ('start','end','ends_next_day')
               )
               OR jsonb_typeof(window_value->'start') IS DISTINCT FROM 'number'
               OR jsonb_typeof(window_value->'end') IS DISTINCT FROM 'number'
               OR (window_value->>'start') !~ '^[0-9]{1,4}$'
               OR (window_value->>'end') !~ '^[0-9]{1,4}$'
               OR (
                   window_value ? 'ends_next_day'
                   AND jsonb_typeof(window_value->'ends_next_day')
                       IS DISTINCT FROM 'boolean'
               ) THEN
                RAISE EXCEPTION 'Invalid interval fields' USING ERRCODE='22023';
            END IF;
            start_min := (window_value->>'start')::integer;
            end_min := (window_value->>'end')::integer;
            next_day := coalesce((window_value->>'ends_next_day')::boolean,false);
            end_absolute := end_min+CASE WHEN next_day THEN 1440 ELSE 0 END;
            IF start_min NOT BETWEEN 0 AND 1439
               OR end_min NOT BETWEEN 0 AND 1440
               OR (next_day AND end_min=1440)
               OR end_absolute-start_min NOT BETWEEN 1 AND 1440 THEN
                RAISE EXCEPTION 'Invalid interval duration' USING ERRCODE='22023';
            END IF;
        END LOOP;
    END LOOP;

    IF operation='recurring' THEN
        SELECT coalesce(max(revision),0) INTO current_revision
        FROM scheduling.unavailability_recurring
        WHERE employee_id=employee_value;
        IF current_revision<>expected_revision THEN
            RAISE EXCEPTION 'Pattern changed' USING ERRCODE='40001';
        END IF;

        SELECT to_jsonb(r) INTO previous_state
        FROM scheduling.unavailability_recurring r
        WHERE r.employee_id=employee_value
        ORDER BY r.revision DESC LIMIT 1;

        INSERT INTO scheduling.unavailability_recurring(
            organization_id,location_id,employee_id,revision,
            effective_from,weekly_windows,actor_user_id
        ) VALUES (
            organization_value,target_location,employee_value,current_revision+1,
            target_day,supplied_windows,actor_value
        ) RETURNING to_jsonb(unavailability_recurring) INTO result;
    ELSE
        SELECT to_jsonb(x),x.revision INTO previous_state,current_revision
        FROM scheduling.unavailability_exception x
        WHERE x.employee_id=employee_value AND x.local_date=target_day;
        current_revision := coalesce(current_revision,0);
        IF current_revision<>expected_revision THEN
            RAISE EXCEPTION 'Date replacement changed' USING ERRCODE='40001';
        END IF;

        INSERT INTO scheduling.unavailability_exception(
            organization_id,location_id,employee_id,local_date,revision,
            present,windows,actor_user_id
        ) VALUES (
            organization_value,target_location,employee_value,target_day,
            current_revision+1,operation<>'delete',supplied_windows,actor_value
        )
        ON CONFLICT (employee_id,local_date) DO UPDATE
        SET revision=EXCLUDED.revision,present=EXCLUDED.present,
            windows=EXCLUDED.windows,actor_user_id=EXCLUDED.actor_user_id,
            updated_at=clock_timestamp()
        RETURNING to_jsonb(unavailability_exception) INTO result;
    END IF;

    -- Recheck the actual clock after validation and writes. Raising rolls back
    -- the complete operation if its deadline was crossed while processing.
    IF clock_timestamp()>=cutoff_value THEN
        RAISE EXCEPTION 'Cutoff reached during save' USING ERRCODE='55000';
    END IF;

    INSERT INTO scheduling.audit_event(
        organization_id,location_id,actor_user_id,action,entity_type,
        entity_id,before_data,after_data
    ) VALUES (
        organization_value,target_location,actor_value,
        'unavailability.'||operation,'employee',
        employee_value,previous_state,result
    );
    RETURN result;
END
$$;

REVOKE ALL ON FUNCTION scheduling.write_my_unavailability(
    uuid,text,date,integer,integer,jsonb
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION scheduling.write_my_unavailability(
    uuid,text,date,integer,integer,jsonb
) TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0011_employee_unavailability_writes', :'migration_sha256');
