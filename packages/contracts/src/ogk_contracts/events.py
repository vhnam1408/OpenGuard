"""Activity payloads from SRS 6.1 (Python 3.11+, Pydantic 2).

Binding, credential and policy ownership checks belong to the authenticated
receiver. Batch IDs, data epochs and idempotency keys belong to the envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)


Identifier = Annotated[
    str, StringConstraints(strict=True, min_length=1, pattern=r"^\S+$")
]
Subject = Annotated[str, StringConstraints(strict=True, min_length=1)]
DurationSeconds = Annotated[
    float, Field(strict=True, ge=0, allow_inf_nan=False)
]


class EventType(str, Enum):
    """The nine event types permitted on the wire."""

    APP_START = "app_start"
    APP_STOP = "app_stop"
    DOMAIN_QUERY = "domain_query"
    BLOCKED_APP = "blocked_app"
    BLOCKED_DOMAIN = "blocked_domain"
    QUOTA_WARNING = "quota_warning"
    LOCKED = "locked"
    UNLOCK_REQUEST = "unlock_request"
    OVERRIDE_GRANTED = "override_granted"


class Event(BaseModel):
    """Exactly seven required fields; unknown fields are rejected.

    Producers must supply an app name or registrable domain, never a window
    title or personal content. Domain extraction requires a public suffix list
    at the producer; syntax validation here cannot determine registrability.
    Use duration_sec=0 for events that do not measure a time interval.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    ts: AwareDatetime
    device_id: Identifier
    child_id: Identifier
    type: EventType
    subject: Subject
    duration_sec: DurationSeconds
    policy_id: Identifier

    @field_validator("ts", mode="before")
    @classmethod
    def reject_numeric_timestamp(cls, value: object) -> object:
        """Accept datetime/ISO 8601 input without coercing Unix timestamps."""
        if not isinstance(value, (datetime, str)):
            raise ValueError("ts must be a timezone-aware datetime or ISO 8601 string")
        if isinstance(value, str) and "T" not in value and "t" not in value:
            raise ValueError("ts must include an ISO 8601 date and time")
        return value

    @field_validator("ts")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        """Preserve the instant, including when a queued payload is resent."""
        return value.astimezone(timezone.utc)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str, info: ValidationInfo) -> str:
        """Reject URL/path/query syntax and control characters."""
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("subject must not contain surrounding whitespace or control characters")
        if any(char in value for char in "/\\:?#=&@%"):
            raise ValueError("subject must be an app name or domain, without URL, path or query")
        if info.data.get("type") in (EventType.DOMAIN_QUERY, EventType.BLOCKED_DOMAIN):
            domain = value.removesuffix(".").lower()
            labels = domain.split(".")
            if len(domain) > 253 or len(labels) < 2 or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels
            ) or all(label.isdigit() for label in labels):
                raise ValueError("domain subject must be a DNS name in ASCII/IDNA form")
            return domain
        return value


__all__ = ["Event", "EventType"]
