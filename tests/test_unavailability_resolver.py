from datetime import date, timedelta

import pytest

from app.unavailability_resolver import (
    BlockWindow, available_for_date, blocked_for_date, intersects_blocked,
)

MONDAY = date(2026, 10, 26)
TUESDAY = MONDAY + timedelta(days=1)


def recurring(pattern):
    return lambda day: pattern.get(day.weekday(), ())


def test_no_entries_means_fully_available():
    assert blocked_for_date(MONDAY, recurring({}), {}) == ()
    assert available_for_date(MONDAY, recurring({}), {}) == ((0, 1440),)


def test_disjoint_blocks_leave_the_middle_available():
    pattern = recurring({0: [BlockWindow(480, 720), BlockWindow(1020, 1260)]})
    blocked = blocked_for_date(MONDAY, pattern, {})
    assert available_for_date(MONDAY, pattern, {}) == (
        (0, 480), (720, 1020), (1260, 1440)
    )
    assert not intersects_blocked(840, 960, blocked)
    assert intersects_blocked(1080, 1200, blocked)


def test_empty_exception_clears_one_date_only():
    pattern = recurring({0: [BlockWindow(900, 1380)]})
    exceptions = {MONDAY: []}
    assert blocked_for_date(MONDAY, pattern, exceptions) == ()
    assert blocked_for_date(MONDAY + timedelta(days=7), pattern, exceptions) == (
        (900, 1380),
    )
    del exceptions[MONDAY]
    assert blocked_for_date(MONDAY, pattern, exceptions) == ((900, 1380),)


def test_all_day_exception():
    assert available_for_date(
        MONDAY, recurring({}), {MONDAY: [BlockWindow(0, 1440)]}
    ) == ()


def test_overnight_recurring_block_is_split_at_midnight():
    pattern = recurring({0: [BlockWindow(1200, 120, True)]})
    assert blocked_for_date(MONDAY, pattern, {}) == ((1200, 1440),)
    assert blocked_for_date(TUESDAY, pattern, {}) == ((0, 120),)


def test_tuesday_exception_replaces_monday_carry_in():
    pattern = recurring({0: [BlockWindow(1200, 120, True)]})
    assert blocked_for_date(TUESDAY, pattern, {TUESDAY: []}) == ()
    assert blocked_for_date(
        TUESDAY, pattern, {TUESDAY: [BlockWindow(600, 660)]}
    ) == ((600, 660),)


def test_previous_date_exception_controls_its_overnight_carry():
    pattern = recurring({0: [BlockWindow(1200, 120, True)]})
    assert blocked_for_date(TUESDAY, pattern, {MONDAY: []}) == ()
    assert blocked_for_date(TUESDAY, recurring({}), {
        MONDAY: [BlockWindow(1320, 180, True)]
    }) == ((0, 180),)


def test_overlapping_carry_and_current_blocks_are_merged():
    pattern = recurring({
        0: [BlockWindow(1320, 120, True)],
        1: [BlockWindow(60, 180)],
    })
    assert blocked_for_date(TUESDAY, pattern, {}) == ((0, 180),)


def test_sunday_carry_crosses_week_boundary():
    pattern = recurring({6: [BlockWindow(1380, 60, True)]})
    assert blocked_for_date(MONDAY, pattern, {}) == ((0, 60),)


@pytest.mark.parametrize("start,end,expected", [
    (480, 600, False), (720, 780, False),
    (599, 601, True), (719, 721, True),
])
def test_touching_endpoints_do_not_conflict(start, end, expected):
    assert intersects_blocked(start, end, ((600, 720),)) is expected


@pytest.mark.parametrize("arguments", [
    (1200, 120), (600, 600), (-1, 60),
    (0, 1441), (True, 60), (600, 700, True),
])
def test_invalid_or_implicit_overnight_window_is_rejected(arguments):
    with pytest.raises(ValueError):
        BlockWindow(*arguments)


def test_effective_version_loader_receives_each_origin_date():
    seen = []
    def load(day):
        seen.append(day)
        return [BlockWindow(1320, 60, True)] if day == MONDAY else []
    assert blocked_for_date(TUESDAY, load, {}) == ((0, 60),)
    assert seen == [TUESDAY, MONDAY]
