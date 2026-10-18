"""Employee-account certification in the disposable integration database."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import re
import secrets
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.auth_store import create_session, identity, revoke_session
from app.database import database_url
from app.security import hash_password
from scripts.create_recovery_checkpoint import admin, fingerprint


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def literal(value):
    return sql.Literal(value).as_string()


def verify(test_db, test_dsn, identities, locations):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(test_dsn).get("dbname") == test_db
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Account certification must use the disposable database.",
    )

    path = Path(__file__).resolve().parents[1] / (
        "migrations/0006_employee_account_creation.sql"
    )
    source = path.read_text()
    guard = "current_database() <> 'shift_scheduling'"
    require(source.count(guard) == 1, "Unexpected migration database guard.")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    # Adapt only the explicit database guard for this uniquely named clone.
    migration = source.replace(
        guard, "current_database() <> " + literal(test_db), 1
    ).replace(":'migration_sha256'", literal(checksum))

    admin(
        "BEGIN; SET LOCAL ROLE shift_scheduling_owner;\n"
        + migration + "\nCOMMIT;",
        test_db,
    )

    location = str(uuid4())
    employees = {key: str(uuid4()) for key in (
        "success", "duplicate", "stale", "concurrent", "audit"
    )}
    statements = [
        "BEGIN; SET LOCAL ROLE shift_scheduling_owner;",
        "INSERT INTO scheduling.location(id,organization_id,name,timezone) "
        f"SELECT {literal(location)},organization_id,'Account certification',"
        f"'America/Los_Angeles' FROM scheduling.location "
        f"WHERE id={literal(locations['a'])};",
    ]
    for key, employee in employees.items():
        statements.append(
            "INSERT INTO scheduling.employee("
            "id,organization_id,location_id,display_name) "
            f"SELECT {literal(employee)},organization_id,id,"
            f"{literal('Account fixture ' + key)} "
            f"FROM scheduling.location WHERE id={literal(location)};"
        )
    statements.append("COMMIT;")
    admin("\n".join(statements), test_db)

    password = secrets.token_urlsafe(24)
    password_hash = hash_password(password)
    email = uuid4().hex + "@example.invalid"

    def invoke(employee, digest, address=email, revision=1):
        try:
            with psycopg.connect(test_dsn) as conn:
                conn.execute(
                    "SELECT set_config('scheduling.session_sha256',%s,true)",
                    (digest,),
                )
                row = conn.execute(
                    "SELECT scheduling.create_employee_account(%s,%s,%s,%s,%s)",
                    (location, employee, revision, address, password_hash),
                ).fetchone()[0]
            return row
        except psycopg.Error as error:
            # Do not print exception details containing account inputs.
            return error.sqlstate

    admin_digest = identities["admin_a"]["digest"]
    before = fingerprint(test_db)
    for label in ("scheduler_a", "employee_a", "admin_b"):
        require(
            invoke(employees["success"], identities[label]["digest"]) == "42501",
            "Unauthorized account creation was accepted.",
        )
    require(
        invoke(employees["success"], "") == "42501",
        "Account creation without session context was accepted.",
    )
    require(fingerprint(test_db) == before, "Denied creation changed data.")

    require(
        invoke(employees["stale"], admin_digest, revision=999) == "40001",
        "Stale employee revision was accepted.",
    )
    require(fingerprint(test_db) == before, "Stale creation changed data.")

    created = invoke(employees["success"], admin_digest)
    require(isinstance(created, dict), "Account creation failed.")
    require(
        set(created) == {"employee_id", "user_id", "email", "revision"}
        and created["employee_id"] == employees["success"]
        and created["email"] == email and created["revision"] == 2,
        "Unexpected account creation response.",
    )

    token = create_session(email, password)
    require(token is not None, "New employee could not sign in.")
    account = identity(token)
    require(
        account is not None
        and account["user_id"] == created["user_id"]
        and len(account["memberships"]) == 1
        and account["memberships"][0]["permissions"] == ["employee"],
        "New account received incorrect access.",
    )
    revoke_session(token)
    require(identity(token) is None, "New employee sign-out failed.")

    before = fingerprint(test_db)
    require(
        invoke(employees["duplicate"], admin_digest) == "23505",
        "Duplicate account email was accepted.",
    )
    require(
        invoke(employees["success"], admin_digest, revision=2) == "40001",
        "Already-linked employee was accepted.",
    )
    require(fingerprint(test_db) == before, "Rejected creation changed data.")

    barrier = Barrier(2)
    addresses = [uuid4().hex + "@example.invalid" for _ in range(2)]

    def concurrent(address):
        barrier.wait(timeout=10)
        return invoke(employees["concurrent"], admin_digest, address)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(concurrent, addresses))
    require(
        sum(isinstance(item, dict) for item in outcomes) == 1
        and sum(item == "40001" for item in outcomes) == 1,
        "Concurrent linking did not produce one winner.",
    )
    count = admin(
        "SELECT count(*) FROM scheduling.app_user WHERE email IN ("
        + ",".join(literal(address) for address in addresses) + ");",
        test_db,
    )
    require(count == "1", "Concurrent linking left an orphan account.")

    # This trigger exists only in the disposable copy.
    admin(
        """
        CREATE FUNCTION scheduling.reject_account_audit_test()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.entity_id = """ + literal(employees["audit"]) + """::uuid THEN
                RAISE EXCEPTION 'Synthetic audit failure';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER reject_account_audit_test
        BEFORE INSERT ON scheduling.audit_event
        FOR EACH ROW EXECUTE FUNCTION scheduling.reject_account_audit_test();
        """,
        test_db,
    )
    before = fingerprint(test_db)
    failed = invoke(
        employees["audit"], admin_digest, uuid4().hex + "@example.invalid"
    )
    require(failed == "P0001", "Synthetic audit failure was not exercised.")
    require(
        fingerprint(test_db) == before,
        "Audit failure left account, membership, link or audit changes.",
    )

    audit_ok = admin(
        "SELECT count(*) FROM scheduling.audit_event "
        f"WHERE entity_id={literal(employees['success'])} "
        "AND action='employee.account_created' "
        f"AND actor_user_id={literal(identities['admin_a']['user_id'])} "
        "AND NOT (after_data ? 'password_hash') "
        "AND NOT (after_data ? 'password');",
        test_db,
    )
    require(audit_ok == "1", "Account audit actor or redaction failed.")

    with psycopg.connect(test_dsn) as conn:
        privileges = conn.execute(
            """
            SELECT
              has_column_privilege(
                current_user,'scheduling.app_user','email','INSERT'),
              has_column_privilege(
                current_user,'scheduling.app_user','password_hash','UPDATE'),
              has_column_privilege(
                current_user,'scheduling.employee','user_id','UPDATE')
            """
        ).fetchone()
        require(
            privileges == (False, False, False),
            "Account function introduced broad runtime write access.",
        )

    print("EMPLOYEE_ACCOUNT_CREATION_AND_LINKING=PASS")
    print("REAL_EMPLOYEE_SIGN_IN_AND_SIGN_OUT=PASS")
    print("ACCOUNT_FUNCTION_ADMINISTRATOR_ISOLATION=PASS")
    print("DUPLICATE_STALE_AND_ALREADY_LINKED_REQUESTS=DENIED")
    print("CONCURRENT_ACCOUNT_LINKING=PASS")
    print("ACCOUNT_MEMBERSHIP_LINK_AND_AUDIT_ROLLBACK=PASS")
    print("ACCOUNT_AUDIT_ACTOR_AND_SECRET_EXCLUSION=PASS")
    print("GENERAL_ACCOUNT_WRITE_PRIVILEGES=NOT_GRANTED")
