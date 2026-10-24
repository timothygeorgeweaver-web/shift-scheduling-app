"""Real copy requests against the disposable database only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import re
from threading import Barrier

from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location, week_id, role_id, first):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Copy checks require the disposable database.",
    )
    token = identities["scheduler_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    base = "/api/v1/staff/locations/"+location
    source_url = base+"/schedule-weeks/"+week_id
    url = source_url+"/copy"
    literal = lambda value: sql.Literal(value).as_string()

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        source_response = client.get(source_url)
        require(source_response.status_code == 200, "Copy fixture read failed.")
        source = source_response.json()
        settings = client.get(base+"/schedule-weeks", params={
            "start": first.isoformat(),
            "end": (first+timedelta(days=6)).isoformat(),
        }).json()["location"]

        def body(offset=56, **changes):
            return {
                "week_start_date": (first+timedelta(days=offset)).isoformat(),
                "expected_source_revision": source["week"]["revision"],
                "expected_location_revision": settings["revision"],
                **changes,
            }

        require(client.post(url, json=body()).status_code == 403,
                "Copy accepted missing CSRF.")
        require(client.post(url, headers={
            **headers, "Origin": "https://invalid.example"
        }, json=body()).status_code == 403, "Cross-origin copy accepted.")

        before = fingerprint(test_db)
        require(client.post(url, headers=headers, json=body(
            expected_source_revision=source["week"]["revision"]+1,
        )).status_code == 409, "Stale source copy accepted.")
        require(client.post(url, headers=headers, json=body(
            expected_location_revision=settings["revision"]+1,
        )).status_code == 409, "Stale location copy accepted.")
        require(client.post(url, headers=headers, json=body(
            offset=57,
        )).status_code == 422, "Wrong week-start date accepted.")
        require(fingerprint(test_db) == before,
                "Rejected copy retained a destination or audit event.")

        response = client.post(url, headers=headers, json=body())
        require(response.status_code == 201, "Schedule copy failed.")
        copied = response.json()
        require(copied["week"]["state"] == "draft"
                and copied["week"]["id"] != week_id
                and len(copied["slots"]) == len(source["slots"]),
                "Copy did not produce an independent draft.")

        def structure(data):
            from datetime import date
            anchor = date.fromisoformat(data["week"]["week_start_date"])
            return sorted((
                s["role_id"],
                (date.fromisoformat(s["local_start"][:10])-anchor).days,
                s["local_start"][11:16],
                (date.fromisoformat(s["local_end"][:10])-anchor).days,
                s["local_end"][11:16],
                s["note"],
            ) for s in data["slots"])

        require(structure(copied) == structure(source),
                "Copy lost a role, local time, note or overnight endpoint.")
        require(set(s["id"] for s in copied["slots"]).isdisjoint(
                    s["id"] for s in source["slots"]),
                "Copy reused source shift IDs.")
        require(client.get(source_url).json() == source,
                "Copy changed its source.")
        require(client.get(base+"/schedule-weeks/"+copied["week"]["id"])
                .json() == copied, "Copied draft did not persist.")
        require(client.post(url, headers=headers, json=body()).status_code == 409,
                "Copy overwrote an existing destination.")

        admin(
            f"UPDATE scheduling.job_role SET active=false "
            f"WHERE id={literal(role_id)};", test_db,
        )
        before = fingerprint(test_db)
        require(client.post(url, headers=headers, json=body(63)).status_code == 422,
                "Copy accepted an inactive role.")
        require(fingerprint(test_db) == before,
                "Inactive-role failure left a partial copy.")
        admin(
            f"UPDATE scheduling.job_role SET active=true "
            f"WHERE id={literal(role_id)};", test_db,
        )

        admin("""
            CREATE FUNCTION scheduling.reject_copy_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='schedule.week_copied' THEN
                    RAISE EXCEPTION 'Synthetic copy audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_copy_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_copy_audit_test();
        """, test_db)
        before = fingerprint(test_db)
        require(client.post(url, headers=headers, json=body(70)).status_code == 503,
                "Copy audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Copy audit failure retained a week, shifts or audit events.")
        admin(
            "DROP TRIGGER reject_copy_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_copy_audit_test();", test_db,
        )

        barrier = Barrier(2)
        payload = body(77)

        def race(number):
            with TestClient(app) as other:
                other.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return other.post(url, headers=headers, json=payload).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(race, (1, 2)))
        require(sorted(results) == [201, 409],
                "Concurrent copies duplicated the destination.")

        for label, expected in (("employee_a", 403), ("admin_b", 404)):
            with TestClient(app) as denied:
                other = identities[label]["token"]
                denied.cookies.set(COOKIE_NAME, other)
                require(denied.post(url, headers={"X-CSRF-Token": _csrf(other)},
                                   json=body(84)).status_code == expected,
                        "Copy staff/business isolation failed.")

    from scripts.verify_schedule_templates import verify as verify_templates
    verify_templates(test_db, identities, location, week_id)

    print("COPY_WEEK_STRUCTURE_NOTES_AND_OVERNIGHT_TIMES=PASS")
    print("COPIED_DRAFT_PERSISTENCE_AND_SOURCE_PRESERVATION=PASS")
    print("COPY_CSRF_AUTHORIZATION_AND_REVISION_CHECKS=PASS")
    print("COPY_DUPLICATE_AND_CONCURRENT_DESTINATION_PROTECTION=PASS")
    print("COPY_INACTIVE_ROLE_AND_AUDIT_FULL_ROLLBACK=PASS")
