"""Asynchronous, local-only Named Pipe client. Callbacks run on the worker.

The Service must enforce its pipe ACL, reject remote clients, verify the actual
Windows client SID/session, and stop enforcement when the presence lease expires.
No SID, credential, database access or privileged command is supplied by this UI.
"""

from __future__ import annotations

import logging
import os
from queue import Empty, Full, Queue
import threading
from typing import Callable

from ogk_contracts.ipc import CLIENT_TYPES, PIPE_NAME, SERVER_TYPES, FrameDecoder, Message, ProtocolError

LOG = logging.getLogger(__name__)
SECURITY_IDENTIFICATION = 0x00010000  # WinNT.h: SecurityIdentification << 16.


class DisconnectedError(ConnectionError):
    pass


class _WindowsPipe:
    """Overlapped I/O owned by one worker, including cancellation and close."""

    def __init__(self, name: str) -> None:
        if os.name != "nt":
            raise OSError("Named Pipes require Windows")
        import pywintypes
        import win32con
        import win32event
        import win32file

        self._types, self._event, self._file = pywintypes, win32event, win32file
        self._handle = win32file.CreateFile(
            # Specific pipe rights omit FILE_CREATE_PIPE_INSTANCE (0x4), which
            # GENERIC_WRITE would request. Match the Service's restricted ACL.
            name, 0x0012019B, 0, None,
            win32con.OPEN_EXISTING,
            win32con.FILE_FLAG_OVERLAPPED | win32con.SECURITY_SQOS_PRESENT
            | SECURITY_IDENTIFICATION, None,
        )
        self._pending = None
        self._closed = False

    def _operation(self):
        operation = self._types.OVERLAPPED()
        operation.hEvent = self._event.CreateEvent(None, True, False, None)
        return operation

    def read(self, timeout_ms: int = 100) -> bytes | None:
        if self._pending is None:
            operation = self._operation()
            buffer = self._file.AllocateReadBuffer(4096)
            try:
                self._file.ReadFile(self._handle, buffer, operation)
            except BaseException:
                # Submission failed: no pending operation/event to drain.
                operation.hEvent.Close()
                raise
            self._pending = (operation, buffer)
        operation, buffer = self._pending
        if self._event.WaitForSingleObject(operation.hEvent, timeout_ms) == self._event.WAIT_TIMEOUT:
            return None
        try:
            size = self._file.GetOverlappedResult(self._handle, operation, False)
            if not size:
                raise DisconnectedError("Pipe closed")
            return bytes(buffer[:size])
        finally:
            operation.hEvent.Close()
            self._pending = None

    def write(self, data: bytes) -> None:
        operation = self._operation()
        submitted = False
        try:
            self._file.WriteFile(self._handle, data, operation)
            submitted = True
            if self._event.WaitForSingleObject(operation.hEvent, 1000) == self._event.WAIT_TIMEOUT:
                # CancelIo is thread-scoped: all I/O and cleanup run on this
                # worker. A write timeout ends the entire connection.
                self._file.CancelIo(self._handle)
                raise TimeoutError("Pipe write timed out")
            if self._file.GetOverlappedResult(self._handle, operation, False) != len(data):
                raise DisconnectedError("Incomplete write")
        finally:
            # Drain cancellation before releasing the buffer/OVERLAPPED object.
            if submitted:
                try:
                    self._file.GetOverlappedResult(self._handle, operation, True)
                except (OSError, self._types.error):
                    pass
            operation.hEvent.Close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._pending is not None:
                operation, buffer = self._pending
                try:
                    self._file.CancelIo(self._handle)
                except (OSError, self._types.error):
                    pass  # The read may have completed before cancellation.
                try:
                    self._file.GetOverlappedResult(self._handle, operation, True)
                except (OSError, self._types.error):
                    pass
                operation.hEvent.Close()
                self._pending = None
        finally:
            self._handle.Close()


class NamedPipeClient:
    """Reconnect without replaying requests whose delivery may be ambiguous."""

    def __init__(
        self,
        on_message: Callable[[Message], None],
        on_connection: Callable[[bool], None],
        pipe_name: str = PIPE_NAME,
        *,
        transport_factory: Callable = _WindowsPipe,
    ) -> None:
        prefix = "\\\\.\\pipe\\"
        suffix = pipe_name[len(prefix):]
        if not pipe_name.startswith(prefix) or not suffix or any(c in suffix for c in "\\/\r\n\0"):
            raise ValueError("Only a local Named Pipe is allowed")
        self._name = pipe_name
        self._on_message, self._on_connection = on_message, on_connection
        self._factory = transport_factory
        self._outgoing: Queue[bytes] = Queue(maxsize=64)
        self._lock = threading.Lock()
        self._connected = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Client already started")
        self._thread = threading.Thread(target=self._run, name="ogk-pipe", daemon=True)
        self._thread.start()

    def send(self, message: Message) -> str:
        if message.type not in CLIENT_TYPES:
            raise ProtocolError("Operation is not allowed for Tray")
        frame = message.encode()
        with self._lock:
            if not self._connected or self._stop.is_set():
                raise DisconnectedError("Service is unavailable")
            try:
                self._outgoing.put_nowait(frame)
            except Full:
                raise DisconnectedError("IPC queue is full") from None
        return message.request_id

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                LOG.error("IPC worker did not stop within deadline")

    def _disconnect(self) -> None:
        with self._lock:
            self._connected = False
            while True:
                try:
                    self._outgoing.get_nowait()
                except Empty:
                    break

    def _run(self) -> None:
        delay = 0.5
        while not self._stop.is_set():
            pipe = None
            try:
                pipe = self._factory(self._name)
                decoder = FrameDecoder()
                with self._lock:
                    self._connected = True
                self._on_connection(True)
                while not self._stop.is_set():
                    # Bound each batch so incoming warnings cannot starve.
                    for _ in range(8):
                        try:
                            frame = self._outgoing.get_nowait()
                        except Empty:
                            break
                        pipe.write(frame)
                    chunk = pipe.read(100)
                    if chunk is not None:
                        for message in decoder.feed(chunk):
                            if message.type not in SERVER_TYPES:
                                raise ProtocolError("Unexpected Service operation")
                            self._on_message(message)
                        delay = 0.5
            except Exception as error:
                # Do not log payloads, names, request text, tokens or exceptions
                # that may contain remote data. Only a technical class/code.
                LOG.warning("IPC disconnected: class=%s code=%s", type(error).__name__,
                            getattr(error, "winerror", None))
            finally:
                self._disconnect()
                if pipe is not None:
                    try:
                        pipe.close()
                    except Exception as error:
                        LOG.error("Pipe cleanup failed: %s", type(error).__name__)
                try:
                    self._on_connection(False)
                except Exception as error:
                    LOG.error("Connection callback failed: %s", type(error).__name__)
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 10)
