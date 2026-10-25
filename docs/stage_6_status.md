# Stage 6: operational readiness and pilot

Status: IN PROGRESS.

## Verified starting point

Stage 5 closure:
`2d5019a239aa0ab49c78b2deea1225fe068fdf2c`.

The preceding gate passed 537 regression tests, owner browser acceptance,
focused PostgreSQL publication integration, and an independent recovery restore.

Recovery checkpoint:
`/home/timothygeorgeweaver/shift-scheduling-evidence/checkpoints/20261025T231519262883Z`.

## Remaining V1 release gates

1. Reproducible installation and startup:
   validate dependencies, configuration handling, process supervision,
   restart behavior, and liveness versus database readiness.

2. Deployment:
   select the pilot host and access method; configure HTTPS, session-cookie
   behavior, trusted proxy handling, secret storage, and restricted database
   access for that environment.

3. Routine recovery:
   establish an encrypted backup destination, retention, scheduling,
   failure reporting, and an independently demonstrated restore procedure.
   Existing recovery checkpoints do not alone establish unattended backups.

4. Final acceptance:
   demonstrate blank-week creation, copied weeks, templates, assignment,
   publication, employee access, amendments, and history with a realistic
   restaurant dataset. Retain the existing authorization, concurrency,
   privacy, business-day, and timezone/DST acceptance evidence.

5. Pilot handoff:
   document account setup, operating instructions, known limitations,
   support and rollback procedures, and owner acceptance.

## Decisions required before dependent deployment work

- Pilot hosting and access arrangement.
- Pilot business jurisdiction and required labor constraints.
- Backup destination, encryption-key custody, and retention.

Development can continue on environment-independent readiness work before
these decisions. Public deployment and live pilot sign-off remain pending.

## Scope boundaries

V1 does not certify legal compliance. Automated meal deductions are estimates.
Notifications, additional approval workflows, and mobile-specific features
must be explicitly classified in the pilot limitations rather than implied
to exist.

No database migration, real publication, external notification, or hosted
deployment is performed by this opening block.
