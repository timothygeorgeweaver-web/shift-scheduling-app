"""Targeted existing-draft template application certification."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient

from app.auth_routes import COOKIE_NAME, _csrf
from app.main import app
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin


def verify(test_db, token, location, role):
    base = "/api/v1/staff/locations/"+location
    headers = {"X-CSRF-Token": _csrf(token)}

    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, token)

        def post(path, body):
            response = client.post(base+path, headers=headers, json=body)
            require(response.status_code == 201,
                    "Merge fixture failed: "+path+" status="+str(response.status_code))
            return response.json()

        week = post("/schedule-weeks", {
            "week_start_date": "2027-01-04", "expected_location_revision": 1,
        })
        detail = "/schedule-weeks/"+week["id"]
        original = post(detail+"/shifts", {
            "role_id": role, "local_date": "2027-01-04",
            "start_minute": 540, "end_minute": 1020,
            "expected_week_revision": week["revision"],
        })
        original_id = original["slots"][0]["id"]
        template = post("/schedule-templates", {
            "name": "Merge pattern",
            "expected_template_revision": 0, "expected_location_revision": 1,
            "slots": [{
                "role_id": role, "weekday": 6, "start_minute": 120,
                "end_minute": 240, "ends_next_day": False, "note": "Sunday",
            }],
        })
        apply_path = "/schedule-templates/"+template["id"]+"/apply"

        def payload(mode="append"):
            current = client.get(base+detail).json()
            return {
                "week_start_date": "2027-01-04",
                "expected_template_revision": template["revision"],
                "expected_location_revision": 1,
                "week_id": week["id"],
                "expected_week_revision": current["week"]["revision"],
                "mode": mode,
            }

        body = payload()
        require(client.post(base+apply_path, json=body).status_code == 403,
                "Template merge accepted missing CSRF.")
        require(client.post(base+apply_path, headers=headers,
            json={**body, "mode": "unknown"}).status_code == 422,
            "Unknown template mode accepted.")

        appended = post(apply_path, body)
        require(len(appended["slots"]) == 2
                and original_id in {slot["id"] for slot in appended["slots"]},
                "Append did not preserve existing shifts.")
        require(client.post(base+apply_path, headers=headers, json=body)
                .status_code == 409, "Stale template merge accepted.")

        replaced = post(apply_path, payload("replace"))
        require(len(replaced["slots"]) == 1
                and replaced["slots"][0]["id"] != original_id
                and replaced["slots"][0]["local_start"].startswith("2027-01-11T02:00"),
                "Replace did not preserve template business-day placement.")

        admin("""
            CREATE FUNCTION scheduling.reject_template_merge_test()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.action='schedule.template_applied' THEN
                    RAISE EXCEPTION 'Synthetic template merge audit failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_template_merge_test
            BEFORE INSERT ON scheduling.audit_event
            FOR EACH ROW EXECUTE FUNCTION scheduling.reject_template_merge_test();
        """, test_db)
        before = client.get(base+detail).json()
        audit_before = admin("SELECT count(*) FROM scheduling.audit_event;", test_db)
        require(client.post(base+apply_path, headers=headers,
            json=payload("replace")).status_code == 503,
            "Replacement audit failure not exercised.")
        require(client.get(base+detail).json() == before,
                "Failed replacement did not restore original shifts.")
        require(admin("SELECT count(*) FROM scheduling.audit_event;", test_db)
                == audit_before, "Failed replacement retained audit events.")
        admin(
            "DROP TRIGGER reject_template_merge_test ON scheduling.audit_event;"
            "DROP FUNCTION scheduling.reject_template_merge_test();", test_db,
        )

        barrier = Barrier(2)
        race_body = payload()
        def race(_):
            with TestClient(app) as other:
                other.cookies.set(COOKIE_NAME, token)
                barrier.wait(timeout=10)
                return other.post(base+apply_path, headers=headers,
                                  json=race_body).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(race, (0, 1)))
        require(sorted(outcomes) == [201, 409],
                "Concurrent merges bypassed the week revision.")

        fresh = post(apply_path, {
            "week_start_date": "2027-02-01",
            "expected_template_revision": template["revision"],
            "expected_location_revision": 1, "mode": "replace",
        })
        require(len(fresh["slots"]) == 1,
                "Template could not create a fresh destination.")

        current = client.get(base+detail).json()
        blocked_body = payload()
        admin(
            "BEGIN; SET LOCAL ROLE shift_scheduling_owner;"
            "UPDATE scheduling.schedule_week SET state='published' "
            f"WHERE id='{week['id']}'; COMMIT;", test_db,
        )
        require(client.post(base+apply_path, headers=headers, json=blocked_body)
                .status_code == 423, "Template changed a non-draft week.")

    print("TEMPLATE_APPEND_PRESERVES_EXISTING_SHIFTS=PASS")
    print("TEMPLATE_REPLACE_AND_FRESH_DESTINATION=PASS")
    print("REPLACE_FAILURE_RESTORES_ALL_SHIFTS_AND_AUDIT=PASS")
    print("TEMPLATE_MERGE_STALE_CONCURRENT_AND_DRAFT_GUARDS=PASS")
