-- Caller supplies transaction, owner role and migration_sha256.

DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

ALTER TABLE scheduling.employee
    ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0);
ALTER TABLE scheduling.job_role
    ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0);
ALTER TABLE scheduling.employee_qualification
    ADD COLUMN revision integer NOT NULL DEFAULT 1 CHECK (revision > 0);

CREATE TABLE scheduling.audit_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    actor_user_id uuid NOT NULL,
    action text NOT NULL CHECK (length(btrim(action)) > 0),
    entity_type text NOT NULL CHECK (
        entity_type IN ('employee', 'job_role', 'employee_qualification')
    ),
    entity_id uuid NOT NULL,
    before_data jsonb,
    after_data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (organization_id, location_id)
        REFERENCES scheduling.location(organization_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, actor_user_id)
        REFERENCES scheduling.organization_member(organization_id, user_id)
        ON DELETE RESTRICT
);

CREATE INDEX audit_event_location_time_idx
    ON scheduling.audit_event(location_id, created_at DESC, id);

ALTER TABLE scheduling.audit_event ENABLE ROW LEVEL SECURITY;

CREATE POLICY staff_audit_read ON scheduling.audit_event
    FOR SELECT TO shift_scheduling_app
    USING (scheduling.staff_read_allowed(organization_id));

CREATE POLICY staff_audit_insert ON scheduling.audit_event
    FOR INSERT TO shift_scheduling_app
    WITH CHECK (
        scheduling.staff_read_allowed(organization_id)
        AND actor_user_id = (
            SELECT s.user_id FROM scheduling.auth_session s
            WHERE s.token_sha256 =
                current_setting('scheduling.session_sha256', true)
        )
    );

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'employee', 'job_role', 'employee_qualification'
    ]
    LOOP
        EXECUTE format(
            'CREATE POLICY staff_insert ON scheduling.%I
             FOR INSERT TO shift_scheduling_app
             WITH CHECK (scheduling.staff_read_allowed(organization_id))',
            table_name
        );
        EXECUTE format(
            'CREATE POLICY staff_update ON scheduling.%I
             FOR UPDATE TO shift_scheduling_app
             USING (scheduling.staff_read_allowed(organization_id))
             WITH CHECK (scheduling.staff_read_allowed(organization_id))',
            table_name
        );
    END LOOP;
END
$$;

GRANT INSERT (organization_id, location_id, display_name)
    ON scheduling.employee TO shift_scheduling_app;
GRANT UPDATE (display_name, active, revision)
    ON scheduling.employee TO shift_scheduling_app;

GRANT INSERT (organization_id, location_id, name)
    ON scheduling.job_role TO shift_scheduling_app;
GRANT UPDATE (name, active, revision)
    ON scheduling.job_role TO shift_scheduling_app;

GRANT INSERT (
    organization_id, location_id, employee_id, job_role_id, active
) ON scheduling.employee_qualification TO shift_scheduling_app;
GRANT UPDATE (active, revision)
    ON scheduling.employee_qualification TO shift_scheduling_app;

GRANT SELECT ON scheduling.audit_event TO shift_scheduling_app;
GRANT INSERT (
    organization_id, location_id, actor_user_id, action,
    entity_type, entity_id, before_data, after_data
) ON scheduling.audit_event TO shift_scheduling_app;

-- No DELETE grants; deactivate staff records to retain history.
-- No UPDATE or DELETE grants on the audit table.
-- Staff writes and their audit insert must share one application transaction.

INSERT INTO scheduling.schema_migration(version, sha256)
VALUES ('0005_staff_write_foundation', :'migration_sha256');
