-- Caller supplies transaction, owner role and migration_sha256.

DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

CREATE TABLE scheduling.auth_session (
    token_sha256 text PRIMARY KEY
        CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    user_id uuid NOT NULL
        REFERENCES scheduling.app_user(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CHECK (expires_at > created_at)
);

CREATE INDEX auth_session_user_idx
    ON scheduling.auth_session(user_id);

CREATE INDEX auth_session_expiry_idx
    ON scheduling.auth_session(expires_at);

GRANT SELECT (id, email, password_hash, active)
    ON scheduling.app_user TO shift_scheduling_app;
GRANT SELECT ON scheduling.organization_member,
    scheduling.member_permission TO shift_scheduling_app;
GRANT SELECT, INSERT ON scheduling.auth_session
    TO shift_scheduling_app;
GRANT UPDATE (revoked_at) ON scheduling.auth_session
    TO shift_scheduling_app;

INSERT INTO scheduling.schema_migration(version, sha256)
VALUES ('0003_authentication_sessions', :'migration_sha256');
