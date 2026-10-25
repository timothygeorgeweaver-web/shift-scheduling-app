# V1 full mock restaurant test

Status: READY TO PERFORM. Results are not yet recorded.

Use fictional staff and schedules. Changes made through this test are real
saved application records, including immutable publication history. Use
clearly named mock records and an unused future date range.

The automated checks do not replace this owner acceptance test.

## Start and open

    systemctl --user start shift-scheduling.service

Open http://127.0.0.1:8010 and hard refresh after code changes.

Stop the service before database cloning or recovery operations:

    systemctl --user stop shift-scheduling.service

## Suggested dataset

- 15–25 fictional employees.
- 4–6 job types covering a realistic restaurant.
- A mix of single-role and multi-role qualifications.
- Several inactive or unqualified employees for exclusion checks.
- An administrator, a scheduler, and employee-only accounts.
- Three unused future weeks beyond the applicable availability cutoff.
- Opening, daytime, closing and after-midnight shifts.
- Multiple shifts beginning at the same time.
- Multiple same-day shifts for selected employees.
- Availability blocks including the hours after midnight.

Use the restaurant staffing pattern you want the product to handle.
Account credentials and private employee details must not be recorded in
the repository or test findings.

## Acceptance checklist

Record PASS, FAIL, or NOT TESTED for each item.

| ID | Exercise | Expected result |
| --- | --- | --- |
| M01 | Create roles and staff; reorder job types. | Qualifications and ordering persist after reload. |
| M02 | Save employee unavailability and perform an additive mass edit. | Correct business days; existing unrelated blocks remain. |
| M03 | Create a blank week and its full shift structure. | Times, roles, duplicate start times and overnight shifts are correct. |
| M04 | Save a template, edit it, and create a new blank template. | Template changes leave existing weeks unchanged. |
| M05 | Copy a week; apply a template by append and replace. | Shift structure is correct; employee assignments are not copied. |
| M06 | Open the separate assigner and assign a full week. | Only occupied start-time rows appear; shift editing and assignment views remain mutually exclusive. |
| M07 | Attempt an unqualified, inactive, overlapping or prohibited-rest assignment. | Assignment is blocked with a useful explanation. |
| M08 | Attempt availability and daily-hours warning assignments. | Required per-rule approval and reason are enforced. |
| M09 | Create two assignments that together exceed daily hours. | Both contributing shifts show the warning. |
| M10 | Edit an assigned shift; unassign one shift; clear a week's assignments. | Validation runs; intended employee retention works; shifts remain when assignments are cleared. |
| M11 | Publish a week with an Unfilled shift. | Explicit publication and Unfilled confirmations are required. |
| M12 | Sign in as an employee and inspect the published roster. | Full team roster is visible; private reasons and draft changes are absent. |
| M13 | Attempt to change published-week unavailability. | Published cells are protected; adjacent unlocked business-week hours remain editable. |
| M14 | Amend a published week and republish. | Employee roster changes only after republication; prior version remains inspectable. |
| M15 | Delete an unpublished mock week and recreate it. | Date can be reused; a published week cannot be deleted. |
| M16 | Reload pages, sign out/in, and restart the service. | Saved data persists and permissions remain correct. |

## Findings

For each issue record:

- Checklist ID and a short title.
- Steps to reproduce using fictional names.
- Expected behavior and actual behavior.
- Whether it blocks scheduling, has a workaround, or is cosmetic.

Do not mark V1 accepted until blocking findings are fixed and retested.
Unexercised items remain NOT TESTED.

## Acceptance boundary

This is desktop acceptance of the local V1 scheduling foundation.
It does not certify hosted deployment, mobile delivery or legal compliance.

V2 adds random assignment. V3 adds the employee mobile collaboration app.
