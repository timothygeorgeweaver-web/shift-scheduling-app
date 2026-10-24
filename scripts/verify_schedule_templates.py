"""Template snapshots in the disposable database only."""

import re

import psycopg
from psycopg.conninfo import conninfo_to_dict
from fastapi.testclient import TestClient

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location, week_id):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Template checks require the disposable database.",
    )
    install("0019_schedule_templates", test_db)
    token = identities["scheduler_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    base = "/api/v1/staff/locations/"+location
    source_url = base+"/schedule-weeks/"+week_id
    save_url = source_url+"/templates"
    list_url = base+"/schedule-templates"

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        source = client.get(source_url).json()
        body = {
            "name": "Template certification",
            "expected_source_revision": source["week"]["revision"],
        }
        require(client.post(save_url, json=body).status_code == 403,
                "Template save accepted missing CSRF.")
        require(client.post(save_url, headers=headers,
                            json={**body, "name": "   "}).status_code == 422,
                "Blank template name accepted.")
        require(client.post(save_url, headers=headers, json={
            **body, "expected_source_revision": source["week"]["revision"]+1,
        }).status_code == 409, "Stale template source accepted.")

        response = client.post(save_url, headers=headers, json=body)
        require(response.status_code == 201, "Template save failed.")
        template = response.json()
        detail_url = list_url+"/"+template["id"]
        detail = client.get(detail_url).json()
        require(template["shift_count"] == len(source["slots"])
                and len(detail["snapshot"]["slots"]) == len(source["slots"]),
                "Template snapshot lost shifts.")
        require(any(t["id"] == template["id"]
                    for t in client.get(list_url).json()),
                "Saved template did not appear in list.")
        require(client.get(source_url).json() == source,
                "Saving a template changed the source.")
        require(client.post(save_url, headers=headers, json=body)
                .status_code == 409, "Duplicate template name accepted.")

        # Remove a source shift through the real API; the snapshot must persist.
        require(bool(source["slots"]), "Template fixture needs a source shift.")
        slot = source["slots"][0]
        removed = client.post(
            source_url+"/shifts/"+slot["id"]+"/remove",
            headers=headers, json={
                "expected_week_revision": source["week"]["revision"],
                "expected_shift_revision": slot["revision"],
            },
        )
        require(removed.status_code == 200, "Template independence probe failed.")
        require(client.get(detail_url).json() == detail,
                "Source edits changed the saved template snapshot.")

        admin("""
            CREATE FUNCTION scheduling.reject_template_audit_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='schedule.template_saved' THEN
                    RAISE EXCEPTION 'Synthetic template audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_template_audit_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_template_audit_test();
        """, test_db)
        current = client.get(source_url).json()
        before = fingerprint(test_db)
        require(client.post(save_url, headers=headers, json={
            "name": "Must roll back",
            "expected_source_revision": current["week"]["revision"],
        }).status_code == 503, "Template audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Template survived a failed audit.")
        admin(
            "DROP TRIGGER reject_template_audit_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_template_audit_test();", test_db,
        )

        for label, expected in (("employee_a", 403), ("admin_b", 404)):
            with TestClient(app) as denied:
                other = identities[label]["token"]
                denied.cookies.set(COOKIE_NAME, other)
                require(denied.get(list_url).status_code == expected,
                        "Template list isolation failed.")
                require(denied.get(detail_url).status_code == expected,
                        "Template detail isolation failed.")
                require(denied.post(save_url,
                    headers={"X-CSRF-Token": _csrf(other)}, json=body)
                    .status_code == expected, "Template write isolation failed.")

    from scripts.verify_template_apply import verify as verify_apply
    verify_apply(test_db, identities, location, template['id'], detail)

    try:
        with psycopg.connect(database_url()) as conn:
            conn.execute("SELECT * FROM scheduling.schedule_template")
    except psycopg.errors.InsufficientPrivilege:
        pass
    else:
        raise SystemExit("STOP: Runtime has direct template-table access.")

    print("NAMED_TEMPLATE_SAVE_LIST_AND_PERSISTENCE=PASS")
    print("TEMPLATE_SNAPSHOT_INDEPENDENT_OF_SOURCE_EDITS=PASS")
    print("TEMPLATE_CSRF_STALE_NAME_AND_BUSINESS_CHECKS=PASS")
    print("TEMPLATE_AND_AUDIT_ATOMIC_ROLLBACK=PASS")
    print("GENERAL_TEMPLATE_TABLE_ACCESS=DENIED")
