"""Transactional per-binding budget checkpoints; no activity history or tokens.

Production caller supplies the ACL-protected ProgramData state/agent.db path.
The installer must secure the directory before opening SQLite (including WAL).
"""

from pathlib import Path
import json
import sqlite3
import threading

from ogk_agent.time_control.counter import Checkpoint


class SQLiteCheckpointStore:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        with self._db:
            self._db.execute("""CREATE TABLE IF NOT EXISTS quota_checkpoint (
                binding_id TEXT PRIMARY KEY, budget_day TEXT NOT NULL,
                used_seconds REAL NOT NULL CHECK(used_seconds >= 0),
                trusted_utc TEXT NOT NULL, warned TEXT NOT NULL)""")

    def load(self, binding_id: str) -> Checkpoint | None:
        with self._lock:
            row = self._db.execute("""SELECT budget_day, used_seconds, trusted_utc, warned
                FROM quota_checkpoint WHERE binding_id = ?""", (binding_id,)).fetchone()
        return Checkpoint(*row[:3], tuple(json.loads(row[3]))) if row else None

    def save(self, binding_id: str, checkpoint: Checkpoint) -> None:
        with self._lock, self._db:
            self._db.execute("""INSERT INTO quota_checkpoint VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(binding_id) DO UPDATE SET budget_day=excluded.budget_day,
                used_seconds=excluded.used_seconds, trusted_utc=excluded.trusted_utc,
                warned=excluded.warned""", (binding_id, checkpoint.budget_day,
                checkpoint.used_seconds, checkpoint.trusted_utc, json.dumps(checkpoint.warned)))

    def close(self) -> None:
        with self._lock:
            self._db.close()
