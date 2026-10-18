-- Caller supplies transaction, owner role and migration_sha256.

DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.app_user (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email text NOT NULL UNIQUE
        CHECK (email = lower(btrim(email)) AND length(email) > 0),
    password_hash text
        CHECK (password_hash IS NULL OR length(password_hash) > 0),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE scheduling.organization_member (
    organization_id uuid NOT NULL
        REFERENCES scheduling.organization(id) ON DELETE RESTRICT,
    user_id uuid NOT NULL
        REFERENCES scheduling.app_user(id) ON DELETE RESTRICT,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, user_id)
);

CREATE TABLE scheduling.member_permission (
    organization_id uuid NOT NULL,
    user_id uuid NOT NULL,
    permission text NOT NULL
        CHECK (permission IN ('administrator', 'scheduler', 'employee')),
    PRIMARY KEY (organization_id, user_id, permission),
    FOREIGN KEY (organization_id, user_id)
        REFERENCES scheduling.organization_member(organization_id, user_id)
        ON DELETE RESTRICT
);

CREATE TABLE scheduling.employee (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    user_id uuid,
    display_name text NOT NULL CHECK (length(btrim(display_name)) > 0),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (organization_id, location_id)
        REFERENCES scheduling.location(organization_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, user_id)
        REFERENCES scheduling.organization_member(organization_id, user_id)
        ON DELETE RESTRICT,
    UNIQUE (organization_id, location_id, id),
    UNIQUE (organization_id, location_id, user_id)
);

CREATE TABLE scheduling.job_role (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (organization_id, location_id)
        REFERENCES scheduling.location(organization_id, id)
        ON DELETE RESTRICT,
    UNIQUE (organization_id, location_id, id)
);

CREATE TABLE scheduling.employee_qualification (
    organization_id uuid NOT NULL,
    location_id uuid NOT NULL,
    employee_id uuid NOT NULL,
    job_role_id uuid NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, location_id, employee_id, job_role_id),
    FOREIGN KEY (organization_id, location_id, employee_id)
        REFERENCES scheduling.employee(organization_id, location_id, id)
        ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, location_id, job_role_id)
        REFERENCES scheduling.job_role(organization_id, location_id, id)
        ON DELETE RESTRICT
);

CREATE INDEX organization_member_user_idx
    ON scheduling.organization_member(user_id);

CREATE INDEX employee_user_idx ON scheduling.employee(user_id);

CREATE INDEX qualification_role_idx
    ON scheduling.employee_qualification(
        organization_id, location_id, job_role_id
    );

-- No application grants until authenticated, authorized access is implemented.

INSERT INTO scheduling.schema_migration(version, sha256)
VALUES ('0002_accounts_employees', :'migration_sha256');
