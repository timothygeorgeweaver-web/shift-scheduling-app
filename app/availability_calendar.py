"""Shared local-calendar rules for availability deadlines."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


@dataclass(frozen=True)
class CalendarPolicy:
    timezone: str = "America/Los_Angeles"
    week_start: int = 0  # Monday=0 through Sunday=6.
    cutoff_days_before: int = 7
    cutoff_time: time = time(17, 0)

    def __post_init__(self):
        ZoneInfo(self.timezone)
        if type(self.week_start) is not int or not 0 <= self.week_start <= 6:
            raise ValueError("Week start must be an integer from 0 to 6.")
        if (
            type(self.cutoff_days_before) is not int
            or self.cutoff_days_before < 0
        ):
            raise ValueError("Cutoff days must be a nonnegative integer.")
        if (
            not isinstance(self.cutoff_time, time)
            or self.cutoff_time.tzinfo is not None
        ):
            raise ValueError("Cutoff time must be a local wall-clock time.")


@dataclass(frozen=True)
class AvailabilityGate:
    week_start: date
    cutoff: datetime  # UTC instant.
    editable: bool
    reason: str | None


def week_start_for(day: date, week_start: int = 0) -> date:
    if type(day) is not date:
        raise ValueError("Supply a calendar date.")
    if type(week_start) is not int or not 0 <= week_start <= 6:
        raise ValueError("Invalid week start.")
    return day - timedelta(days=(day.weekday() - week_start) % 7)


def _local_instant(day: date, clock: time, zone: ZoneInfo) -> datetime:
    """Resolve a civil time, rejecting DST gaps and ambiguous wall times."""
    naive = datetime.combine(day, clock)
    candidates = set()
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        instant = local.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == naive:
            candidates.add(instant)
    if len(candidates) != 1:
        raise ValueError("Cutoff falls at a nonexistent or ambiguous local time.")
    return candidates.pop()


def cutoff_for_week(start: date, policy: CalendarPolicy) -> datetime:
    if week_start_for(start, policy.week_start) != start:
        raise ValueError("Date is not the configured start of a week.")
    cutoff_day = start - timedelta(days=policy.cutoff_days_before)
    return _local_instant(
        cutoff_day, policy.cutoff_time, ZoneInfo(policy.timezone)
    )


def availability_gate(
    target_day: date,
    now: datetime,
    policy: CalendarPolicy,
    *,
    published: bool = False,
) -> AvailabilityGate:
    """Evaluate cutoff/publication only; identity checks belong to the API."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("Supply a timezone-aware current instant.")
    if type(published) is not bool:
        raise ValueError("Published state must be boolean.")

    start = week_start_for(target_day, policy.week_start)
    cutoff = cutoff_for_week(start, policy)
    if published:
        return AvailabilityGate(start, cutoff, False, "published_week")
    if now.astimezone(UTC) >= cutoff:
        return AvailabilityGate(start, cutoff, False, "cutoff_reached")
    return AvailabilityGate(start, cutoff, True, None)


def local_today(now: datetime, policy: CalendarPolicy) -> date:
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("Supply a timezone-aware current instant.")
    return now.astimezone(ZoneInfo(policy.timezone)).date()
