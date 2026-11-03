"""Windowed, asInvoker entry; a mutex prevents duplicate trays per session."""
import ctypes
import sys
from ctypes import wintypes


def main() -> int:
    if sys.argv[1:] == ["--smoke-test"]:
        import tkinter
        import pystray._win32
        from PIL import Image, ImageDraw
        from ogk_ui.tray import main as tray, dialogs, ipc_client
        return 0
    if sys.argv[1:] not in ([], ["--autostart"]):
        return 2
    from ogk_ui.tray.main import _require_user_session, main as run_tray
    if sys.argv[1:] == ["--autostart"]:
        try:
            _require_user_session()
        except RuntimeError:
            # Inno cannot recover an unelevated token if launched already elevated.
            # The common Startup shortcut will run at the next normal User logon.
            return 0
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, "Local\\OpenGuardKids.Tray.v1")
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    try:
        return 0 if error == 183 else run_tray()  # ERROR_ALREADY_EXISTS
    finally:
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
