"""Independent template changes, retirement and atomic rollback."""

import re

from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from app.auth_routes import COOKIE_NAME, _csrf
from app.database import database_url
from app.main import app
from scripts.apply_reviewed_migration import install, require
from scripts.create_recovery_checkpoint import admin, fingerprint


def verify(test_db, identities, location, template_id, week_id):
    require(
        re.fullmatch(r"shift_sched_test_[0-9a-f]{32}", test_db) is not None
        and conninfo_to_dict(database_url()).get("dbname") == test_db,
        "Template change checks require the disposable database.",
    )
    install("0020_manage_schedule_templates", test_db)
    token = identities["scheduler_a"]["token"]
    headers = {"X-CSRF-Token": _csrf(token)}
    base = "/api/v1/staff/locations/"+location
    url = base+"/schedule-templates/"+template_id

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)
        original = client.get(url).json()
        body = {
            "name": "Renamed certification template",
            "expected_template_revision": original["revision"],
        }
        require(client.patch(url, json=body).status_code == 403,
                "Template change accepted missing CSRF.")
        response = client.patch(url, headers=headers, json=body)
        require(response.status_code == 200, "Template rename failed.")
        renamed = client.get(url).json()
        require(renamed["name"] == body["name"]
                and renamed["snapshot"] == original["snapshot"],
                "Rename changed the template pattern.")
        require(client.patch(url, headers=headers, json=body)
                .status_code == 409, "Stale template change accepted.")

        source = client.get(base+"/schedule-weeks/"+week_id).json()
        replacement = {
            "name": renamed["name"],
            "expected_template_revision": renamed["revision"],
            "source_week_id": week_id,
            "expected_source_revision": source["week"]["revision"],
        }
        before = fingerprint(test_db)
        require(client.patch(url, headers=headers, json=replacement)
                .status_code == 200, "Template pattern replacement failed.")
        after = fingerprint(test_db)
        for table in ("schedule_week", "shift_slot"):
            require(after["data"][table] == before["data"][table],
                    "Template replacement changed an existing schedule.")
        current = client.get(url).json()
        require(len(current["snapshot"]["slots"]) == len(source["slots"]),
                "Template did not capture the replacement pattern.")
        require(current["snapshot"] != original["snapshot"],
                "Template replacement did not use the edited source.")

        admin("""
            CREATE FUNCTION scheduling.reject_template_change_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='schedule.template_changed' THEN
                    RAISE EXCEPTION 'Synthetic template change audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_template_change_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_template_change_test();
        """, test_db)
        before = fingerprint(test_db)
        require(client.patch(url, headers=headers, json={
            "name": current["name"],
            "expected_template_revision": current["revision"],
            "active": False,
        }).status_code == 503, "Template change audit failure not exercised.")
        require(fingerprint(test_db) == before,
                "Template change survived an audit failure.")
        admin(
            "DROP TRIGGER reject_template_change_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_template_change_test();", test_db,
        )

        for label, expected in (("employee_a", 403), ("admin_b", 404)):
            with TestClient(app) as denied:
                other = identities[label]["token"]
                denied.cookies.set(COOKIE_NAME, other)
                require(denied.patch(url, headers={"X-CSRF-Token": _csrf(other)},
                    json={
                        "name": current["name"],
                        "expected_template_revision": current["revision"],
                    }).status_code == expected,
                    "Template change isolation failed.")

        require(client.patch(url, headers=headers, json={
            "name": current["name"],
            "expected_template_revision": current["revision"],
            "active": False,
        }).status_code == 200, "Template archive failed.")
        require(client.get(url).status_code == 404,
                "Archived template remained usable.")
        require(all(t["id"] != template_id for t in
                    client.get(base+"/schedule-templates").json()),
                "Archived template remained in the active list.")

    print("TEMPLATE_RENAME_AND_PATTERN_REPLACEMENT=PASS")
    print("TEMPLATE_EDITS_LEAVE_EXISTING_WEEKS_UNCHANGED=PASS")
    print("TEMPLATE_CHANGE_AUTHORIZATION_REVISIONS_AND_ROLLBACK=PASS")
    print("TEMPLATE_ARCHIVE_PRESERVES_HISTORY_AND_REMOVES_ACTIVE_USE=PASS")
