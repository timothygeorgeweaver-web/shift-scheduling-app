from datetime import date, datetime, timedelta

import pytest

from app.planner_logic import (
    cells_to_intervals,
    intervals_to_cells,
    normalize_cells,
    normalize_template,
    preview_additions,
    toggle_cell,
)


def test_no_blocks_means_no_unavailable_intervals():
    assert cells_to_intervals([]) == ()


def test_exact_first_and_last_cells():
    assert cells_to_intervals([0, 95]) == ((0, 15), (1425, 1440))


def test_adjacent_cells_merge_without_filling_available_gaps():
    assert cells_to_intervals([5, 1, 0, 1, 4]) == ((0, 30), (60, 90))


def test_full_day_round_trip():
    cells = tuple(range(96))
    assert cells_to_intervals(cells) == ((0, 1440),)
    assert intervals_to_cells(((0, 1440),)) == cells


def test_disjoint_and_overlapping_intervals_normalize_exactly():
    assert intervals_to_cells(((0, 30), (15, 45), (60, 75))) == (0, 1, 2, 4)


@pytest.mark.parametrize("values", [
    [True], [False], [-1], [96], [1.0], ["1"], "12", None,
])
def test_invalid_cells_are_rejected(values):
    with pytest.raises(ValueError):
        normalize_cells(values)


@pytest.mark.parametrize("intervals", [
    ((1, 15),), ((0, 16),), ((15, 15),), ((1200, 120),),
    ((0, 1441),), ((True, 15),), ((0, 15, 30),),
])
def test_invalid_or_nonquarter_hour_intervals_are_not_rounded(intervals):
    with pytest.raises(ValueError):
        intervals_to_cells(intervals)


def test_click_twice_restores_original_cells_without_mutating_input():
    original = [4, 5]
    selected = toggle_cell(original, 6)
    assert selected == (4, 5, 6)
    assert toggle_cell(selected, 6) == (4, 5)
    assert toggle_cell(original, 4) == (5,)
    assert original == [4, 5]


def test_blank_mass_template_has_seven_empty_weekdays():
    assert normalize_template({}) == {str(day): () for day in range(7)}


@pytest.mark.parametrize("template", [
    {"7": []}, {"Monday": []}, {0: []}, {"0": [96]}, [],
])
def test_invalid_weekly_template_is_rejected(template):
    with pytest.raises(ValueError):
        normalize_template(template)


def test_approved_may_through_june_example_uses_inclusive_dates():
    result = preview_additions(
        date(2026, 5, 1), date(2026, 6, 15),
        {"0": list(range(36, 48))}, {},
    )
    assert [change.day for change in result.changes] == [
        date(2026, 5, 4), date(2026, 5, 11), date(2026, 5, 18),
        date(2026, 5, 25), date(2026, 6, 1), date(2026, 6, 8),
        date(2026, 6, 15),
    ]
    assert all(cells_to_intervals(change.after) == ((540, 720),)
               for change in result.changes)


def test_single_date_range_includes_both_boundaries():
    monday = date(2026, 6, 15)
    result = preview_additions(monday, monday, {"0": [0]}, {})
    assert len(result.changes) == 1
    assert result.changes[0].day == monday


def test_additive_edit_preserves_preexisting_blocks_and_other_weekdays():
    monday = date(2026, 6, 8)
    friday = date(2026, 6, 12)
    existing = {monday: [4, 5], friday: [60, 61]}
    result = preview_additions(
        monday, friday, {"0": [5, 6]}, existing,
    )
    assert len(result.changes) == 1
    assert result.changes[0].before == (4, 5)
    assert result.changes[0].after == (4, 5, 6)
    assert existing == {monday: [4, 5], friday: [60, 61]}


def test_reapplying_same_template_has_no_effective_changes():
    monday = date(2026, 6, 8)
    first = preview_additions(monday, monday, {"0": [4, 5]}, {})
    saved = {change.day: change.after for change in first.changes}
    again = preview_additions(monday, monday, {"0": [4, 5]}, saved)
    assert again.changes == ()


def test_locked_dates_are_reported_and_never_modified():
    first = date(2026, 6, 8)
    next_week = first + timedelta(days=7)
    existing = {first: [70]}
    result = preview_additions(
        first, next_week, {"0": [4, 5]}, existing, {first},
    )
    assert result.skipped_locked_dates == (first,)
    assert [change.day for change in result.changes] == [next_week]
    assert existing[first] == [70]


def test_blank_template_preserves_all_dates_including_locked_dates():
    monday = date(2026, 6, 8)
    result = preview_additions(monday, monday, {}, {monday: [4]}, {monday})
    assert result.changes == ()
    assert result.skipped_locked_dates == ()


def test_overnight_template_does_not_add_outside_selected_dates():
    sunday = date(2026, 6, 14)
    monday = date(2026, 6, 15)
    template = {"6": [94, 95], "0": [0, 1]}
    sunday_only = preview_additions(sunday, sunday, template, {})
    assert [(change.day, change.after) for change in sunday_only.changes] == [
        (sunday, (94, 95)),
    ]
    both = preview_additions(sunday, monday, template, {})
    assert [(change.day, change.after) for change in both.changes] == [
        (sunday, (94, 95)), (monday, (0, 1)),
    ]


def test_one_date_can_be_corrected_without_changing_other_dates():
    first = date(2026, 6, 8)
    second = first + timedelta(days=7)
    saved = {first: (4, 5), second: (4, 5)}
    saved[first] = toggle_cell(saved[first], 4)
    assert saved[first] == (5,)
    assert saved[second] == (4, 5)


def test_range_crosses_year_boundary():
    result = preview_additions(
        date(2026, 12, 31), date(2027, 1, 1),
        {"3": [95], "4": [0]}, {},
    )
    assert [change.day for change in result.changes] == [
        date(2026, 12, 31), date(2027, 1, 1),
    ]


def test_final_supported_python_date_does_not_overflow():
    last = date.max
    result = preview_additions(last, last, {str(last.weekday()): [95]}, {})
    assert result.changes[0].day == last


@pytest.mark.parametrize("first,last", [
    (date(2026, 6, 9), date(2026, 6, 8)),
    ("2026-06-08", date(2026, 6, 9)),
    (datetime(2026, 6, 8), date(2026, 6, 9)),
])
def test_invalid_date_ranges_are_rejected(first, last):
    with pytest.raises(ValueError):
        preview_additions(first, last, {}, {})
