# Saved availability policy

Administrators can expand Availability deadlines in Team and edit the policy.

Defaults approved by the owner:
- Week starts Monday (weekday 0).
- Cutoff is seven local calendar days before the week starts.
- Local cutoff time is 17:00.
- The pilot location uses America/Los_Angeles.

The editor supports Monday–Sunday, an integer offset of 0–365 days, and a
minute-precision cutoff time from 00:00 through 23:59.
The location's existing timezone determines the deadline timezone.

The policy is stored on the location. Policy and location settings share one
revision counter; either kind of change invalidates an older edit form.

The database independently requires a live administrator for the target
business. Changes and their audit event commit together. The runtime receives
only execution permission on the controlled write function.

The shared calendar module consumes this stored policy. It rejects an
ambiguous/nonexistent cutoff instant for an affected week rather than guessing.
The policy editor itself stores local wall-clock settings.

Employee availability records, publication locks and their effect on later
policy changes are upcoming work. This editor does not yet enable employee
availability entry.

Automated checks cover persisted defaults, calendar integration, validation,
CSRF, business isolation, shared revisions, simultaneous writes and audit
rollback. Browser verification is pending.
