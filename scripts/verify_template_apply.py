"""Apply saved template snapshots in disposable PostgreSQL only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
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


def verify(test_db, identities, location, template_id, saved):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Template application requires the disposable database.",
    )
    token = identities["scheduler_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    base = "/api/v1/staff/locations/"+location
    detail_url = base+"/schedule-templates/"+template_id
    url = detail_url+"/apply"
    first = date.fromisoformat(saved["snapshot"]["week"]["week_start_date"])
    literal = lambda value: sql.Literal(value).as_string()

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        settings = client.get(base+"/schedule-weeks", params={
            "start": first.isoformat(),
            "end": (first+timedelta(days=6)).isoformat(),
        }).json()["location"]

        def body(offset=112, **changes):
            return {
                "week_start_date": (first+timedelta(days=offset)).isoformat(),
                "expected_template_revision": saved["revision"],
                "expected_location_revision": settings["revision"],
                **changes,
            }

        require(client.post(url, json=body()).status_code == 403,
                "Template application accepted missing CSRF.")
        require(client.post(url, headers={
            **headers, "Origin": "https://invalid.example"
        }, json=body()).status_code == 403,
                "Cross-origin template application accepted.")

        before = fingerprint(test_db)
        require(client.post(url, headers=headers, json=body(
            expected_template_revision=saved["revision"]+1,
        )).status_code == 409, "Stale template accepted.")
        require(client.post(url, headers=headers, json=body(
            expected_location_revision=settings["revision"]+1,
        )).status_code == 409, "Stale template location accepted.")
        require(client.post(url, headers=headers, json=body(113))
                .status_code == 422, "Invalid destination weekday accepted.")
        require(fingerprint(test_db) == before,
                "Rejected template application retained changes.")

        response = client.post(url, headers=headers, json=body())
        require(response.status_code == 201, "Template application failed.")
        created = response.json()

        def structure(data):
            anchor = date.fromisoformat(data["week"]["week_start_date"])
            return sorted((
                s["role_id"],
                (date.fromisoformat(s["local_start"][:10])-anchor).days,
                s["local_start"][11:16],
                (date.fromisoformat(s["local_end"][:10])-anchor).days,
                s["local_end"][11:16], s["note"],
            ) for s in data["slots"])

        require(created["week"]["state"] == "draft"
                and structure(created) == structure(saved["snapshot"]),
                "Applied template lost saved structure or overnight times.")
        require(client.get(base+"/schedule-weeks/"+created["week"]["id"])
                .json() == created, "Applied template did not persist.")
        require(client.get(detail_url).json() == saved,
                "Applying a template changed its snapshot.")
        require(client.post(url, headers=headers, json=body())
                .status_code == 409, "Template overwrote an existing schedule.")

        role_id = saved["snapshot"]["slots"][0]["role_id"]
        admin(f"UPDATE scheduling.job_role SET active=false "
              f"WHERE id={literal(role_id)};", test_db)
        before = fingerprint(test_db)
        require(client.post(url, headers=headers, json=body(119))
                .status_code == 422, "Template accepted an inactive role.")
        require(fingerprint(test_db) == before,
                "Inactive-role failure left partial template shifts.")
        admin(f"UPDATE scheduling.job_role SET active=true "
              f"WHERE id={literal(role_id)};", test_db)

        admin("""
            CREATE FUNCTION scheduling.reject_template_apply_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='schedule.template_applied' THEN
                    RAISE EXCEPTION 'Synthetic template apply failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_template_apply_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_template_apply_test();
        """, test_db)
        before = fingerprint(test_db)
        require(client.post(url, headers=headers, json=body(126))
                .status_code == 503, "Template application audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Template application audit failure retained changes.")
        admin(
            "DROP TRIGGER reject_template_apply_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_template_apply_test();", test_db,
        )

        barrier = Barrier(2)
        payload = body(140)

        def race(number):
            with TestClient(app) as other:
                other.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return other.post(url, headers=headers, json=payload).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(race, (1, 2)))
        require(sorted(outcomes) == [201, 409],
                "Concurrent template application duplicated a week.")

        for label, expected in (("employee_a", 403), ("admin_b", 404)):
            with TestClient(app) as denied:
                other = identities[label]["token"]
                denied.cookies.set(COOKIE_NAME, other)
                require(denied.post(url, headers={"X-CSRF-Token": _csrf(other)},
                                   json=body(147)).status_code == expected,
                        "Template application isolation failed.")

    print("APPLY_TEMPLATE_PERSISTENCE_AND_SNAPSHOT_INDEPENDENCE=PASS")
    print("TEMPLATE_ROLE_NOTES_AND_OVERNIGHT_STRUCTURE=PASS")
    print("TEMPLATE_APPLY_CSRF_REVISIONS_AND_BUSINESS_ISOLATION=PASS")
    print("TEMPLATE_APPLY_CONCURRENT_DESTINATION_PROTECTION=PASS")
    print("TEMPLATE_APPLY_AND_AUDIT_FULL_ROLLBACK=PASS")
