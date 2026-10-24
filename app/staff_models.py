"""Explicit staff-edit inputs; identity and ownership are server-controlled."""

from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StrictInt,
    field_validator, model_validator,
)


class StaffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("name", check_fields=False)
    @classmethod
    def clean_name(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("A name is required.")
        return value


class CreateStaff(StaffInput):
    name: str = Field(min_length=1, max_length=200)


class EditStaff(StaffInput):
    expected_revision: StrictInt = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    active: StrictBool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if self.name is None and self.active is None:
            raise ValueError("A change is required.")
        return self


class SetQualification(StaffInput):
    expected_revision: StrictInt = Field(ge=0)
    active: StrictBool


class CreateRole(CreateStaff):
    display_order: StrictInt = Field(default=0, ge=0, le=9999)


class EditRole(EditStaff):
    display_order: StrictInt | None = Field(default=None, ge=0, le=9999)

    @model_validator(mode="after")
    def require_change(self):
        if self.name is None and self.active is None and self.display_order is None:
            raise ValueError("A change is required.")
        return self
