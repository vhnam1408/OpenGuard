"""Windows 10/11 per-session idle/lock metadata; never reads input contents.

GetLastInputInfo in Session 0 cannot measure the child's interactive session.
Use WTS information instead. Missing/invalid metadata is an error, never 'active'.
The service must mark suspended=True on power suspend/resume and discard the
first post-resume interval; counters also reject sampling gaps over five seconds.
"""

from __future__ import annotations

import ctypes
import os

from ogk_agent.time_control.counter import SessionSample


class _Level1(ctypes.Structure):
    _fields_ = [
        ("session_id", ctypes.c_uint32), ("state", ctypes.c_int32),
        ("flags", ctypes.c_int32), ("station", ctypes.c_wchar * 33),
        ("user", ctypes.c_wchar * 21), ("domain", ctypes.c_wchar * 18),
        ("logon", ctypes.c_int64), ("connect", ctypes.c_int64),
        ("disconnect", ctypes.c_int64), ("last_input", ctypes.c_int64),
        ("current", ctypes.c_int64), ("incoming_bytes", ctypes.c_uint32),
        ("outgoing_bytes", ctypes.c_uint32), ("incoming_frames", ctypes.c_uint32),
        ("outgoing_frames", ctypes.c_uint32),
    ]


class _InfoEx(ctypes.Structure):
    _fields_ = [("level", ctypes.c_uint32), ("data", _Level1)]


class WindowsSessionSampler:
    def __init__(self, windows_sid: str, session_id: int) -> None:
        if os.name != "nt" or session_id <= 0 or not windows_sid:
            raise ValueError("Windows interactive SID/session required")
        self.sid, self.session_id = windows_sid, session_id
        self._wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
        self._query = self._wts.WTSQuerySessionInformationW
        self._query.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                               ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
        self._query.restype = ctypes.c_int
        self._wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        self._wts.WTSFreeMemory.restype = None

    def sample(self, *, suspended: bool = False) -> SessionSample:
        import win32security
        import win32ts
        token = win32ts.WTSQueryUserToken(self.session_id)
        try:
            actual = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0])
            if actual != self.sid:
                raise PermissionError("Session is no longer owned by the managed SID")
        finally:
            token.Close()
        pointer, size = ctypes.c_void_p(), ctypes.c_uint32()
        if not self._query(None, self.session_id, 25, ctypes.byref(pointer), ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if size.value < ctypes.sizeof(_InfoEx):
                raise OSError("Incomplete WTS session metadata")
            info = ctypes.cast(pointer, ctypes.POINTER(_InfoEx)).contents
            state = info.data
            if (info.level != 1 or state.session_id != self.session_id
                    or state.flags not in (0, 1) or state.last_input <= 0
                    or state.current < state.last_input):
                raise OSError("Invalid WTS session metadata")
            return SessionSample(self.sid, self.session_id,
                                 (state.current - state.last_input) / 10_000_000,
                                 active=state.state == 0, locked=state.flags == 0,
                                 suspended=suspended)
        finally:
            self._wts.WTSFreeMemory(pointer)
