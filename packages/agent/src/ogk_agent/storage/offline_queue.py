"""Durable per-binding event queue and bounded HTTPS batch synchronization.

Default storage: %ProgramData%/OpenGuardKids/state/agent.db. The installer must
provision the state directory with SYSTEM/Administrators ACLs (including WAL).
The runtime calls configure_binding only after trusted enrollment/lifecycle
checks, and passes prepare_sync to check lifecycle, refresh auth, then policy
BEFORE each upload. Transport and the existing service are deliberately decoupled.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import random
import sqlite3
import threading
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from ogk_contracts.events import Event
from ogk_contracts.sync import EventBatch, EventBatchAck, Key
from pydantic import TypeAdapter


RETENTION_SECONDS = 90 * 86400
_key = TypeAdapter(Key)


def default_database_path() -> Path:
    program_data = os.environ.get("ProgramData")
    if not program_data:
        raise OSError("ProgramData must be set, or supply an explicit database path")
    return Path(program_data) / "OpenGuardKids" / "state" / "agent.db"


class OfflineQueue:
    """Thread-safe queue, compatible with the quota checkpoint database.

    Batch/event IDs are committed before upload. Timeout, process restart and
    lost responses retain the same IDs. Concurrent uploaders may resend a batch;
    the server's unique (binding_id, data_epoch, idempotency_key) removes repeats.
    """

    def __init__(self, path: str | Path | None = None, *,
                 utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.path = Path(path) if path is not None else default_database_path()
        if not self.path.parent.is_dir():
            raise FileNotFoundError("Provision the protected state directory before opening the queue")
        self._utcnow = utcnow
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), timeout=5, check_same_thread=False)
        try:
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA secure_delete=ON")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS offline_binding (
                    binding_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
                    child_id TEXT NOT NULL, data_epoch INTEGER NOT NULL CHECK(data_epoch >= 1),
                    active INTEGER NOT NULL CHECK(active IN (0, 1)));
                CREATE TABLE IF NOT EXISTS offline_event (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    binding_id TEXT NOT NULL REFERENCES offline_binding(binding_id),
                    data_epoch INTEGER NOT NULL CHECK(data_epoch >= 1),
                    idempotency_key TEXT NOT NULL, occurred_at REAL NOT NULL,
                    payload TEXT NOT NULL, batch_id TEXT,
                    UNIQUE(binding_id, data_epoch, idempotency_key));
                CREATE INDEX IF NOT EXISTS ix_offline_event_batch
                    ON offline_event(binding_id, data_epoch, batch_id, sequence);
            """)
        except BaseException:
            self._db.close()
            raise

    def _binding(self, binding_id: str):
        row = self._db.execute("""SELECT device_id, child_id, data_epoch, active
            FROM offline_binding WHERE binding_id=?""", (binding_id,)).fetchone()
        if row is None or not row[3]:
            raise ValueError("Binding is unknown or revoked")
        return row

    def configure_binding(self, binding_id: str, *, device_id: str, child_id: str, data_epoch: int) -> None:
        """Advance a trusted epoch and discard older queued data atomically.

        Revoked bindings cannot be reactivated; a fresh enrollment needs a new ID.
        Epoch metadata is persistent so stale producers cannot resurrect history.
        """
        for value in (binding_id, device_id, child_id):
            _key.validate_python(value)
        if type(data_epoch) is not int or data_epoch < 1:
            raise ValueError("data_epoch must be a positive integer")
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("""SELECT device_id, child_id, data_epoch, active
                FROM offline_binding WHERE binding_id=?""", (binding_id,)).fetchone()
            if row is not None:
                if row[:2] != (device_id, child_id) or data_epoch < row[2] or not row[3]:
                    raise ValueError("Binding identity, epoch rollback or revocation mismatch")
                if data_epoch > row[2]:
                    self._db.execute("DELETE FROM offline_event WHERE binding_id=?", (binding_id,))
                self._db.execute("UPDATE offline_binding SET data_epoch=? WHERE binding_id=?",
                                 (data_epoch, binding_id))
            else:
                self._db.execute("INSERT INTO offline_binding VALUES (?, ?, ?, ?, 1)",
                                 (binding_id, device_id, child_id, data_epoch))

    def revoke_binding(self, binding_id: str) -> None:
        _key.validate_python(binding_id)
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            # Keep a tombstone even if revocation arrives before initialization.
            self._db.execute("INSERT OR IGNORE INTO offline_binding VALUES (?, '', '', 1, 0)", (binding_id,))
            self._db.execute("UPDATE offline_binding SET active=0 WHERE binding_id=?", (binding_id,))
            self._db.execute("DELETE FROM offline_event WHERE binding_id=?", (binding_id,))

    def enqueue(self, event: Event | dict, *, binding_id: str, data_epoch: int,
                idempotency_key: str | None = None) -> str:
        """Commit an event before attempting network I/O, even while online."""
        event = Event.model_validate(event)
        key = _key.validate_python(idempotency_key if idempotency_key is not None else str(uuid4()))
        now = self._utcnow()
        if not now - timedelta(seconds=RETENTION_SECONDS) < event.ts <= now + timedelta(minutes=5):
            raise ValueError("Event is outside the retention/clock window")
        payload = event.model_dump_json()
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            device_id, child_id, epoch, _ = self._binding(binding_id)
            if type(data_epoch) is not int or data_epoch != epoch:
                raise ValueError("Event epoch differs from current trusted epoch")
            if (event.device_id, event.child_id) != (device_id, child_id):
                raise ValueError("Event target differs from binding")
            old = self._db.execute("""SELECT payload FROM offline_event
                WHERE binding_id=? AND data_epoch=? AND idempotency_key=?""", (binding_id, epoch, key)).fetchone()
            if old is not None:
                if old[0] != payload:
                    raise ValueError("Idempotency key already used for another event")
            else:
                self._db.execute("""INSERT INTO offline_event
                    (binding_id, data_epoch, idempotency_key, occurred_at, payload)
                    VALUES (?, ?, ?, ?, ?)""", (binding_id, epoch, key, event.ts.timestamp(), payload))
        return key

    def prune(self) -> int:
        """Retention uses occurrence time, including time spent offline."""
        with self._lock, self._db:
            return self._db.execute("DELETE FROM offline_event WHERE occurred_at <= ?",
                                    (self._utcnow().timestamp() - RETENTION_SECONDS,)).rowcount

    def pending_count(self, binding_id: str) -> int:
        self.prune()
        with self._lock:
            return self._db.execute("SELECT count(*) FROM offline_event WHERE binding_id=?",
                                    (binding_id,)).fetchone()[0]

    def next_batch(self, binding_id: str, *, batch_size: int = 100) -> EventBatch | None:
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        self.prune()
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            _, _, epoch, _ = self._binding(binding_id)
            first = self._db.execute("""SELECT batch_id FROM offline_event
                WHERE binding_id=? AND data_epoch=? ORDER BY sequence LIMIT 1""", (binding_id, epoch)).fetchone()
            if first is None:
                return None
            batch_id = first[0]
            if batch_id is None:
                batch_id = str(uuid4())
                self._db.execute("""UPDATE offline_event SET batch_id=? WHERE sequence IN (
                    SELECT sequence FROM offline_event WHERE binding_id=? AND data_epoch=?
                    AND batch_id IS NULL ORDER BY sequence LIMIT ?)""", (batch_id, binding_id, epoch, batch_size))
            # A persisted batch keeps its original membership when batch_size changes.
            rows = self._db.execute("""SELECT idempotency_key, payload FROM offline_event
                WHERE binding_id=? AND data_epoch=? AND batch_id=? ORDER BY sequence""",
                (binding_id, epoch, batch_id)).fetchall()
            return EventBatch(batch_id=batch_id, binding_id=binding_id, data_epoch=epoch,
                              events=[{"idempotency_key": key, "event": json.loads(payload)} for key, payload in rows])

    def acknowledge(self, batch: EventBatch, ack: EventBatchAck | dict) -> int:
        ack = EventBatchAck.model_validate(ack)
        if (ack.batch_id, ack.binding_id, ack.data_epoch) != (batch.batch_id, batch.binding_id, batch.data_epoch):
            raise ValueError("ACK does not match uploaded batch")
        keys = set(ack.accepted_keys)
        if len(keys) != len(ack.accepted_keys) or not keys <= {item.idempotency_key for item in batch.events}:
            raise ValueError("ACK contains duplicate or unknown keys")
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self._binding(batch.binding_id)[2] != batch.data_epoch:
                raise ValueError("Lifecycle changed during upload")
            removed = 0
            for key in keys:
                removed += self._db.execute("""DELETE FROM offline_event WHERE binding_id=?
                    AND data_epoch=? AND batch_id=? AND idempotency_key=?""",
                    (batch.binding_id, batch.data_epoch, batch.batch_id, key)).rowcount
            return removed

    def sync_once(self, binding_id: str, send_batch: Callable[[EventBatch], EventBatchAck | dict], *,
                  prepare_sync: Callable[[], bool], batch_size: int = 100) -> int:
        """Upload one batch; failures retain every unacknowledged event.

        prepare_sync must check lifecycle/revocation first, refresh credentials,
        sync policy, and apply configure_binding/revoke_binding before returning
        True. False pauses upload. Never hold SQLite locks during network I/O.
        """
        if not prepare_sync():
            return 0
        batch = self.next_batch(binding_id, batch_size=batch_size)
        if batch is None:
            return 0
        ack = send_batch(batch)
        return self.acknowledge(batch, ack)

    def sync_forever(self, binding_id: str, send_batch: Callable[[EventBatch], EventBatchAck | dict], *,
                     prepare_sync: Callable[[], bool], stop: threading.Event) -> None:
        """Run in a runtime-owned worker; reconnect retries use bounded jitter.

        OSError covers HTTPS/HTTP failures; malformed ACK/epoch errors also pause
        without deleting data. Each retry repeats prepare_sync. The caller owns
        worker health/logging; no tokens or activity are logged by this module.
        """
        delay = 1.0
        while not stop.is_set():
            try:
                removed = self.sync_once(binding_id, send_batch, prepare_sync=prepare_sync)
            except (OSError, ValueError, sqlite3.OperationalError):
                if stop.wait(random.uniform(delay / 2, delay)):
                    return
                delay = min(delay * 2, 60)
            else:
                delay = 1.0
                # Drain successful batches immediately, poll idle/paused queues in 5s.
                if stop.wait(0.01 if removed else 5):
                    return

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None  # Do not forward a bearer token to a redirected host.


class HTTPSBatchUploader:
    """POST /api/v1/events with certificate verification and fresh bearer token.

    Supply an HTTPSHandler with a pinned SSLContext via opener when integrating
    deployment-specific certificate pinning. Default uses OS CA verification.
    """

    def __init__(self, base_url: str, token_provider: Callable[[], str], *, timeout: float = 10,
                 opener=None) -> None:
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("base_url must be an HTTPS origin without credentials")
        if not 0 < timeout <= 60:
            raise ValueError("timeout must be between 0 and 60 seconds")
        self.url = base_url.rstrip("/") + "/api/v1/events"
        self.token_provider = token_provider
        self.timeout = timeout
        self.opener = opener or build_opener(_NoRedirect())

    def __call__(self, batch: EventBatch) -> EventBatchAck:
        token = self.token_provider()
        if not token or "\r" in token or "\n" in token:
            raise ValueError("Invalid bearer token")
        request = Request(self.url, data=batch.model_dump_json().encode("utf-8"), method="POST",
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            if response.status != 200:
                raise OSError("Server did not acknowledge the event batch")
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Batch ACK is too large")
            return EventBatchAck.model_validate_json(raw)
