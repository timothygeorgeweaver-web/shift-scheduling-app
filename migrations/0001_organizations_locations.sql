-- Caller supplies a transaction, owner role and migration_sha256.
-- Run only against the dedicated scheduling database.

DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.schema_migration (
    version text PRIMARY KEY,
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT now(),
    applied_by text NOT NULL DEFAULT session_user
);

CREATE TABLE scheduling.organization (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE scheduling.location (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL
        REFERENCES scheduling.organization(id) ON DELETE RESTRICT,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    timezone text NOT NULL CHECK (length(btrim(timezone)) > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, id)
);

CREATE INDEX location_organization_idx
    ON scheduling.location (organization_id);

-- No application access grants yet. Add them alongside authorized APIs.

INSERT INTO scheduling.schema_migration (version, sha256)
VALUES ('0001_organizations_locations', :'migration_sha256');
