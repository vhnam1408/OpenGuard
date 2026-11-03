"""TIME-01..04: monotonic accounting; the last 60 seconds are the grace period.

One counter per binding, owned by one service worker. Session samples must come
from Windows, not IPC payloads. tick() returns a lock *request*: the interactive
session helper must perform/confirm the lock, never the Session-0 service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import math
import time
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

LOG = logging.getLogger(__name__)
IDLE_LIMIT = 300.0
WARNINGS = (600, 300, 60)


@dataclass(frozen=True)
class SessionSample:
    windows_sid: str
    session_id: int
    idle_seconds: float
    active: bool = True
    locked: bool = False
    suspended: bool = False


@dataclass(frozen=True)
class Checkpoint:
    budget_day: str
    used_seconds: float
    trusted_utc: str
    warned: tuple[int, ...] = ()


class CheckpointStore(Protocol):
    def load(self, binding_id: str) -> Checkpoint | None: ...
    def save(self, binding_id: str, checkpoint: Checkpoint) -> None: ...


@dataclass(frozen=True)
class CounterResult:
    used_seconds: float
    remaining_seconds: int
    warnings: tuple[int, ...]
    grace: bool
    lock_required: bool
    reason: str
    clock_changed: bool


class UsageCounter:
    def __init__(
        self, *, binding_id: str, windows_sid: str, session_id: int,
        quota_seconds: int, store: CheckpointStore,
        timezone_name: str = "Asia/Ho_Chi_Minh",
        schedule: tuple[tuple[bool, ...], ...] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_sample_gap: float = 5.0,
    ) -> None:
        if type(quota_seconds) is not int or not 0 <= quota_seconds <= 86400:
            raise ValueError("Invalid daily quota")
        if (session_id <= 0 or not windows_sid or not binding_id
                or not math.isfinite(max_sample_gap) or max_sample_gap <= 0):
            raise ValueError("Invalid binding or sampling interval")
        if schedule is not None and (
            len(schedule) != 7 or any(len(day) != 48 for day in schedule)
            or any(type(slot) is not bool for day in schedule for slot in day)
        ):
            raise ValueError("Schedule must be a 7x48 boolean matrix")
        self.binding_id, self.sid, self.session_id = binding_id, windows_sid, session_id
        self.quota_seconds, self.store = quota_seconds, store
        self.zone, self.schedule = ZoneInfo(timezone_name), schedule
        self.clock, self.utcnow, self.max_gap = monotonic, utcnow, max_sample_gap
        self._mono = monotonic()
        wall = self._wall()
        saved = store.load(binding_id)
        self.used_seconds, self.warned = 0.0, set()
        self.day = wall.astimezone(self.zone).date().isoformat()
        if saved:
            trusted = datetime.fromisoformat(saved.trusted_utc)
            if (trusted.tzinfo is None or not math.isfinite(saved.used_seconds)
                    or saved.used_seconds < 0 or set(saved.warned) - set(WARNINGS)):
                raise ValueError("Invalid counter checkpoint")
            wall = max(wall, trusted)
            self.day = max(saved.budget_day, wall.astimezone(self.zone).date().isoformat())
            if self.day == saved.budget_day:
                self.used_seconds, self.warned = saved.used_seconds, set(saved.warned)
        self._trusted = wall
        self._previous: SessionSample | None = None
        self._previous_remaining = self._remaining(wall)
        self._last_clock_notice = False
        self._save()

    def _wall(self) -> datetime:
        value = self.utcnow()
        if value.tzinfo is None:
            raise ValueError("UTC clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _schedule_remaining(self, stamp: datetime) -> float:
        if self.schedule is None:
            return float("inf")
        if all(all(day) for day in self.schedule):
            return float("inf")
        # Walk UTC boundaries to handle DST folds/gaps without naive subtraction.
        local = stamp.astimezone(self.zone)
        if not self.schedule[local.weekday()][local.hour * 2 + local.minute // 30]:
            return 0.0
        boundary = stamp.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(7 * 24 * 60 + 1):
            probe = boundary.astimezone(self.zone)
            if not self.schedule[probe.weekday()][probe.hour * 2 + probe.minute // 30]:
                return (boundary - stamp).total_seconds()
            boundary += timedelta(minutes=1)
        return float("inf")

    def _remaining(self, stamp: datetime) -> float:
        return max(0.0, min(self.quota_seconds - self.used_seconds,
                            self._schedule_remaining(stamp)))

    def _eligible(self, sample: SessionSample | None) -> bool:
        return bool(sample and sample.windows_sid == self.sid
                    and sample.session_id == self.session_id and sample.active
                    and not sample.locked and not sample.suspended)

    def _save(self) -> None:
        # Must commit before publishing a warning or requesting a lock.
        self.store.save(self.binding_id, Checkpoint(
            self.day, self.used_seconds, self._trusted.isoformat(), tuple(sorted(self.warned)),
        ))

    def tick(self, sample: SessionSample, *, tray_present: bool) -> CounterResult:
        if not math.isfinite(sample.idle_seconds) or sample.idle_seconds < 0:
            raise ValueError("Invalid session idle duration")
        now = self.clock()
        elapsed = now - self._mono
        if elapsed < 0 or not math.isfinite(elapsed):
            raise ValueError("Monotonic clock moved backwards")
        before_stamp = self._trusted
        self._trusted += timedelta(seconds=elapsed)
        clock_changed = abs((self._wall() - self._trusted).total_seconds()) > 30
        if clock_changed and not self._last_clock_notice:
            LOG.warning("System clock differs from monotonic budget clock")
        self._last_clock_notice = clock_changed
        current_day = self._trusted.astimezone(self.zone).date().isoformat()
        rolled = current_day > self.day
        if rolled:
            self.day, self.used_seconds, self.warned = current_day, 0.0, set()
        eligible = tray_present and self._eligible(sample)
        if (eligible and self._eligible(self._previous) and 0 <= elapsed <= self.max_gap
                and self._schedule_remaining(before_stamp) > 0):
            # Count up to the five-minute idle boundary, including a partial tick.
            active = max(0.0, elapsed - max(0.0, sample.idle_seconds - IDLE_LIMIT))
            if self._previous.idle_seconds > IDLE_LIMIT:
                # A newly observed input cannot retroactively charge idle time.
                active = min(active, sample.idle_seconds)
            active = min(active, self._schedule_remaining(before_stamp))
            if rolled:
                midnight = datetime.combine(self._trusted.astimezone(self.zone).date(),
                                             datetime.min.time(), self.zone)
                active = min(active, (self._trusted - midnight.astimezone(timezone.utc)).total_seconds())
            self.used_seconds += min(max(0.0, self.quota_seconds - self.used_seconds), active)
        remaining = self._remaining(self._trusted)
        previous = self._previous_remaining
        if rolled:
            previous = self._remaining(before_stamp) if self.schedule else float(self.quota_seconds)
        warnings = tuple(threshold for threshold in WARNINGS
                         if eligible and threshold not in self.warned
                         and previous >= threshold >= remaining and remaining > 0)
        self.warned.update(warnings)
        self._mono, self._previous_remaining = now, remaining
        self._previous = sample if eligible else None
        self._save()
        reason = "outside_schedule" if self._schedule_remaining(self._trusted) == 0 else "daily_quota"
        return CounterResult(self.used_seconds, math.ceil(remaining), warnings,
                             eligible and 0 < remaining <= 60,
                             eligible and remaining <= 0, reason, clock_changed)

    def set_quota(self, quota_seconds: int) -> None:
        """Called only after an authenticated policy/command transaction.

        Command replay prevention belongs to that transaction, never to Tray.
        """
        if type(quota_seconds) is not int or not 0 <= quota_seconds <= 86400:
            raise ValueError("Invalid daily quota")
        self.quota_seconds = quota_seconds
        remaining = self._remaining(self._trusted)
        self.warned = {threshold for threshold in self.warned if remaining <= threshold}
        self._previous_remaining = remaining
        self._save()
