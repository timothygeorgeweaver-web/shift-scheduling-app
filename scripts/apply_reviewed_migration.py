"""Reviewed migrations 0007/0008, supporting already-migrated test copies."""

import hashlib
import json
from pathlib import Path
import re

from scripts.create_recovery_checkpoint import admin, fingerprint

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def install(version, database, rehearse=False):
    require(version in (
        "0007_account_access_management", "0008_location_settings",
        "0009_availability_policy", "0010_private_unavailability_storage"
    ), "Unsupported migration.")
    require(
        database == "shift_scheduling"
        or re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", database),
        "Unexpected migration target.",
    )
    paths = sorted((ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
    manifest = {
        path.stem: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    recorded = json.loads(admin(
        "SELECT json_object_agg(version,sha256) FROM scheduling.schema_migration;",
        database,
    ))
    require(all(
        name in manifest and manifest[name] == digest
        for name, digest in recorded.items()
    ), "Applied migration differs from repository files.")
    require(all(
        name in recorded for name in manifest if name < version
    ), "A preceding migration is missing.")
    if version in recorded:
        return
    require(not any(name > version for name in recorded),
            "Cannot insert a migration before an applied later migration.")

    path = ROOT / "migrations" / (version + ".sql")
    source = path.read_text()
    guard = "current_database() <> 'shift_scheduling'"
    require(source.count(guard) == 1, "Unexpected database guard.")
    require(source.count(":'migration_sha256'") == 1, "Unexpected checksum marker.")
    source = source.replace(guard, f"current_database() <> '{database}'", 1)
    source = source.replace(":'migration_sha256'", "'" + manifest[version] + "'")
    prefix = (
        "BEGIN; SET LOCAL lock_timeout='5s'; "
        "SET LOCAL statement_timeout='20s'; "
        "SET LOCAL ROLE shift_scheduling_owner;\n"
    )

    stripped, removed_keys = {
        "0007_account_access_management": ({"organization_member"}, ["revision"]),
        "0008_location_settings": ({"organization", "location"}, ["revision"]),
        "0009_availability_policy": ({"location"}, [
            "week_start", "availability_cutoff_days", "availability_cutoff_time"
        ]),
        "0010_private_unavailability_storage": (set(), []),
    }[version]
    normalized_expression = "to_jsonb(t)-ARRAY[" + ",".join(
        "'" + key + "'" for key in removed_keys
    ) + "]::text[]"
    before = fingerprint(database)
    normalized = {}
    for table in stripped:
        normalized[table] = admin(f"""
            SELECT md5(coalesce(string_agg(
              ({normalized_expression})::text,E'\\n'
              ORDER BY ({normalized_expression})::text COLLATE "C"
            ),'')) FROM scheduling.{table} t;
        """, database)

    if rehearse:
        admin(prefix + source + "\nROLLBACK;", database)
        require(fingerprint(database) == before,
                "Migration rollback changed database state.")
        print("MIGRATION_ROLLBACK_REHEARSAL=PASS")

    admin(prefix + source + "\nCOMMIT;", database)
    after = fingerprint(database)
    if version == "0010_private_unavailability_storage":
        new_tables = {
            "unavailability_recurring", "unavailability_exception",
            "unavailability_day_lock",
        }
        require(set(after["data"])-set(before["data"]) == new_tables,
                "Unexpected private-storage tables.")
        require(all(after["data"][name]["rows"] == 0 for name in new_tables),
                "Migration unexpectedly created employee records.")
    for table, value in before["data"].items():
        if table not in stripped | {"schema_migration"}:
            require(after["data"][table] == value,
                    "Unexpected business data change.")
    for table, expected in normalized.items():
        actual = admin(f"""
            SELECT md5(coalesce(string_agg(
              ({normalized_expression})::text,E'\\n'
              ORDER BY ({normalized_expression})::text COLLATE "C"
            ),'')) FROM scheduling.{table} t;
        """, database)
        require(actual == expected, "Existing records changed during migration.")

    signature = {
        "0007_account_access_management":
            "scheduling.employee_account_access(uuid,uuid,jsonb)",
        "0008_location_settings":
            "scheduling.edit_location_settings(uuid,integer,integer,text,text,text)",
        "0009_availability_policy":
            "scheduling.edit_availability_policy(uuid,integer,integer,integer,text)",
        "0010_private_unavailability_storage":
            "scheduling.read_my_unavailability(uuid,date,integer)",
    }[version]
    require(admin(f"""
        SELECT p.prosecdef
          AND pg_get_userbyid(p.proowner)='shift_scheduling_owner'
          AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
          AND has_function_privilege('shift_scheduling_app',p.oid,'EXECUTE')
          AND NOT EXISTS (
            SELECT FROM aclexplode(
              coalesce(p.proacl,acldefault('f',p.proowner))
            ) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE'
          )
        FROM pg_proc p WHERE p.oid='{signature}'::regprocedure;
    """, database) == "t", "Unexpected function security.")
    require(admin(
        f"SELECT sha256 FROM scheduling.schema_migration WHERE version='{version}';",
        database,
    ) == manifest[version], "Committed migration checksum differs.")
    print("REVIEWED_MIGRATION_AND_FUNCTION_SECURITY=PASS")
    print("EXISTING_BUSINESS_RECORDS=UNCHANGED")
