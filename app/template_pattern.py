"""Generic weekday patterns; no database access or real scheduled dates."""

from datetime import date, datetime, time, timedelta
from uuid import UUID

from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StrictInt,
    field_validator, model_validator,
)


class TemplateShift(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_id: UUID
    weekday: StrictInt = Field(ge=0, le=6)
    start_minute: StrictInt = Field(ge=0, le=1425)
    end_minute: StrictInt = Field(ge=0, le=1425)
    ends_next_day: StrictBool = False
    note: str = Field(default="", max_length=500)

    @field_validator("start_minute", "end_minute")
    @classmethod
    def quarter_hour(cls, value):
        if value % 15:
            raise ValueError("Choose a 15-minute time.")
        return value

    @model_validator(mode="after")
    def positive_interval(self):
        if not self.ends_next_day and self.end_minute <= self.start_minute:
            raise ValueError("End must follow start, or select Ends next day.")
        return self


class TemplateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    slots: list[TemplateShift] = Field(default_factory=list, max_length=500)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Enter a template name.")
        return value


def _week_start(value):
    if type(value) is not int or not 0 <= value <= 6:
        raise ValueError("Invalid template week-start day.")
    return value


def to_snapshot(draft: TemplateDraft, week_start_day: int):
    """Encode generic weekdays in the existing independent snapshot format."""
    start_day = _week_start(week_start_day)
    anchor = date(2000, 1, 3) + timedelta(days=start_day)
    slots = []
    for shift in draft.slots:
        day = anchor + timedelta(days=(shift.weekday-start_day) % 7)
        start = datetime.combine(day, time()) + timedelta(
            minutes=shift.start_minute
        )
        end = datetime.combine(day, time()) + timedelta(
            days=int(shift.ends_next_day), minutes=shift.end_minute
        )
        slots.append({
            "role_id": str(shift.role_id),
            "local_start": start.isoformat(timespec="seconds"),
            "local_end": end.isoformat(timespec="seconds"),
            "note": shift.note,
        })
    return {
        "week": {
            "week_start_date": anchor.isoformat(),
            "week_start_day": start_day,
        },
        "slots": slots,
    }


def from_snapshot(snapshot, name):
    """Read saved patterns without rounding or interpreting them in a timezone."""
    try:
        first = date.fromisoformat(snapshot["week"]["week_start_date"])
        start_day = _week_start(snapshot["week"]["week_start_day"])
        if first.weekday() != start_day:
            raise ValueError("Saved template week-start metadata is inconsistent.")
        values = snapshot["slots"]
        if not isinstance(values, list):
            raise ValueError("Saved template slots must be a list.")
        slots = []
        for value in values:
            start = datetime.fromisoformat(value["local_start"])
            end = datetime.fromisoformat(value["local_end"])
            if (
                start.tzinfo is not None or end.tzinfo is not None
                or start.second or start.microsecond
                or end.second or end.microsecond
            ):
                raise ValueError("Saved template endpoints must be exact local minutes.")
            if not first <= start.date() <= first+timedelta(days=6):
                raise ValueError("Saved shift starts outside its template week.")
            day_count = (end.date()-start.date()).days
            if day_count not in (0, 1):
                raise ValueError("Saved shift must end on the same or next day.")
            slots.append(TemplateShift(
                role_id=value["role_id"],
                weekday=start.weekday(),
                start_minute=start.hour*60+start.minute,
                end_minute=end.hour*60+end.minute,
                ends_next_day=day_count == 1,
                note=value.get("note", ""),
            ))
        return TemplateDraft(name=name, slots=slots)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Invalid saved template structure.") from error
