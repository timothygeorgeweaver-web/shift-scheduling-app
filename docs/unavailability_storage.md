# Private employee unavailability storage and reads

Migration 0010 adds:
- Effective-dated recurring pattern versions.
- Date replacements with revisions and retained deletion tombstones.
- Frozen local-day blocked snapshots for cutoff/publication integration.

The tables have row security enabled and no runtime table grants.
Controlled database functions independently derive the employee from a live
session, active account, active business membership and active profile link.
Users cannot request an arbitrary employee ID.

GET /api/v1/me/unavailability/profiles lists only the caller's active profiles.
GET /api/v1/me/unavailability/{location}?start=YYYY-MM-DD&days=7
returns a bounded private calendar, relevant recurring origins, exceptions,
snapshots and derived blocked/available intervals.

The previous local date is included internally for overnight carry.
Recurring versions are selected by each origin date's effective date.
An empty date exception means available all day; absent exceptions use the
recurring pattern. Frozen days take precedence over later pattern changes.

Read responses are no-store. Administrators do not automatically receive
other employees' personal self-service records through these endpoints.
Scheduler-facing availability reads will need their own explicit authorization.

These are read APIs. Employee writes, transactional cutoff checks, snapshot
creation and the browser editor are not implemented by this migration.
The integration test creates synthetic snapshots in the disposable copy;
this does not mean schedule publication is implemented.

Future controlled write operations must validate complete window shapes,
enforce cutoff/publication restrictions, preserve revisions and audit changes.
The runtime cannot insert or modify private tables directly.
