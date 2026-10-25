"""Narrow normalization of six known quarter-hour CHECK definitions.

Only the two exact observed forms are equivalent here. Do not remove
parentheses generally or normalize arbitrary SQL expressions.
"""

KNOWN = {
    ("location", "location_business_day_start_minute_check"):
        ("business_day_start_minute", 1439),
    ("location", "location_minimum_rest_minutes_check"):
        ("minimum_rest_minutes", 1440),
    ("location", "location_rest_window_end_minute_check"):
        ("rest_window_end_minute", 1439),
    ("location", "location_rest_window_start_minute_check"):
        ("rest_window_start_minute", 1439),
    ("schedule_template", "schedule_template_business_day_start_minute_check"):
        ("business_day_start_minute", 1439),
    ("schedule_week", "schedule_week_business_day_start_minute_check"):
        ("business_day_start_minute", 1439),
}


def normalize_constraint_definition(table, name, definition):
    spec = KNOWN.get((table, name))
    if spec is None:
        return definition
    column, maximum = spec
    nested = (
        f"CHECK (((({column} >= 0) AND ({column} <= {maximum})) "
        f"AND (({column} % 15) = 0)))"
    )
    flat = (
        f"CHECK ((({column} >= 0) AND ({column} <= {maximum}) "
        f"AND (({column} % 15) = 0)))"
    )
    return flat if definition == nested else definition
