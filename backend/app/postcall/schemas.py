"""Strict schema for post-call structured extraction.

The LLM's raw JSON output is validated against this model before it's ever
written to the database -- if validation fails we log and keep the original
transcript, we never store garbage.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class CallExtraction(BaseModel):
    intent: str
    customer_name: str | None = None
    customer_phone: str | None = None
    language: str
    service: str | None = None
    appointment_date: str | None = None
    appointment_time: str | None = None
    outcome: str
    requires_followup: bool = False
    summary: str

    @field_validator("appointment_date")
    @classmethod
    def _validate_date(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _DATE_RE.match(v):
            raise ValueError(f"appointment_date must be YYYY-MM-DD, got {v!r}")
        return v

    @field_validator("appointment_time")
    @classmethod
    def _validate_time(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _TIME_RE.match(v):
            raise ValueError(f"appointment_time must be HH:MM (24h), got {v!r}")
        return v

    @field_validator("customer_phone")
    @classmethod
    def _normalize_phone(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        digits = re.sub(r"\D", "", v)
        return digits or None
