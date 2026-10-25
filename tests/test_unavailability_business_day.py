from copy import deepcopy
from datetime import date, timedelta

import pytest

from app.unavailability_business_day import (
    bulk_calendar_additions, calendar_address, merge_business_edits,
)

MONDAY = date(2027, 1, 4)


@pytest.mark.parametrize("rollover", [0, 300, 420])
def test_week_addresses_are_unique_and_cover_exact_cells(rollover):
    addresses = [
        calendar_address(MONDAY+timedelta(days=offset), cell, rollover)
        for offset in range(7)
        for cell in range(96)
    ]
    assert len(set(addresses)) == 672
    assert addresses[0] == (MONDAY, rollover//15)
    assert addresses[-1] == (
        MONDAY+timedelta(days=6 if rollover == 0 else 7),
        (rollover//15-1) % 96,
    )


def test_sunday_early_hours_are_stored_on_following_monday():
    sunday = MONDAY+timedelta(days=6)
    assert calendar_address(sunday, 84, 300) == (
        MONDAY+timedelta(days=7), 8,
    )


def test_edit_preserves_cells_outside_visible_business_week():
    following = MONDAY+timedelta(days=7)
    existing = {MONDAY: [0, 8, 20], following: [8, 20, 95]}
    original = deepcopy(existing)
    changes = merge_business_edits(MONDAY, [
        (MONDAY, 0, False),
        (MONDAY+timedelta(days=6), 84, False),
    ], existing)
    assert changes == {MONDAY: (0, 8), following: (20, 95)}
    assert existing == original


def test_actual_calendar_lock_blocks_next_day_cell_change():
    following = MONDAY+timedelta(days=7)
    with pytest.raises(ValueError, match="locked"):
        merge_business_edits(MONDAY, [
            (MONDAY+timedelta(days=6), 84, True),
        ], {}, locked_dates={following})


def test_no_change_on_locked_date_is_not_a_write():
    assert merge_business_edits(
        MONDAY, [(MONDAY, 0, True)],
        {MONDAY: [20]}, locked_dates={MONDAY},
    ) == {}


def test_failed_merge_does_not_mutate_input():
    existing = {MONDAY: [20]}
    original = deepcopy(existing)
    with pytest.raises(ValueError):
        merge_business_edits(MONDAY, [
            (MONDAY, 1, True),
            (MONDAY+timedelta(days=1), 0, True),
        ], existing, locked_dates={MONDAY+timedelta(days=1)})
    assert existing == original


def test_mass_edit_includes_last_business_days_next_calendar_date():
    sunday = MONDAY+timedelta(days=6)
    weekly = {"6": [84, 85]}
    original = deepcopy(weekly)
    assert bulk_calendar_additions(sunday, sunday, weekly) == {
        sunday+timedelta(days=1): (8, 9),
    }
    assert weekly == original


def test_mass_edit_maps_both_parts_of_business_day_without_clearing():
    assert bulk_calendar_additions(MONDAY, MONDAY, {"0": [0, 76, 84]}) == {
        MONDAY: (20,),
        MONDAY+timedelta(days=1): (0, 8),
    }
    assert bulk_calendar_additions(MONDAY, MONDAY, {}) == {}


@pytest.mark.parametrize("edit", [
    (MONDAY, True, True),
    (MONDAY, 96, True),
    (MONDAY, 0, 1),
    (MONDAY+timedelta(days=7), 0, True),
])
def test_invalid_edits_rejected(edit):
    with pytest.raises(ValueError):
        merge_business_edits(MONDAY, [edit], {})


def test_duplicate_cell_rejected():
    with pytest.raises(ValueError):
        merge_business_edits(
            MONDAY, [(MONDAY, 0, True), (MONDAY, 0, False)], {},
        )


def test_storage_date_limit_cannot_be_exceeded_by_carry():
    with pytest.raises(ValueError):
        calendar_address(date(9998, 12, 31), 84)


def test_bulk_range_limit():
    with pytest.raises(ValueError):
        bulk_calendar_additions(
            MONDAY, MONDAY+timedelta(days=366), {"0": [0]},
        )
