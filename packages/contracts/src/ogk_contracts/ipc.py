"""Tray/Service IPC v1: bounded UTF-8 JSON lines, independent of Windows/GUI."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from uuid import UUID, uuid4

VERSION = 1
MAX_FRAME_BYTES = 64 * 1024  # Includes the newline delimiter.
PIPE_NAME = r"\\.\pipe\OpenGuardKids.Tray.v1"
CLIENT_TYPES = frozenset({"status.get", "time.request", "tray.presence"})
SERVER_TYPES = frozenset({
    "status", "quota_warning", "blocked_app", "blocked_domain", "locked",
    "time.request.result", "error",
})


class ProtocolError(ValueError):
    """Never include raw payloads in the exception or application logs."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True)
class Message:
    type: str
    payload: dict[str, Any]
    request_id: str
    version: int = VERSION

    @classmethod
    def create(cls, kind: str, payload: dict[str, Any]) -> Message:
        return cls(kind, payload, str(uuid4()))

    def encode(self) -> bytes:
        try:
            frame = (json.dumps({
                "version": self.version, "request_id": self.request_id,
                "type": self.type, "payload": self.payload,
            }, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise ProtocolError("Invalid outgoing JSON") from None
        if len(frame) > MAX_FRAME_BYTES:
            raise ProtocolError("Frame too large")
        # Use the same envelope validation on both ends.
        self.decode(frame[:-1])
        return frame

    @classmethod
    def decode(cls, frame: bytes) -> Message:
        if len(frame) + 1 > MAX_FRAME_BYTES:
            raise ProtocolError("Frame too large")
        try:
            obj = json.loads(frame.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=lambda _: _reject_constant())
            if not isinstance(obj, dict) or set(obj) != {"version", "request_id", "type", "payload"}:
                raise ProtocolError("Invalid envelope")
            if type(obj["version"]) is not int or obj["version"] != VERSION:
                raise ProtocolError("Unsupported version")
            if not isinstance(obj["request_id"], str) or len(obj["request_id"]) != 36:
                raise ProtocolError("Invalid request ID")
            UUID(obj["request_id"])
            if not isinstance(obj["type"], str) or obj["type"] not in CLIENT_TYPES | SERVER_TYPES:
                raise ProtocolError("Unsupported message type")
            if not isinstance(obj["payload"], dict):
                raise ProtocolError("Invalid payload")
            return cls(**obj)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ProtocolError("Invalid IPC message") from None


def _reject_constant() -> None:
    raise ProtocolError("Non-finite JSON value")


class FrameDecoder:
    """Handle split UTF-8 characters/frames and multiple frames per pipe read."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[Message]:
        self._buffer.extend(chunk)
        messages = []
        while True:
            end = self._buffer.find(b"\n")
            if end < 0:
                if len(self._buffer) >= MAX_FRAME_BYTES:
                    raise ProtocolError("Unterminated or oversized frame")
                return messages
            if end + 1 > MAX_FRAME_BYTES:
                raise ProtocolError("Frame too large")
            messages.append(Message.decode(bytes(self._buffer[:end])))
            del self._buffer[:end + 1]
