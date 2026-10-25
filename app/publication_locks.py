"""Exact publication lock boundaries; no persistence or authorization."""

from datetime import date, timedelta

from app.business_day import business_day_bounds, validate_rollover


def publication_lock_plan(first_date, timezone_name, rollover_minute):
    """Describe the published week without locking adjacent business hours.

    Calendar windows use half-open local minute intervals. They cover exactly
    seven business days, even when those days span eight calendar dates.
    UTC endpoints preserve the actual elapsed duration across DST.

    Persistence must freeze effective availability within these windows and
    enforce changed-cell checks for every write path in the same transaction
    as publication. This helper alone does not lock or publish anything.
    """
    if (type(first_date) is not date
            or not date(2000, 1, 1) <= first_date <= date(9998, 12, 24)):
        raise ValueError("Choose a supported publication week.")
    rollover = validate_rollover(rollover_minute)
    bounds = [
        business_day_bounds(
            first_date+timedelta(days=offset), timezone_name, rollover,
        )
        for offset in range(7)
    ]
    windows = []
    for offset in range(8):
        start = rollover if offset == 0 else 0
        end = rollover if offset == 7 else 1440
        if start < end:
            windows.append({
                "date": (first_date+timedelta(days=offset)).isoformat(),
                "start_minute": start,
                "end_minute": end,
            })
    return {
        "starts_at": bounds[0][0].isoformat(),
        "ends_at": bounds[-1][1].isoformat(),
        "calendar_windows": windows,
    }
