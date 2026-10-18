# Business and location settings

Administrators can expand Business and location settings in Team and select
Edit settings. Business name, location name and timezone save together.

The API validates nonblank names up to 200 characters and a recognized timezone.
Pacific time is America/Los_Angeles. The database independently validates the
timezone and the live administrator's membership in the target business.

Both business and location revisions are required. Saves serialize on the
business and increment both revisions. Stale or concurrent edits cannot
overwrite a successful change. Two audit events record the before/after
business and location details in the same transaction.

Schedulers and employees cannot use the settings API or see its controls.
The runtime role receives execution permission on the controlled function;
general business/location UPDATE grants are not added.

This edits the existing location; it does not introduce a multi-location
operational UI. Business names are shared across locations in the same business.

Week-start, cutoff and rest policy settings remain separate scheduling work.
Timezone edits currently precede availability/schedule data. Once those data
exist, timezone changes will need explicit impact handling before commit.

Automated checks cover authorization, CSRF, validation, stale/concurrent saves
and full rollback on audit failure. Browser verification remains pending.
