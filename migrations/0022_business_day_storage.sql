DO $$
BEGIN
    IF current_database() <> 'shift_scheduling'
       OR current_user <> 'shift_scheduling_owner' THEN
        RAISE EXCEPTION 'Unexpected migration database or role';
    END IF;
END
$$;

-- Proposed location policy. Planner activation follows in later migrations.
ALTER TABLE scheduling.location
ADD COLUMN business_day_start_minute integer NOT NULL DEFAULT 300
CHECK (
    business_day_start_minute BETWEEN 0 AND 1439
    AND business_day_start_minute % 15 = 0
);

-- Preserve midnight-based semantics for every existing saved week/template.
-- Defaults remain zero until creation/copy/apply functions are upgraded.
ALTER TABLE scheduling.schedule_week
ADD COLUMN business_day_start_minute integer NOT NULL DEFAULT 0
CHECK (
    business_day_start_minute BETWEEN 0 AND 1439
    AND business_day_start_minute % 15 = 0
);

ALTER TABLE scheduling.schedule_template
ADD COLUMN business_day_start_minute integer NOT NULL DEFAULT 0
CHECK (
    business_day_start_minute BETWEEN 0 AND 1439
    AND business_day_start_minute % 15 = 0
);

INSERT INTO scheduling.schema_migration(version,sha256)
VALUES ('0022_business_day_storage', :'migration_sha256');
