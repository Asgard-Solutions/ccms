"""
Clinic Profile service — per-location contact info + hours of operation.

Future relational schema:
    clinic_profiles (
      id                  UUID PRIMARY KEY,
      tenant_id           UUID NOT NULL,
      location_id         UUID NOT NULL UNIQUE,   -- 1:1 with locations.id
      name                VARCHAR(200) NOT NULL,
      address_line1       VARCHAR(200),
      address_line2       VARCHAR(200),
      city                VARCHAR(100),
      state               VARCHAR(64),
      postal_code         VARCHAR(20),
      country             VARCHAR(2),
      primary_phone       VARCHAR(40),
      secondary_phone     VARCHAR(40),
      email               VARCHAR(200),
      website             VARCHAR(300),
      timezone            VARCHAR(64) NOT NULL,
      notes               TEXT,
      created_at          TIMESTAMPTZ NOT NULL,
      updated_at          TIMESTAMPTZ NOT NULL,
      created_by          UUID,
      updated_by          UUID
    );

    clinic_hours (
      id                  UUID PRIMARY KEY,
      clinic_profile_id   UUID NOT NULL REFERENCES clinic_profiles(id) ON DELETE CASCADE,
      day_of_week         SMALLINT NOT NULL,      -- 0=Mon ... 6=Sun
      is_closed           BOOLEAN NOT NULL DEFAULT FALSE,
      intervals           JSONB NOT NULL DEFAULT '[]'
                                                -- [{open_time:"09:00", close_time:"13:00"}, ...]
                                                -- multiple intervals support lunch
                                                -- breaks & future holiday overrides
    );

MongoDB representation keeps `hours` embedded on the profile doc for simplicity
(each entry has day_of_week + is_closed + intervals[]). Postgres migration is
mechanical.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DAY_OF_WEEK = Literal[0, 1, 2, 3, 4, 5, 6]  # 0 = Monday
DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _hhmm_to_minutes(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


class HoursInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open_time: str = Field(description="HH:MM, 24-hour clock")
    close_time: str = Field(description="HH:MM, 24-hour clock; must be strictly after open_time")

    @field_validator("open_time", "close_time")
    @classmethod
    def _format(cls, v: str) -> str:
        if not HHMM_RE.match(v or ""):
            raise ValueError("must be HH:MM 24-hour format, e.g. 09:00 or 17:30")
        return v

    @model_validator(mode="after")
    def _ordered(self):
        if _hhmm_to_minutes(self.close_time) <= _hhmm_to_minutes(self.open_time):
            raise ValueError("close_time must be after open_time (no overnight intervals)")
        return self


class DayHours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day_of_week: DAY_OF_WEEK
    is_closed: bool = False
    intervals: list[HoursInterval] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self):
        if self.is_closed:
            if self.intervals:
                raise ValueError("day marked is_closed cannot have intervals")
            return self
        # Open day must have at least one interval; forbid overlaps.
        if not self.intervals:
            raise ValueError("day not closed must have at least one hours interval")
        sorted_ = sorted(self.intervals, key=lambda i: _hhmm_to_minutes(i.open_time))
        for a, b in zip(sorted_, sorted_[1:]):
            if _hhmm_to_minutes(b.open_time) < _hhmm_to_minutes(a.close_time):
                raise ValueError("intervals within a day must not overlap")
        return self


def _default_hours() -> list[DayHours]:
    # Mon–Fri 09:00–17:00, weekends closed. Frontend can override.
    return [
        DayHours(day_of_week=i, is_closed=(i >= 5),
                 intervals=([HoursInterval(open_time="09:00", close_time="17:00")] if i < 5 else []))
        for i in range(7)
    ]


class ClinicProfileBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=64)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default="US", max_length=2)
    primary_phone: str | None = Field(default=None, max_length=40)
    secondary_phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=300)
    timezone: str = Field(default="America/Los_Angeles", min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(v)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"unknown IANA timezone: {v}") from exc
        return v

    @model_validator(mode="after")
    def _normalize_phones(self):
        from core.phone import normalize_us_phone

        supplied = self.__pydantic_fields_set__
        for attr in ("primary_phone", "secondary_phone"):
            if attr not in supplied:
                continue
            value = getattr(self, attr, None)
            if value in (None, ""):
                setattr(self, attr, None)
                continue
            try:
                setattr(self, attr, normalize_us_phone(value))
            except ValueError as exc:
                raise ValueError(
                    f"{attr.replace('_', ' ').title()}: {exc}",
                ) from exc
        return self


class ClinicProfileCreate(ClinicProfileBase):
    location_id: str
    hours: list[DayHours] | None = None  # falls back to sensible defaults

    @model_validator(mode="after")
    def _hours_coverage(self):
        hours = self.hours or _default_hours()
        seen = {h.day_of_week for h in hours}
        if seen != set(range(7)):
            raise ValueError("hours must contain exactly one entry per day_of_week 0..6")
        self.hours = hours
        return self


class ClinicProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=64)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=2)
    primary_phone: str | None = Field(default=None, max_length=40)
    secondary_phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=300)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)
    hours: list[DayHours] | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(v)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"unknown IANA timezone: {v}") from exc
        return v

    @model_validator(mode="after")
    def _hours_coverage(self):
        if self.hours is not None:
            seen = {h.day_of_week for h in self.hours}
            if seen != set(range(7)):
                raise ValueError("hours must contain exactly one entry per day_of_week 0..6")
        return self

    @model_validator(mode="after")
    def _normalize_phones(self):
        from core.phone import normalize_us_phone

        supplied = self.__pydantic_fields_set__
        for attr in ("primary_phone", "secondary_phone"):
            if attr not in supplied:
                continue
            value = getattr(self, attr, None)
            if value in (None, ""):
                setattr(self, attr, None)
                continue
            try:
                setattr(self, attr, normalize_us_phone(value))
            except ValueError as exc:
                raise ValueError(
                    f"{attr.replace('_', ' ').title()}: {exc}",
                ) from exc
        return self


class ClinicProfilePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    location_id: str
    name: str
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    primary_phone: str | None = None
    secondary_phone: str | None = None
    email: str | None = None
    website: str | None = None
    timezone: str
    notes: str | None = None
    hours: list[DayHours]
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None
