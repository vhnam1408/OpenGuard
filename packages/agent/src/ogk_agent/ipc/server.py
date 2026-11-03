"""Local-only, ACL-protected IPC v1; no shell/file/policy mutation endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from queue import Empty, Full, Queue
import threading
import time
from typing import Callable
from uuid import uuid4

from ogk_contracts.ipc import (
    CLIENT_TYPES, SERVER_TYPES, MAX_FRAME_BYTES, PIPE_NAME, FrameDecoder, Message, ProtocolError,
)

LOG = logging.getLogger(__name__)
# FILE_GENERIC_READ | FILE_GENERIC_WRITE without FILE_CREATE_PIPE_INSTANCE (4).
# Clients must request this specific mask rather than GENERIC_WRITE.
CLIENT_ACCESS = 0x0012019B
PIPE_SDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x0012019b;;;IU)"


@dataclass(frozen=True)
class ClientIdentity:
    windows_sid: str
    session_id: int


@dataclass
class _Client:
    identity: ClientIdentity
    outgoing: Queue = field(default_factory=lambda: Queue(maxsize=64))
    last_presence: float | None = None
    closing: bool = False


class PresenceRegistry:
    """Connection-scoped lease: reconnects do not inherit presence."""
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._clients: dict[str, _Client] = {}

    def connect(self, key: str, identity: ClientIdentity) -> None:
        with self._lock:
            self._clients[key] = _Client(identity)

    def renew(self, key: str, visible: bool) -> None:
        with self._lock:
            client = self._clients[key]
            client.last_presence = self._clock() if visible and not client.closing else None

    def disconnect(self, key: str) -> None:
        with self._lock:
            self._clients.pop(key, None)

    def is_present(self, identity: ClientIdentity) -> bool:
        with self._lock:
            now = self._clock()
            return any(client.identity == identity and not client.closing
                       and client.last_presence is not None
                       and 0 <= now - client.last_presence < 5
                       for client in self._clients.values())

    def publish(self, identity: ClientIdentity, message: Message) -> bool:
        if message.type not in SERVER_TYPES:
            raise ProtocolError("Unexpected server operation")
        frame = message.encode()
        delivered = False
        with self._lock:
            for client in self._clients.values():
                if client.identity != identity or client.closing:
                    continue
                try:
                    client.outgoing.put_nowait(frame)
                    delivered = True
                except Full:
                    # Losing explanations invalidates this client's presence.
                    client.closing, client.last_presence = True, None
        return delivered

    def take(self, key: str) -> bytes | None:
        with self._lock:
            client = self._clients[key]
            if client.closing:
                raise ConnectionError("IPC output queue overflow")
            try:
                return client.outgoing.get_nowait()
            except Empty:
                return None


def client_identity(handle) -> ClientIdentity:
    """Read the security token of the last pipe writer; always revert on error."""
    import win32api
    import win32con
    import win32security

    win32security.ImpersonateNamedPipeClient(handle)
    try:
        token = win32security.OpenThreadToken(win32api.GetCurrentThread(), win32con.TOKEN_QUERY, True)
        try:
            sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0])
            session = win32security.GetTokenInformation(token, win32security.TokenSessionId)
            if session <= 0 or sid in {"S-1-5-18", "S-1-5-19", "S-1-5-20"}:
                raise PermissionError("Interactive User session required")
            return ClientIdentity(sid, session)
        finally:
            token.Close()
    finally:
        win32security.RevertToSelf()


class _PipeIO:
    """Single-thread ownership of all overlapped I/O, cancellation and buffers."""
    def __init__(self, handle) -> None:
        import pywintypes
        import win32event
        import win32file
        self.types, self.event, self.file = pywintypes, win32event, win32file
        self.handle, self.pending = handle, None

    def operation(self):
        operation = self.types.OVERLAPPED()
        operation.hEvent = self.event.CreateEvent(None, True, False, None)
        return operation

    def cancel_drain(self, operation) -> None:
        try:
            self.file.CancelIo(self.handle)
        except (OSError, self.types.error):
            pass
        try:
            self.file.GetOverlappedResult(self.handle, operation, True)
        except (OSError, self.types.error):
            pass

    def connect(self, stop: threading.Event) -> bool:
        import win32pipe
        operation = self.operation()
        submitted = False
        try:
            try:
                result = win32pipe.ConnectNamedPipe(self.handle, operation)
                submitted = result != 535  # ERROR_PIPE_CONNECTED: no pending I/O.
            except (OSError, self.types.error) as error:
                if getattr(error, "winerror", None) != 535:
                    raise
                result = 535
            if result == 535:
                return True
            while not stop.is_set():
                if self.event.WaitForSingleObject(operation.hEvent, 100) == self.event.WAIT_OBJECT_0:
                    self.file.GetOverlappedResult(self.handle, operation, False)
                    submitted = False
                    return True
            return False
        finally:
            if submitted:
                self.cancel_drain(operation)
            operation.hEvent.Close()

    def read(self) -> bytes | None:
        if self.pending is None:
            operation = self.operation()
            buffer = self.file.AllocateReadBuffer(4096)
            try:
                self.file.ReadFile(self.handle, buffer, operation)
            except BaseException:
                operation.hEvent.Close()
                raise
            self.pending = operation, buffer
        operation, buffer = self.pending
        if self.event.WaitForSingleObject(operation.hEvent, 100) == self.event.WAIT_TIMEOUT:
            return None
        try:
            size = self.file.GetOverlappedResult(self.handle, operation, False)
            if size == 0:
                raise ConnectionError("Pipe closed")
            return bytes(buffer[:size])
        finally:
            operation.hEvent.Close()
            self.pending = None

    def write(self, data: bytes) -> None:
        operation = self.operation()
        submitted = False
        try:
            self.file.WriteFile(self.handle, data, operation)
            submitted = True
            if self.event.WaitForSingleObject(operation.hEvent, 1000) != self.event.WAIT_OBJECT_0:
                raise TimeoutError("IPC write timeout")
            if self.file.GetOverlappedResult(self.handle, operation, False) != len(data):
                raise ConnectionError("Incomplete IPC write")
            submitted = False
        finally:
            if submitted:
                self.cancel_drain(operation)
            operation.hEvent.Close()

    def reset(self) -> None:
        import win32pipe
        if self.pending:
            operation, buffer = self.pending
            self.cancel_drain(operation)
            operation.hEvent.Close()
            self.pending = None
        try:
            win32pipe.DisconnectNamedPipe(self.handle)
        except (OSError, self.types.error):
            pass


class NamedPipeServer:
    def __init__(
        self, handler: Callable[[ClientIdentity, Message], Message | None], *,
        pipe_name: str = PIPE_NAME, instances: int = 8,
        presence: PresenceRegistry | None = None,
    ) -> None:
        prefix = "\\\\.\\pipe\\"
        if (not pipe_name.startswith(prefix) or not pipe_name[len(prefix):]
                or any(char in pipe_name[len(prefix):] for char in "\\/\r\n\0")
                or not 1 <= instances <= 16):
            raise ValueError("Invalid local pipe or instance count")
        self.handler, self.name, self.instances = handler, pipe_name, instances
        self.presence = presence or PresenceRegistry()
        self.stop = threading.Event()
        self._handles, self._threads = [], []

    def start(self) -> None:
        if os.name != "nt" or self._threads:
            raise RuntimeError("Named Pipe server requires Windows and a fresh instance")
        import pywintypes
        import win32file
        import win32pipe
        import win32security
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            PIPE_SDDL, win32security.SDDL_REVISION_1)
        try:
            # Retain all handles for the service lifetime, including disconnections.
            # The first-instance flag prevents silently joining a squatted name.
            for index in range(self.instances):
                first = win32pipe.FILE_FLAG_FIRST_PIPE_INSTANCE if index == 0 else 0
                self._handles.append(win32pipe.CreateNamedPipe(
                    self.name, win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED | first,
                    win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT
                    | win32pipe.PIPE_REJECT_REMOTE_CLIENTS,
                    self.instances, MAX_FRAME_BYTES, MAX_FRAME_BYTES, 1000, attributes,
                ))
        except BaseException:
            for handle in self._handles:
                handle.Close()
            self._handles.clear()
            raise
        for handle in self._handles:
            worker = threading.Thread(target=self._worker, args=(handle,), name="ogk-ipc", daemon=True)
            self._threads.append(worker)
            worker.start()

    def _worker(self, handle) -> None:
        pipe = _PipeIO(handle)
        while not self.stop.is_set():
            key = str(uuid4())
            try:
                if not pipe.connect(self.stop):
                    break
                identity = None
                decoder = FrameDecoder()
                started = last_message = window = time.monotonic()
                requests = 0
                while not self.stop.is_set():
                    if identity:
                        for _ in range(8):
                            outgoing = self.presence.take(key)
                            if outgoing is None:
                                break
                            pipe.write(outgoing)
                    now = time.monotonic()
                    if now - last_message > (15 if identity else 5) or now - started > 5:
                        raise TimeoutError("IPC client/frame timeout")
                    chunk = pipe.read()
                    if chunk is None:
                        continue
                    actual = client_identity(handle)
                    if identity is None:
                        identity = actual
                        self.presence.connect(key, identity)
                    elif identity != actual:
                        raise PermissionError("IPC writer identity changed")
                    messages = decoder.feed(chunk)
                    for message in messages:
                        if now - window >= 1:
                            window, requests = now, 0
                        requests += 1
                        if requests > 32 or message.type not in CLIENT_TYPES:
                            raise ProtocolError("IPC request rate/type rejected")
                        if message.type == "tray.presence":
                            if set(message.payload) != {"visible"} or type(message.payload["visible"]) is not bool:
                                raise ProtocolError("Invalid presence")
                            self.presence.renew(key, message.payload["visible"])
                        elif message.type == "status.get":
                            if message.payload:
                                raise ProtocolError("Status payload must be empty")
                        else:
                            minutes = message.payload.get("minutes")
                            if (set(message.payload) != {"minutes"} or type(minutes) is not int
                                    or not 1 <= minutes <= 120):
                                raise ProtocolError("Invalid time request")
                        response = self.handler(identity, message)
                        if response is not None:
                            if response.type not in SERVER_TYPES or response.request_id != message.request_id:
                                raise ProtocolError("Invalid handler response")
                            pipe.write(response.encode())
                    if messages:
                        started = last_message = time.monotonic()
            except Exception as error:
                LOG.debug("IPC connection closed: class=%s code=%s", type(error).__name__,
                          getattr(error, "winerror", None))
            finally:
                self.presence.disconnect(key)
                pipe.reset()

    def check_health(self) -> None:
        if any(not worker.is_alive() for worker in self._threads) and not self.stop.is_set():
            raise RuntimeError("Named Pipe worker stopped unexpectedly")

    def close(self) -> None:
        self.stop.set()
        deadline = time.monotonic() + 5
        for worker in self._threads:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in self._threads):
            raise TimeoutError("Named Pipe worker failed to stop")
        for handle in self._handles:
            handle.Close()
        self._handles.clear()
