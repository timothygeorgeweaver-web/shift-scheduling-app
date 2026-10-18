"""Apply the reviewed access migration to the local DB or a test clone."""

import hashlib
import json
from pathlib import Path
import re

from scripts.create_recovery_checkpoint import admin, fingerprint

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0007_account_access_management"


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def install(database, rehearse=False):
    require(
        database == "shift_scheduling"
        or re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", database),
        "Unexpected migration target.",
    )
    path = ROOT / "migrations" / (VERSION + ".sql")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    recorded = json.loads(admin(
        "SELECT json_object_agg(version,sha256) "
        "FROM scheduling.schema_migration;", database,
    ))
    paths = sorted((ROOT / "migrations").glob("000[1-6]_*.sql"))
    require(len(paths) == 6, "Expected six preceding migrations.")
    expected = {
        item.stem: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in paths
    }
    if VERSION in recorded:
        require(recorded.pop(VERSION) == checksum, "Access migration checksum differs.")
        require(recorded == expected, "Unexpected preceding migrations.")
        return

    require(recorded == expected, "Unexpected migration state.")
    source = path.read_text()
    guard = "current_database() <> 'shift_scheduling'"
    require(source.count(guard) == 1, "Unexpected database guard.")
    source = source.replace(guard, f"current_database() <> '{database}'", 1)
    require(source.count(":'migration_sha256'") == 1, "Unexpected checksum marker.")
    source = source.replace(":'migration_sha256'", "'" + checksum + "'")
    prefix = (
        "BEGIN; SET LOCAL lock_timeout='5s'; "
        "SET LOCAL statement_timeout='20s'; "
        "SET LOCAL ROLE shift_scheduling_owner;\n"
    )

    before = fingerprint(database)
    members_sql = """
        SELECT md5(coalesce(string_agg(
          (to_jsonb(m)-'revision')::text, E'\\n'
          ORDER BY (to_jsonb(m)-'revision')::text COLLATE "C"
        ),'')) FROM scheduling.organization_member m;
    """
    members_before = admin(members_sql, database)

    if rehearse:
        admin(prefix + source + "\nROLLBACK;", database)
        require(fingerprint(database) == before, "Migration rollback changed state.")
        print("ACCOUNT_ACCESS_ROLLBACK_REHEARSAL=PASS")

    admin(prefix + source + "\nCOMMIT;", database)
    after = fingerprint(database)
    for table, value in before["data"].items():
        if table not in ("schema_migration", "organization_member"):
            require(after["data"][table] == value, "Unexpected business data change.")
    require(admin(members_sql, database) == members_before,
            "Existing business memberships changed.")

    security = admin("""
        SELECT p.prosecdef
          AND pg_get_userbyid(p.proowner)='shift_scheduling_owner'
          AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
          AND has_function_privilege('shift_scheduling_app',p.oid,'EXECUTE')
          AND NOT EXISTS (
            SELECT FROM aclexplode(
              coalesce(p.proacl,acldefault('f',p.proowner))
            ) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE'
          )
        FROM pg_proc p
        WHERE p.oid=
          'scheduling.employee_account_access(uuid,uuid,jsonb)'::regprocedure;
    """, database)
    require(security == "t", "Unexpected account function security.")
    require(admin(
        f"SELECT sha256 FROM scheduling.schema_migration WHERE version='{VERSION}';",
        database,
    ) == checksum, "Committed migration checksum differs.")
    print("ACCOUNT_ACCESS_MIGRATION_AND_SECURITY=PASS")
    print("EXISTING_BUSINESS_RECORDS=UNCHANGED")
