# Product roadmap

Approved by the product owner on October 25, 2026.

This roadmap supersedes the previous version ordering and conflicting
mobile-delivery scope in the original V1 planning documents.

## Version sequence

| Version | Scope |
| --- | --- |
| V1 — Manual scheduling foundation | Desktop staff administration, qualifications, unavailability, shift planning, templates and copying, manual assignment, eligibility checks, publishing, and history. |
| V2 — Random assignment | Automatically fill shifts using eligibility and scheduling rules. Random assignment is a central product differentiator, not an optional peripheral feature. |
| V3 — Mobile collaboration | A concise employee mobile app for setting unavailability, messaging, trading and picking up shifts, and viewing assigned shifts. |
| V4 — Skills | Employee proficiency scores and skill-based shift requirements and assignment. |
| V5 — Performance | Shift evaluations, performance grades, workload estimates, and sales data. |
| V6 — Fairness | Balance shift distribution across employees. |

## Demo milestone

The target for a potential business demonstration is completion and
validation of V1 through V3.

V1 provides the scheduling foundation. V2 demonstrates the core automated
assignment value. V3 supplies the essential employee-facing mobile tools.

This milestone does not imply that any version is already complete or
approved for live business use.

## Immediate V1 completion path

1. Finish local operating checks and documentation.
2. Mark the build ready for owner mock testing.
3. The owner runs the complete workflow using a full mock staff,
   qualifications, unavailability, and realistic shifts.
4. Fix material findings and obtain owner acceptance.
5. Begin V2 after V1 acceptance.

Online hosting and a real-business pilot are deferred until after the mock
test. They are not prerequisites for declaring the local build ready for
that test. Readiness for live use requires a separate deployment and
operational acceptance gate.

## Mobile delivery

Mobile is essential to the main product and moves forward from V5 to V3.
Skills and Performance move to V4 and V5 respectively.

V1 acceptance focuses on desktop browser use. Phone-browser optimization
is not a V1 acceptance requirement. Existing employee web functionality
remains available; this decision does not require removing it.

The intended employee experience is a dedicated, concise mobile app.
The mobile technology, supported platforms, messaging behavior, and shift
trade/pickup approval rules will be defined before dependent V3 work.

Preserve shared server-side authorization, eligibility rules, publication
boundaries, and APIs so the mobile client uses the same trusted backend.
