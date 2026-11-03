"""SID-first application enforcement; basename is explanatory, hash is identity.

Renaming a denied executable does not bypass its SHA-256 rule. No command lines,
window titles, document contents or process hiding are used. All paths stay local.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Callable, Iterable

import psutil

LOG = logging.getLogger(__name__)
PROCESS_ERRORS = (OSError, psutil.Error)
if os.name == "nt":
    import pywintypes
    PROCESS_ERRORS += (pywintypes.error,)

ESSENTIAL_NAMES = frozenset({
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "services.exe",
    "lsass.exe", "svchost.exe", "winlogon.exe", "dwm.exe", "fontdrvhost.exe",
    "sihost.exe", "taskhostw.exe", "explorer.exe", "logonui.exe", "utilman.exe",
    "sethc.exe", "osk.exe", "magnify.exe", "narrator.exe", "taskmgr.exe",
    "pythonservice.exe", "ogk-service.exe", "ogk-tray.exe",
})


@dataclass(frozen=True)
class AppRule:
    name: str
    sha256: str

    def __post_init__(self) -> None:
        if (not self.name or re.search(r'[\\/:*?"<>|\x00-\x1f]', self.name)
                or not re.fullmatch(r"[a-f0-9]{64}", self.sha256)):
            raise ValueError("App rules require basename and lowercase SHA-256")


@dataclass(frozen=True)
class BlockedApp:
    name: str
    rule_name: str
    sha256: str
    reason: str
    rule_set_by: str
    policy_id: str


def _file_signature(stream) -> tuple:
    info = os.fstat(stream.fileno())
    signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if os.name == "nt":
        # On Windows st_ctime may be creation time. Query the actual filesystem
        # ChangeTime so restoring LastWriteTime cannot reuse an obsolete digest.
        import ctypes
        from ctypes import wintypes
        import msvcrt

        class BasicInfo(ctypes.Structure):
            _fields_ = [("creation", ctypes.c_int64), ("access", ctypes.c_int64),
                        ("write", ctypes.c_int64), ("change", ctypes.c_int64),
                        ("attributes", ctypes.c_uint32)]

        query = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        query.restype = wintypes.BOOL
        basic = BasicInfo()
        if not query(msvcrt.get_osfhandle(stream.fileno()), 0, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise ctypes.WinError(ctypes.get_last_error())
        signature += (basic.change,)
    return signature


class HashCache:
    def __init__(self, max_entries: int = 512, ttl: float = 10.0) -> None:
        if max_entries < 1 or ttl <= 0:
            raise ValueError("Hash cache bounds must be positive")
        self._entries: OrderedDict[tuple, tuple[float, str]] = OrderedDict()
        self.max_entries, self.ttl = max_entries, ttl

    def digest(self, stream) -> str:
        key = _file_signature(stream)
        hit = self._entries.get(key)
        now = time.monotonic()
        if key[1] != 0 and hit and now - hit[0] < self.ttl:
            self._entries.move_to_end(key)
            return hit[1]
        hasher = hashlib.sha256()
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
        if _file_signature(stream) != key:
            raise OSError("Executable changed during hashing")
        result = hasher.hexdigest()
        self._entries[key] = now, result
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return result


@contextmanager
def locked_executable(path: str):
    """Hold a read-only handle denying writers/replacement through termination."""
    import msvcrt
    import win32con
    import win32file

    handle = win32file.CreateFile(path, win32con.GENERIC_READ, win32con.FILE_SHARE_READ,
                                 None, win32con.OPEN_EXISTING, 0, None)
    try:
        descriptor = msvcrt.open_osfhandle(handle.Detach(), os.O_RDONLY | os.O_BINARY)
        with os.fdopen(descriptor, "rb") as stream:
            yield stream
    finally:
        handle.Close()


class WindowsProcessTarget:
    """A stable kernel handle prevents killing a different process on PID reuse."""
    def __init__(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes
        import win32api
        import win32con
        import win32security

        self._api = win32api
        self.pid = pid
        self.handle = win32api.OpenProcess(0x1000 | win32con.PROCESS_TERMINATE | 0x100000, False, pid)
        try:
            token = win32security.OpenProcessToken(self.handle, win32con.TOKEN_QUERY)
            try:
                self.sid = win32security.ConvertSidToStringSid(
                    win32security.GetTokenInformation(token, win32security.TokenUser)[0])
                self.session_id = win32security.GetTokenInformation(token, win32security.TokenSessionId)
            finally:
                token.Close()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            critical = wintypes.BOOL()
            check = kernel.IsProcessCritical
            check.argtypes, check.restype = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL
            if not check(int(self.handle), ctypes.byref(critical)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.critical = bool(critical.value)
            query = kernel.QueryFullProcessImageNameW
            query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not query(int(self.handle), 0, buffer, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.path = buffer.value
            self.name = Path(self.path).name
        except BaseException:
            self.close()
            raise

    def same_process(self) -> bool:
        # This handle still refers to the original process, never a reused PID.
        import win32event
        return win32event.WaitForSingleObject(self.handle, 0) == win32event.WAIT_TIMEOUT

    def terminate(self) -> None:
        self._api.TerminateProcess(self.handle, 1)

    def close(self) -> None:
        self.handle.Close()


class AppBlocker:
    def __init__(
        self, *, windows_sid: str, session_id: int, blocked: Iterable[AppRule],
        allowed: Iterable[AppRule] = (), safety_hashes: Iterable[str] = (),
        can_enforce: Callable[[], bool], notify: Callable[[BlockedApp], None],
        record: Callable[[BlockedApp], None], reason: str, rule_set_by: str, policy_id: str,
        target_factory=WindowsProcessTarget, file_opener=locked_executable,
        pids: Callable[[], Iterable[int]] = psutil.pids,
    ) -> None:
        if not windows_sid or session_id <= 0 or not all((reason, rule_set_by, policy_id)):
            raise ValueError("Managed SID/session and explanation are required")
        self.sid, self.session_id = windows_sid, session_id
        self.blocked = {rule.sha256: rule for rule in blocked}
        self.allowed = {rule.sha256 for rule in allowed} | set(safety_hashes)
        self.can_enforce, self.notify, self.record = can_enforce, notify, record
        self.reason, self.rule_set_by, self.policy_id = reason, rule_set_by, policy_id
        self.target_factory, self.file_opener, self.pids = target_factory, file_opener, pids
        self.cache = HashCache()

    def scan_once(self) -> int:
        if not self.can_enforce() or not self.blocked:
            return 0
        count = 0
        for pid in self.pids():
            if not self.can_enforce():
                break
            if pid in (0, 4, os.getpid()):
                continue
            target = None
            try:
                target = self.target_factory(pid)
                # SID and session precede opening/hashing the executable.
                if (target.sid != self.sid or target.session_id != self.session_id
                        or target.critical or target.name.casefold() in ESSENTIAL_NAMES):
                    continue
                with self.file_opener(target.path) as stream:
                    digest = self.cache.digest(stream)
                    if digest in self.allowed or digest not in self.blocked:
                        continue
                    # Revalidate immediately before terminating the stable handle.
                    if not self.can_enforce() or not target.same_process():
                        continue
                    target.terminate()
                count += 1
                event = BlockedApp(target.name, self.blocked[digest].name, digest,
                                   self.reason, self.rule_set_by, self.policy_id)
                # Notify first; activity persistence must use the seven-field contract.
                self.notify(event)
                self.record(event)
            except PROCESS_ERRORS:
                LOG.debug("Process disappeared or is inaccessible")
            finally:
                if target is not None:
                    target.close()
        return count

    def run(self, stop: threading.Event, interval: float = 1.0) -> None:
        if not 0 < interval <= 5:
            raise ValueError("Scan interval must be in (0, 5]")
        while not stop.is_set():
            self.scan_once()
            stop.wait(interval)
