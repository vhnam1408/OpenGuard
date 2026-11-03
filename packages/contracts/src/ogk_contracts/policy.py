"""Shared policy data (Python 3.11+, Pydantic 2); no enforcement or I/O.

Signature/HMAC creation, verification and version rollback checks belong to the
Server/Agent. Sign signing_payload(), which includes version and target scope;
carry the signature separately from this policy document.
"""

from __future__ import annotations

import json
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
)


Identifier = Annotated[
    str, StringConstraints(strict=True, min_length=1, pattern=r"^\S+$")
]
DaySchedule = Annotated[list[StrictBool], Field(min_length=48, max_length=48)]
WeekSchedule = Annotated[list[DaySchedule], Field(min_length=7, max_length=7)]
DailyQuotaSeconds = Annotated[int, Field(strict=True, ge=0, le=86_400)]
DomainName = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=3,
        max_length=253,
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    ),
]


class AppRule(BaseModel):
    """Executable basename plus SHA-256; paths are never transported."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, pattern=r'^[^\\/:*?"<>|\x00-\x1f\x7f]+$'),
    ]
    sha256: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")
    ]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value != value.strip() or value in (".", ".."):
            raise ValueError("name must be an executable basename")
        return value


class Policy(BaseModel):
    """Policy scoped to one child/device binding, with explicit daily quotas.

    schedule[0] is Monday; schedule[6] is Sunday. Slot 0 is 00:00-00:30
    and slot 47 is 23:30-24:00 in timezone. True allows use; False denies it.
    Weekday quotas apply Monday-Friday, weekend quotas Saturday-Sunday.
    Zero quota allows no usage. Domain entries are lowercase ASCII/IDNA names,
    never URLs or wildcard patterns; consumers match complete DNS labels.

    safety_domains are evaluated before ordinary allow/block rules. Mandatory
    safety entries are supplied and protected by Server/Agent, not this schema.
    On Windows, install tzdata to provide the IANA timezone database.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    policy_id: Identifier
    version: Annotated[int, Field(strict=True, ge=1)]
    device_id: Identifier
    child_id: Identifier
    binding_id: Identifier
    timezone: Annotated[str, StringConstraints(strict=True, min_length=1)]
    schedule: WeekSchedule
    quota_weekday_sec: DailyQuotaSeconds
    quota_weekend_sec: DailyQuotaSeconds
    allowed_apps: list[AppRule] = Field(default_factory=list)
    blocked_apps: list[AppRule] = Field(default_factory=list)
    allowed_domains: list[DomainName] = Field(default_factory=list)
    blocked_domains: list[DomainName] = Field(default_factory=list)
    safety_domains: list[DomainName] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """Require an available IANA timezone instead of a fixed UTC offset."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an available IANA timezone (install tzdata)") from exc
        return value

    def signing_payload(self) -> bytes:
        """Deterministic UTF-8 JSON for signing by other layers.

        All peers must use this serialization: sorted object keys, compact
        separators, literal Unicode and preserved array order. This is not a
        signature and does not establish authenticity on its own.
        """
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")


__all__ = ["AppRule", "Policy"]
