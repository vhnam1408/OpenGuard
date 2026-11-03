"""Realtime commands and offline event envelopes (activity stays seven fields)."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ogk_contracts.events import Event


Key = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^\S+$")]


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommandRequest(Envelope):
    binding_id: Key
    idempotency_key: Key
    type: Literal["lock_now", "add_time"]
    seconds: int = Field(default=0, strict=True, ge=0, le=86400)
    ttl_seconds: int = Field(default=60, strict=True, ge=5, le=300)

    @model_validator(mode="after")
    def check_seconds(self):
        if (self.type == "lock_now" and self.seconds != 0) or (self.type == "add_time" and self.seconds == 0):
            raise ValueError("lock_now requires seconds=0; add_time requires seconds>0")
        return self


class CommandAck(Envelope):
    type: Literal["ack"]
    command_id: Key
    status: Literal["Applied", "Rejected", "Expired"]


class QueuedEvent(Envelope):
    idempotency_key: Key
    event: Event


class EventBatch(Envelope):
    protocol_version: Literal[1] = 1
    batch_id: Key
    binding_id: Key
    data_epoch: int = Field(strict=True, ge=1)
    events: list[QueuedEvent] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_keys(self):
        keys = [item.idempotency_key for item in self.events]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate idempotency keys in batch")
        return self


class EventBatchAck(Envelope):
    batch_id: Key
    binding_id: Key
    data_epoch: int = Field(strict=True, ge=1)
    accepted_keys: list[Key] = Field(max_length=100)
    server_time: datetime
