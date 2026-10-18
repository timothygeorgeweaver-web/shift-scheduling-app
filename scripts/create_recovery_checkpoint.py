"""Create and independently restore a local scheduling-app checkpoint."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "shift_scheduling"
CLEAN_ENV = {"PATH": os.defpath, "LANG": "C.UTF-8"}


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def admin(statement, database="postgres"):
    result = subprocess.run(
        [
            "sudo", "-n", "-u", "postgres", "psql", "-X", "-w",
            "-h", "/var/run/postgresql", "-p", "5432",
            "-d", database, "-v", "ON_ERROR_STOP=1", "-A", "-t", "-q",
        ],
        input=statement,
        text=True,
        capture_output=True,
        env=CLEAN_ENV,
        timeout=60,
    )
    require(
        result.returncode == 0,
        "Database checkpoint command failed. Preserve the checkpoint and "
        "recovery database; return this output.",
    )
    return result.stdout.strip()


def fingerprint(database):
    tables = json.loads(admin("""
        BEGIN READ ONLY;
        SELECT json_agg(
            json_build_object('name', tablename, 'owner', tableowner)
            ORDER BY tablename
        )
        FROM pg_tables WHERE schemaname = 'scheduling';
        COMMIT;
    """, database))

    require(bool(tables), "Scheduling tables were not found.")
    data = {}
    privileges = {}
    for table in tables:
        name = table["name"]
        require(
            name.replace("_", "").isalnum(),
            "Unexpected table identifier; inspect before continuing.",
        )
        data[name] = json.loads(admin(f"""
            BEGIN READ ONLY;
            SELECT json_build_object(
                'rows', count(*),
                'fingerprint', md5(coalesce(
                    string_agg(
                        to_jsonb(t)::text, E'\\n'
                        ORDER BY to_jsonb(t)::text COLLATE "C"
                    ), ''
                ))
            )
            FROM scheduling."{name}" t;
            COMMIT;
        """, database))

        privileges[name] = json.loads(admin(f"""
            BEGIN READ ONLY;
            SELECT json_build_array(
                has_table_privilege('shift_scheduling_app',
                    'scheduling.{name}', 'SELECT'),
                has_table_privilege('shift_scheduling_app',
                    'scheduling.{name}', 'INSERT'),
                has_table_privilege('shift_scheduling_app',
                    'scheduling.{name}', 'UPDATE'),
                has_table_privilege('shift_scheduling_app',
                    'scheduling.{name}', 'DELETE')
            );
            COMMIT;
        """, database))

    structure = json.loads(admin("""
        BEGIN READ ONLY;
        SELECT json_build_object(
            'schema_owner', (
                SELECT pg_get_userbyid(nspowner)
                FROM pg_namespace WHERE nspname = 'scheduling'
            ),
            'schema_usage', has_schema_privilege(
                'shift_scheduling_app', 'scheduling', 'USAGE'
            ),
            'schema_create', has_schema_privilege(
                'shift_scheduling_app', 'scheduling', 'CREATE'
            ),
            'columns', (
                SELECT json_agg(
                    json_build_array(
                        table_name, column_name, data_type,
                        udt_name, is_nullable, column_default,
                        has_column_privilege(
                            'shift_scheduling_app',
                            format('%I.%I', table_schema, table_name),
                            column_name, 'SELECT'
                        ),
                        has_column_privilege(
                            'shift_scheduling_app',
                            format('%I.%I', table_schema, table_name),
                            column_name, 'UPDATE'
                        )
                    ) ORDER BY table_name, ordinal_position
                )
                FROM information_schema.columns
                WHERE table_schema = 'scheduling'
            ),
            'constraints', (
                SELECT json_agg(
                    json_build_array(
                        c.relname, k.conname, k.contype,
                        k.convalidated, pg_get_constraintdef(k.oid)
                    ) ORDER BY c.relname, k.conname
                )
                FROM pg_constraint k
                JOIN pg_class c ON c.oid = k.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'scheduling'
            ),
            'indexes', (
                SELECT json_agg(
                    json_build_array(tablename, indexname, indexdef)
                    ORDER BY tablename, indexname
                )
                FROM pg_indexes WHERE schemaname = 'scheduling'
            )
        );
        COMMIT;
    """, database))

    return {
        "tables": tables,
        "data": data,
        "table_privileges": privileges,
        "structure": structure,
    }


def main():
    os.umask(0o077)
    require((ROOT / ".env").is_file(), "Local configuration is missing.")

    active = admin("""
        SELECT count(*) FROM pg_stat_activity
        WHERE datname = 'shift_scheduling';
    """)
    require(active == "0", "Stop the app and other scheduling DB connections first.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = (
        Path.home() / "shift-scheduling-evidence" / "checkpoints" / stamp
    )
    destination.mkdir(parents=True, mode=0o700)
    destination.chmod(0o700)
    recovery_database = "shift_scheduling_restore_" + stamp.lower()

    print("CHECKPOINT_DIRECTORY=" + str(destination), flush=True)
    print("RECOVERY_DATABASE=" + recovery_database, flush=True)

    before = fingerprint(SOURCE)
    require(
        len(before["tables"]) == 10
        and before["data"]["schema_migration"]["rows"] == 3,
        "Unexpected baseline schema; preserve evidence and inspect.",
    )

    archive = destination / "database.dump"
    with archive.open("xb") as stream:
        result = subprocess.run(
            [
                "sudo", "-n", "-u", "postgres", "pg_dump",
                "-h", "/var/run/postgresql", "-p", "5432",
                "--format=custom", "--dbname=" + SOURCE,
            ],
            stdout=stream,
            stderr=subprocess.PIPE,
            env=CLEAN_ENV,
            timeout=120,
        )
    require(result.returncode == 0, "Database dump failed.")
    require(archive.stat().st_size > 0, "Database dump is empty.")

    shutil.copyfile(ROOT / ".env", destination / "configuration.env")
    (destination / "configuration.env").chmod(0o600)
    shutil.copyfile(__file__, destination / "create_recovery_checkpoint.py")

    code_archive = destination / "repository.bundle"
    subprocess.run(
        ["git", "bundle", "create", str(code_archive), "HEAD"],
        cwd=ROOT, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "bundle", "verify", str(code_archive)],
        cwd=ROOT, check=True, capture_output=True,
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()

    admin(f"""
        CREATE DATABASE "{recovery_database}"
          OWNER shift_scheduling_owner TEMPLATE template0;
    """)
    admin(f"""
        REVOKE ALL ON DATABASE "{recovery_database}" FROM PUBLIC;
    """)

    # Open the restricted archive as the current OS user and pass its
    # descriptor to pg_restore; postgres need not read the checkpoint folder.
    with archive.open("rb") as stream:
        restored = subprocess.run(
            [
                "sudo", "-n", "-u", "postgres", "pg_restore",
                "-h", "/var/run/postgresql", "-p", "5432",
                "--exit-on-error", "--single-transaction",
                "--dbname=" + recovery_database,
            ],
            stdin=stream,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=CLEAN_ENV,
            timeout=120,
        )
    require(restored.returncode == 0, "Independent restore failed.")

    recovered = fingerprint(recovery_database)
    after = fingerprint(SOURCE)

    for name, value in [
        ("source_before.json", before),
        ("restored.json", recovered),
        ("source_after.json", after),
    ]:
        (destination / name).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n"
        )

    require(before == after, "Source database changed during the checkpoint.")
    require(before == recovered, "Restored data/schema/privileges differ.")

    admin(f"""
        ALTER DATABASE "{recovery_database}"
          SET default_transaction_read_only = on;
    """)

    report = {
        "result": "PASS",
        "source_commit": head,
        "source_database": SOURCE,
        "recovery_database": recovery_database,
        "table_count": len(before["tables"]),
        "database_dump_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "python_version": sys.version,
        "verified": [
            "independent_restore",
            "table_data_fingerprints",
            "table_ownership",
            "columns_constraints_indexes",
            "application_table_and_column_privileges",
            "source_database_unchanged",
            "git_bundle_integrity",
        ],
    }
    (destination / "checkpoint.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )

    print("INDEPENDENT_RESTORE=PASS")
    print("DATA_SCHEMA_AND_PRIVILEGES=PASS")
    print("SOURCE_DATABASE_UNCHANGED=PASS")
    print("CODE_BUNDLE_INTEGRITY=PASS")
    print("RECOVERY_DATABASE_DEFAULT=READ_ONLY")
    print("CHECKPOINT_RESULT=PASS")


if __name__ == "__main__":
    main()
