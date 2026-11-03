"""Local fake-Service integration: no installed Service or managed account."""

from pathlib import Path
import os
import sys
import time
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "packages/interfaces/src"), str(ROOT / "packages/contracts/src")]

try:
    if os.name != "nt":
        raise ImportError
    import pywintypes
    import win32event
    import win32file
    import win32pipe
    HAS_WINDOWS_PIPE = True
except ImportError:
    HAS_WINDOWS_PIPE = False

from ogk_contracts.ipc import Message
from ogk_ui.tray.ipc_client import _WindowsPipe


@unittest.skipUnless(HAS_WINDOWS_PIPE, "Requires Windows and pywin32")
class WindowsPipeTests(unittest.TestCase):
    def connect(self):
        name = rf"\\.\pipe\ogk-tray-test-{uuid4()}"
        server = win32pipe.CreateNamedPipe(
            name, win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT
            | win32pipe.PIPE_REJECT_REMOTE_CLIENTS,
            1, 4096, 4096, 0, None,
        )
        self.addCleanup(server.Close)
        operation = pywintypes.OVERLAPPED()
        operation.hEvent = win32event.CreateEvent(None, True, False, None)
        self.addCleanup(operation.hEvent.Close)
        win32pipe.ConnectNamedPipe(server, operation)
        client = _WindowsPipe(name)
        self.addCleanup(client.close)
        self.assertEqual(win32event.WaitForSingleObject(operation.hEvent, 2000), win32event.WAIT_OBJECT_0)
        win32file.GetOverlappedResult(server, operation, False)
        return server, client

    def server_io(self, server, data=None):
        operation = pywintypes.OVERLAPPED()
        operation.hEvent = win32event.CreateEvent(None, True, False, None)
        try:
            if data is None:
                buffer = win32file.AllocateReadBuffer(4096)
                win32file.ReadFile(server, buffer, operation)
            else:
                win32file.WriteFile(server, data, operation)
            self.assertEqual(win32event.WaitForSingleObject(operation.hEvent, 2000), win32event.WAIT_OBJECT_0)
            count = win32file.GetOverlappedResult(server, operation, False)
            return bytes(buffer[:count]) if data is None else count
        finally:
            # Also drain operations if an assertion failed.
            try:
                win32file.CancelIo(server)
            except Exception:
                pass
            try:
                win32file.GetOverlappedResult(server, operation, True)
            except Exception:
                pass
            operation.hEvent.Close()

    def test_full_duplex_and_pending_read_across_timeout(self):
        server, client = self.connect()
        self.assertIsNone(client.read(10))
        request = Message.create("status.get", {}).encode()
        client.write(request)  # A read is still pending while we write.
        self.assertEqual(self.server_io(server), request)
        response = Message.create("status", {"managed": False}).encode()
        self.server_io(server, response)
        self.assertEqual(client.read(2000), response)

    def test_idle_read_is_cancelled_on_close(self):
        server, client = self.connect()
        self.assertIsNone(client.read(10))
        start = time.monotonic()
        client.close()
        self.assertLess(time.monotonic() - start, 1)

    def test_broken_pipe_cleanup(self):
        server, client = self.connect()
        win32pipe.DisconnectNamedPipe(server)
        with self.assertRaises(Exception):
            client.read(100)
        client.close()


if __name__ == "__main__":
    unittest.main()
