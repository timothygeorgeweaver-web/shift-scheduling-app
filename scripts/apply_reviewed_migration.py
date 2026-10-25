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
        "0009_availability_policy", "0010_private_unavailability_storage",
        "0011_employee_unavailability_writes",
        "0012_date_planner_storage", "0013_planner_bulk_scope",
        "0014_retire_legacy_unavailability", "0015_draft_schedule_weeks",
        "0016_draft_shift_slots", "0017_edit_remove_draft_shifts",
        "0018_job_role_display_order", "0019_schedule_templates",
        "0020_manage_schedule_templates", "0021_direct_template_patterns",
        "0022_business_day_storage", "0023_business_day_policy",
        "0024_business_week_shift_boundaries",
        "0025_capture_business_day_policy",
        "0026_remove_draft_schedule_weeks",
        "0027_template_destination_scope",
        "0028_employee_rollover_profile",
        "0029_business_day_dst_guards",
        "0030_assignment_storage",
        "0031_assignment_candidate_inputs",
        "0032_assignment_rest_policy"
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
        "0011_employee_unavailability_writes": (set(), []),
        "0012_date_planner_storage": (set(), []),
        "0013_planner_bulk_scope": (set(), []),
        "0014_retire_legacy_unavailability": (set(), []),
        "0015_draft_schedule_weeks": (set(), []),
        "0016_draft_shift_slots": (set(), []),
        "0017_edit_remove_draft_shifts": (set(), []),
        "0018_job_role_display_order": ({"job_role"}, ["display_order"]),
        "0019_schedule_templates": (set(), []),
        "0020_manage_schedule_templates": (set(), []),
        "0021_direct_template_patterns": (set(), []),
        "0023_business_day_policy": (set(), []),
        "0024_business_week_shift_boundaries": (set(), []),
        "0025_capture_business_day_policy": (set(), []),
        "0026_remove_draft_schedule_weeks": ({"schedule_week"}, ["deleted_at"]),
        "0027_template_destination_scope": (set(), []),
        "0028_employee_rollover_profile": (set(), []),
        "0029_business_day_dst_guards": (set(), []),
        "0030_assignment_storage": (set(), []),
        "0031_assignment_candidate_inputs": (set(), []),
        "0032_assignment_rest_policy": ({"location"}, [
            "rest_window_start_minute", "rest_window_end_minute",
            "minimum_rest_minutes",
        ]),
        "0022_business_day_storage": (
            {"location", "schedule_week", "schedule_template"},
            ["business_day_start_minute"],
        ),
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
    if version == "0012_date_planner_storage":
        require(set(after["data"])-set(before["data"]) == {"unavailability_date"},
                "Unexpected planner tables.")
        require(after["data"]["unavailability_date"]["rows"] == 0,
                "Migration seeded planner data.")
    if version == "0015_draft_schedule_weeks":
        require(set(after["data"])-set(before["data"]) == {"schedule_week"},
                "Unexpected schedule tables.")
        require(after["data"]["schedule_week"]["rows"] == 0,
                "Migration seeded schedule weeks.")
    if version == "0016_draft_shift_slots":
        require(set(after["data"])-set(before["data"]) == {"shift_slot"},
                "Unexpected shift tables.")
        require(after["data"]["shift_slot"]["rows"] == 0,
                "Migration seeded real shifts.")
    if version == "0019_schedule_templates":
        require(set(after["data"])-set(before["data"]) == {"schedule_template"},
                "Unexpected template tables.")
        require(after["data"]["schedule_template"]["rows"] == 0,
                "Migration seeded real templates.")
    if version == "0030_assignment_storage":
        names = {"shift_assignment", "assignment_override"}
        require(set(after["data"])-set(before["data"]) == names,
                "Unexpected assignment tables.")
        require(all(after["data"][name]["rows"] == 0 for name in names),
                "Migration seeded employee assignments.")
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
        "0032_assignment_rest_policy":
            "scheduling.read_assignment_inputs(uuid,uuid)",
        "0031_assignment_candidate_inputs":
            "scheduling.read_assignment_inputs(uuid,uuid)",
        "0007_account_access_management":
            "scheduling.employee_account_access(uuid,uuid,jsonb)",
        "0008_location_settings":
            "scheduling.edit_location_settings(uuid,integer,integer,text,text,text)",
        "0009_availability_policy":
            "scheduling.edit_availability_policy(uuid,integer,integer,integer,text)",
        "0010_private_unavailability_storage":
            "scheduling.read_my_unavailability(uuid,date,integer)",
        "0011_employee_unavailability_writes":
            "scheduling.write_my_unavailability(uuid,text,date,integer,integer,jsonb)",
        "0012_date_planner_storage":
            "scheduling.save_my_planner(uuid,date,integer,jsonb)",
        "0013_planner_bulk_scope":
            "scheduling.lock_my_planner_scope(uuid)",
        "0014_retire_legacy_unavailability":
            "scheduling.lock_my_planner_scope(uuid)",
        "0015_draft_schedule_weeks":
            "scheduling.create_blank_schedule_week(uuid,date,integer)",
        "0016_draft_shift_slots":
            "scheduling.create_draft_shift(uuid,uuid,integer,timestamptz,timestamptz,text)",
        "0017_edit_remove_draft_shifts":
            "scheduling.change_draft_shift(uuid,uuid,integer,boolean,uuid,integer,timestamptz,timestamptz,text)",
        "0018_job_role_display_order":
            "scheduling.change_draft_shift(uuid,uuid,integer,boolean,uuid,integer,timestamptz,timestamptz,text)",
        "0019_schedule_templates":
            "scheduling.save_schedule_template(uuid,uuid,integer,text)",
        "0020_manage_schedule_templates":
            "scheduling.change_schedule_template(uuid,integer,text,boolean,uuid,integer)",
        "0021_direct_template_patterns":
            "scheduling.save_template_pattern(uuid,uuid,integer,integer,text,jsonb)",
        "0022_business_day_storage":
            "scheduling.save_template_pattern(uuid,uuid,integer,integer,text,jsonb)",
        "0023_business_day_policy":
            "scheduling.edit_business_day_policy(uuid,integer,integer)",
        "0024_business_week_shift_boundaries":
            "scheduling.create_draft_shift(uuid,uuid,integer,timestamptz,timestamptz,text)",
        "0025_capture_business_day_policy":
            "scheduling.save_template_pattern(uuid,uuid,integer,integer,text,jsonb)",
        "0026_remove_draft_schedule_weeks":
            "scheduling.remove_draft_schedule_week(uuid,uuid,integer)",
        "0027_template_destination_scope":
            "scheduling.lock_template_destination(uuid,uuid,integer,integer)",
        "0028_employee_rollover_profile":
            "scheduling.my_unavailability_profiles()",
        "0029_business_day_dst_guards":
            "scheduling.create_blank_schedule_week(uuid,date,integer)",
        "0030_assignment_storage":
            "scheduling.lock_template_destination(uuid,uuid,integer,integer)",
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
