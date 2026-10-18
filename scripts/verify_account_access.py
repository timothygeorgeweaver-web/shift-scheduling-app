"""Check administrator boundaries inside the existing disposable DB harness."""

import re

from fastapi import HTTPException
from psycopg.conninfo import conninfo_to_dict

from app.account_access import administrator_connection
from app.database import database_url


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def verify(test_db, identities, locations):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Account checks must target the disposable database.",
    )

    for business in ("a", "b"):
        token = identities["admin_" + business]["token"]
        with administrator_connection(token, locations[business]) as (
            conn, location, actor_id
        ):
            require(
                str(location["id"]) == str(locations[business]),
                "Administrator received the wrong location.",
            )
            require(actor_id is not None, "Administrator actor is missing.")
            require(
                conn.execute("SHOW transaction_read_only").fetchone()[
                    "transaction_read_only"
                ] == "on",
                "Read-only authorization check opened a writable transaction.",
            )

    denied = [
        ("scheduler_a", "a", 403),
        ("employee_a", "a", 403),
        ("admin_a", "b", 404),
        ("admin_b", "a", 404),
    ]
    for identity, business, expected in denied:
        try:
            with administrator_connection(
                identities[identity]["token"], locations[business]
            ):
                pass
        except HTTPException as error:
            require(
                error.status_code == expected,
                "Unexpected account authorization denial.",
            )
        else:
            raise SystemExit("STOP: Unauthorized account access was accepted.")

    print("BUSINESS_ADMINISTRATOR_ACCOUNT_ACCESS=PASS")
    print("SCHEDULER_AND_EMPLOYEE_ACCOUNT_ADMINISTRATION=DENIED")
    print("CROSS_BUSINESS_ACCOUNT_ADMINISTRATION=DENIED")
