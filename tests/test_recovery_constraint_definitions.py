import pytest

from scripts.recovery_constraint_definitions import (
    KNOWN, normalize_constraint_definition,
)


@pytest.mark.parametrize("identity,spec", list(KNOWN.items()))
def test_exact_observed_groupings_match(identity, spec):
    column, maximum = spec
    nested = (
        f"CHECK (((({column} >= 0) AND ({column} <= {maximum})) "
        f"AND (({column} % 15) = 0)))"
    )
    flat = (
        f"CHECK ((({column} >= 0) AND ({column} <= {maximum}) "
        f"AND (({column} % 15) = 0)))"
    )
    assert normalize_constraint_definition(*identity, nested) == flat
    assert normalize_constraint_definition(*identity, flat) == flat


@pytest.mark.parametrize("identity,spec", list(KNOWN.items()))
@pytest.mark.parametrize("old,new", [
    (">= 0", ">= 15"),
    (" <= ", " < "),
    (" AND ", " OR "),
    ("% 15", "% 30"),
    ("= 0", "= 1"),
])
def test_changed_semantics_are_never_normalized(identity, spec, old, new):
    column, maximum = spec
    original = (
        f"CHECK (((({column} >= 0) AND ({column} <= {maximum})) "
        f"AND (({column} % 15) = 0)))"
    )
    changed = original.replace(old, new)
    assert changed != original
    assert normalize_constraint_definition(*identity, changed) == changed


def test_unknown_constraint_is_untouched():
    definition = (
        "CHECK ((((business_day_start_minute >= 0) "
        "AND (business_day_start_minute <= 1439)) "
        "AND ((business_day_start_minute % 15) = 0)))"
    )
    assert normalize_constraint_definition(
        "location", "unrecognized_constraint", definition,
    ) == definition
