# Stage 2: employee availability

## Calendar and cutoff foundation

The shared availability calendar implements the approved Monday week start
and preceding-Monday 17:00 local cutoff. America/Los_Angeles is the pilot
timezone. Cutoffs are converted to UTC after resolving the local date/time.

Week-start, cutoff-day offset and cutoff time are explicit policy inputs.
The backend will load them from administrator-managed location settings.
There is no browser or database policy editor in this foundation step.

At the exact cutoff, editing is closed. Published weeks are locked even
before cutoff. A configurable cutoff falling at a nonexistent or ambiguous
DST wall time is rejected rather than silently shifted.

This module evaluates only calendar/cutoff/publication gates. It does not
authorize employee identity, check employee profile state, persist availability,
or create schedule publication records. Those belong to the upcoming API and
database transactions.

## Remaining Stage 2 work

- Persist administrator-adjustable week/cutoff policy.
- Identify the signed-in employee through a live, active profile link.
- Store recurring availability with effective dates.
- Store date-specific full replacement windows and unavailable-all-day.
- Resolve effective availability by local calendar date.
- Enforce cutoff and publication locks in write transactions.
- Preserve locked availability against later recurring-pattern changes.
- Add employee editing and effective-calendar browser screens.
- Verify privacy, concurrency and the applicable V1 acceptance cases.

The plan's proposed handling of missing availability and overnight window
entry still needs to be resolved before those features are finalized.

## Saved policy implementation

Migration 0009 persists week-start and cutoff settings on each location.
An administrator-only API and Team form now edit these settings with audit
and shared location revision checks. The database integration tests passed;
browser verification remains pending. This completes policy persistence
from the earlier remaining-work list. Employee availability storage and
editing remain the next implementation slice.
