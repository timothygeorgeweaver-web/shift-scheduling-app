from datetime import date, datetime, timedelta

import pytest

from app.business_day import business_date, rebase_local_shift

SOURCE = date(2027, 1, 4)
TARGET = date(2027, 2, 1)


@pytest.mark.parametrize("source_rollover,target_rollover", [
    (0, 0), (0, 300), (300, 0), (300, 300), (300, 420),
])
def test_every_weekday_and_clock_time_preserved(source_rollover, target_rollover):
    for offset in range(7):
        day = SOURCE + timedelta(days=offset)
        for minute in range(0, 1440, 15):
            actual_day = day + timedelta(days=int(minute < source_rollover))
            start = datetime.combine(actual_day, datetime.min.time()) + timedelta(
                minutes=minute,
            )
            end = start + timedelta(hours=8)
            copied_start, copied_end = rebase_local_shift(
                start, end, SOURCE, TARGET,
                source_rollover=source_rollover,
                target_rollover=target_rollover,
            )
            assert business_date(copied_start, target_rollover) == \
                TARGET + timedelta(days=offset)
            assert copied_start.time() == start.time()
            assert copied_end.time() == end.time()
            assert copied_end - copied_start == end - start
            assert start.date() == actual_day


def test_legacy_sunday_early_shift_maps_to_sunday_business_day():
    start, end = rebase_local_shift(
        datetime(2027, 1, 10, 2), datetime(2027, 1, 10, 6),
        SOURCE, TARGET, source_rollover=0, target_rollover=300,
    )
    assert start == datetime(2027, 2, 8, 2)
    assert end == datetime(2027, 2, 8, 6)
    assert business_date(start, 300) == date(2027, 2, 7)


def test_overnight_clock_times_and_calendar_end_flag_preserved():
    start, end = rebase_local_shift(
        datetime(2027, 1, 4, 20), datetime(2027, 1, 5, 2),
        SOURCE, TARGET, source_rollover=0, target_rollover=300,
    )
    assert start == datetime(2027, 2, 1, 20)
    assert end == datetime(2027, 2, 2, 2)


def test_following_monday_early_shift_belongs_to_source_sunday():
    start, end = rebase_local_shift(
        datetime(2027, 1, 11, 2), datetime(2027, 1, 11, 4),
        SOURCE, TARGET, source_rollover=300, target_rollover=300,
    )
    assert start == datetime(2027, 2, 8, 2)
    assert end == datetime(2027, 2, 8, 4)


@pytest.mark.parametrize("start,end", [
    (datetime(2027, 1, 3, 20), datetime(2027, 1, 3, 21)),
    (datetime(2027, 1, 11, 6), datetime(2027, 1, 11, 7)),
    (datetime(2027, 1, 4, 10), datetime(2027, 1, 4, 9)),
    (datetime(2027, 1, 4, 10), datetime(2027, 1, 6, 11)),
    (datetime(2027, 1, 4, 10, 0, 1), datetime(2027, 1, 4, 11)),
])
def test_invalid_source_intervals_rejected(start, end):
    with pytest.raises(ValueError):
        rebase_local_shift(
            start, end, SOURCE, TARGET,
            source_rollover=300, target_rollover=300,
        )
