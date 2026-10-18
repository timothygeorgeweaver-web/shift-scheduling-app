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

## Controlled employee writes

Migration 0011 implements employee-owned recurring-pattern versions and
date-exception creation, replacement and deletion. Deletions retain revision
tombstones. Full before/after history is retained atomically in audit events.

POST /api/v1/me/unavailability/{location} accepts operation, date,
expected_revision and expected_location_revision. Recurring writes supply
weekly_windows keyed 0 (Monday) through 6 (Sunday); exception/delete writes
supply windows. Empty exceptions mean available all day. Deletes restore
recurring behavior. Each day accepts at most 32 intervals.

The database independently checks live session/account/membership/profile
identity, validates interval shapes, serializes revisions and policy changes,
and rejects cutoff-locked or frozen target dates. It rejects ambiguous or
nonexistent cutoff instants. Employee writes cannot target past dates;
the supported write-date upper bound is 9998-12-31.

Recurring versions apply from their effective origin date. Earlier origins
remain unchanged. Existing frozen snapshots remain authoritative, including
when a neighboring overnight entry changes. This migration does not create
publication snapshots or implement schedule publication.

A restrictive audit SELECT policy prevents coworkers from reading private
unavailability interval history through shared staff history.

Disposable PostgreSQL certification covers persistence, overnight behavior,
empty exceptions, deletion tombstones, concurrency, private history, cutoff
and publication denial, DST cutoffs, and audit-failure rollback.
The production migration creates functions/policy and its migration record;
it does not seed employee unavailability.

The employee browser editor and publication/snapshot creation remain pending.
