DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.shift_assignment (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    shift_slot_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
    active boolean NOT NULL DEFAULT true,
    created_by uuid NOT NULL,
    updated_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (organization_id,location_id,id),
    FOREIGN KEY (organization_id,location_id,shift_slot_id)
        REFERENCES scheduling.shift_slot(organization_id,location_id,id),
    FOREIGN KEY (organization_id,location_id,employee_id)
        REFERENCES scheduling.employee(organization_id,location_id,id),
    FOREIGN KEY (organization_id,created_by)
        REFERENCES scheduling.organization_member(organization_id,user_id),
    FOREIGN KEY (organization_id,updated_by)
        REFERENCES scheduling.organization_member(organization_id,user_id)
);

CREATE UNIQUE INDEX shift_assignment_one_active_slot
ON scheduling.shift_assignment(shift_slot_id) WHERE active;

CREATE INDEX shift_assignment_employee_active
ON scheduling.shift_assignment(employee_id,shift_slot_id) WHERE active;

CREATE TABLE scheduling.assignment_override (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    assignment_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    assignment_revision integer NOT NULL CHECK (assignment_revision>0),
    rule_code text NOT NULL
        CHECK (rule_code IN ('unavailability','daily_hours')),
    reason text NOT NULL CHECK (
        reason=btrim(reason)
        AND length(reason) BETWEEN 1 AND 500
        AND reason !~ '^[[:space:]]*$'
    ),
    policy jsonb NOT NULL CHECK (jsonb_typeof(policy)='object'),
    rule_details jsonb NOT NULL CHECK (jsonb_typeof(rule_details)='object'),
    approved_by uuid NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (assignment_id,assignment_revision,rule_code),
    FOREIGN KEY (organization_id,location_id,assignment_id)
        REFERENCES scheduling.shift_assignment(organization_id,location_id,id),
    FOREIGN KEY (organization_id,location_id,employee_id)
        REFERENCES scheduling.employee(organization_id,location_id,id),
    FOREIGN KEY (organization_id,approved_by)
        REFERENCES scheduling.organization_member(organization_id,user_id)
);

ALTER TABLE scheduling.shift_assignment ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduling.assignment_override ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON scheduling.shift_assignment,
    scheduling.assignment_override
FROM PUBLIC,shift_scheduling_app;

CREATE FUNCTION scheduling.preserve_assignment_override()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'Assignment approval history is immutable'
        USING ERRCODE='55000';
END
$$;

REVOKE ALL ON FUNCTION scheduling.preserve_assignment_override()
FROM PUBLIC,shift_scheduling_app;

CREATE TRIGGER assignment_override_immutable
BEFORE UPDATE OR DELETE ON scheduling.assignment_override
FOR EACH ROW EXECUTE FUNCTION scheduling.preserve_assignment_override();

-- Runtime access will be introduced only through the protected assignment
-- service, with fresh eligibility checks and atomic audit writes.
INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0030_assignment_storage', :'migration_sha256');
