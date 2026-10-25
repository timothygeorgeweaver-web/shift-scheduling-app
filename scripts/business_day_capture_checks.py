"""Focused rollover capture, copy and template checks."""

from fastapi.testclient import TestClient
from app.auth_routes import COOKIE_NAME, _csrf
from scripts.apply_reviewed_migration import require
from scripts.create_recovery_checkpoint import admin


def verify(test_db, token, location, role):
    base = "/api/v1/staff/locations/"+location
    headers = {"X-CSRF-Token": _csrf(token)}

    with TestClient(app_import()) as client:
        client.cookies.set(COOKIE_NAME, token)

        def post(path, body):
            response = client.post(base+path, headers=headers, json=body)
            require(response.status_code == 201,
                    "Capture operation failed: "+path+" status="+str(response.status_code))
            return response.json()

        def blank(day, revision=1):
            return post("/schedule-weeks", {
                "week_start_date": day,
                "expected_location_revision": revision,
            })

        first = blank("2027-01-04")
        require(first["business_day_start_minute"] == 300,
                "New week did not capture 05:00.")
        detail = "/schedule-weeks/"+first["id"]
        created = post(detail+"/shifts", {
            "role_id": role, "local_date": "2027-01-11",
            "start_minute": 120, "end_minute": 240,
            "expected_week_revision": first["revision"],
        })
        saved = post(detail+"/templates", {
            "name": "Captured week", "expected_source_revision": created["week"]["revision"],
        })
        require(saved["business_day_start_minute"] == 300,
                "Week template lost its rollover.")
        pattern_path = "/schedule-templates/"+saved["id"]+"/pattern"
        pattern = client.get(base+pattern_path).json()
        require(pattern["slots"][0]["weekday"] == 6
                and pattern["slots"][0]["start_minute"] == 120,
                "Sunday early hours changed business weekday.")

        response = client.put(base+pattern_path, headers=headers, json={
            "name": "Edited captured week", "slots": pattern["slots"],
            "expected_template_revision": pattern["revision"],
            "expected_location_revision": 1,
        })
        require(response.status_code == 200, "Captured template editing failed.")
        saved = response.json()

        applied = post("/schedule-templates/"+saved["id"]+"/apply", {
            "week_start_date": "2027-03-01",
            "expected_template_revision": saved["revision"],
            "expected_location_revision": 1,
        })
        require(applied["week"]["business_day_start_minute"] == 300
                and applied["slots"][0]["local_start"].startswith("2027-03-08T02:00"),
                "Template application lost Sunday business-day placement.")

        copied = post(detail+"/copy", {
            "week_start_date": "2027-04-05",
            "expected_source_revision": created["week"]["revision"],
            "expected_location_revision": 1,
        })
        require(copied["slots"][0]["local_start"].startswith("2027-04-12T02:00"),
                "Week copying lost Sunday business-day placement.")

        direct = post("/schedule-templates", {
            "name": "Direct captured template",
            "slots": pattern["slots"],
            "expected_template_revision": 0,
            "expected_location_revision": 1,
        })
        require(direct["business_day_start_minute"] == 300,
                "New direct template did not capture rollover.")

        policy = client.patch(base+"/business-day-policy", headers=headers, json={
            "business_day_start_minute": 420, "expected_location_revision": 1,
        })
        require(policy.status_code == 200, "Fixture rollover change failed.")
        require(client.get(base+detail).json()["week"]["business_day_start_minute"] == 300,
                "Location change reinterpreted an existing week.")
        require(client.get(base+pattern_path).json()["business_day_start_minute"] == 300,
                "Location change reinterpreted an existing template.")
        later = blank("2027-05-03", 2)
        require(later["business_day_start_minute"] == 420,
                "Later week did not capture updated policy.")

        stale = client.post(base+"/schedule-templates", headers=headers, json={
            "name": "Stale capture", "slots": [],
            "expected_template_revision": 0, "expected_location_revision": 1,
        })
        require(stale.status_code == 409, "Stale location policy was accepted.")

        legacy = blank("2027-06-07", 2)
        # Synthetic legacy fixture only; real saved records are never rewritten.
        admin(
            "BEGIN; SET LOCAL ROLE shift_scheduling_owner;"
            "UPDATE scheduling.schedule_week SET business_day_start_minute=0 "
            f"WHERE id='{legacy['id']}'; COMMIT;", test_db,
        )
        old_detail = "/schedule-weeks/"+legacy["id"]
        old_shift = post(old_detail+"/shifts", {
            "role_id": role, "local_date": "2027-06-13",
            "start_minute": 120, "end_minute": 240,
            "expected_week_revision": legacy["revision"],
        })
        old_copy = post(old_detail+"/copy", {
            "week_start_date": "2027-07-05",
            "expected_source_revision": old_shift["week"]["revision"],
            "expected_location_revision": 2,
        })
        require(old_copy["slots"][0]["local_start"].startswith("2027-07-12T02:00"),
                "Legacy-to-business-week copying changed the business weekday.")

    print("NEW_WEEK_AND_DIRECT_TEMPLATE_ROLLOVER_CAPTURE=PASS")
    print("SAVE_EDIT_APPLY_AND_COPY_SUNDAY_EARLY_HOURS=PASS")
    print("LATER_POLICY_CHANGES_PRESERVE_SAVED_RECORDS=PASS")
    print("STALE_POLICY_AND_LEGACY_COPY_COMPATIBILITY=PASS")


def app_import():
    from app.main import app
    return app
