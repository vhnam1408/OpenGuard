"""Fake data only; unittest suite runs without Tk, pystray or pywin32."""

from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Queue
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "packages/interfaces/src"), str(ROOT / "packages/contracts/src")]

from ogk_contracts.ipc import MAX_FRAME_BYTES, PIPE_NAME, FrameDecoder, Message, ProtocolError
from ogk_ui.tray.dialogs import Dialogs, format_remaining
from ogk_ui.tray.ipc_client import DisconnectedError, NamedPipeClient
from ogk_ui.tray.main import TrayApplication, validate_payload


def explanation(**overrides):
    return {"rule_set_by": "Phụ huynh mẫu", "reason": "Đến giờ nghỉ",
            "policy_id": "policy-demo", **overrides}


class ProtocolTests(unittest.TestCase):
    def test_split_utf8_and_multiple_frames(self):
        warning = Message.create("quota_warning", explanation(remaining_seconds=60))
        status = Message.create("status", {"managed": False})
        data = warning.encode() + status.encode()
        decoder = FrameDecoder()
        actual = []
        for byte in data:
            actual.extend(decoder.feed(bytes([byte])))
        self.assertEqual(actual, [warning, status])

    def test_oversized_unterminated_and_complete_frame(self):
        for data in (b"x" * MAX_FRAME_BYTES, b"x" * MAX_FRAME_BYTES + b"\n"):
            with self.subTest(size=len(data)), self.assertRaises(ProtocolError):
                FrameDecoder().feed(data)

    def test_invalid_envelopes(self):
        valid = json.loads(Message.create("status.get", {}).encode())
        for change in ({"version": True}, {"version": 2}, {"request_id": "bad"},
                       {"payload": []}, {"type": "shell.run"}, {"extra": 1}):
            with self.subTest(change=change), self.assertRaises(ProtocolError):
                Message.decode(json.dumps({**valid, **change}).encode())
        with self.assertRaises(ProtocolError):
            Message.decode(b"\xff")
        with self.assertRaises(ProtocolError):
            Message.decode(b'{"version":1,"version":1}')

    def test_nonfinite_and_large_outgoing_data(self):
        for payload in ({"x": float("nan")}, {"x": "a" * MAX_FRAME_BYTES}):
            with self.assertRaises(ProtocolError):
                Message.create("status", payload).encode()

    def test_explanations_and_numeric_types(self):
        validate_payload(Message.create("status", {"managed": False}))
        validate_payload(Message.create("quota_warning", explanation(remaining_seconds=60)))
        for kind, payload in (
            ("status", {"managed": "yes"}),
            ("quota_warning", explanation(remaining_seconds=True)),
            ("quota_warning", explanation(remaining_seconds=-1)),
            ("blocked_app", {"reason": "Đến giờ nghỉ"}),
            ("locked", explanation(rule_set_by="")),
            ("time.request.result", {"status": "approved"}),
        ):
            with self.subTest(kind=kind), self.assertRaises(ProtocolError):
                validate_payload(Message.create(kind, payload))


class FakePipe:
    def __init__(self, name):
        self.incoming = Queue()
        self.writes = Queue()
        self.closed = threading.Event()

    def read(self, timeout_ms=100):
        try:
            item = self.incoming.get(timeout=timeout_ms / 1000)
        except Empty:
            return None
        if isinstance(item, Exception):
            raise item
        return item

    def write(self, data):
        self.writes.put(data)

    def close(self):
        self.closed.set()


class ClientTests(unittest.TestCase):
    def test_local_only_and_operation_allowlist(self):
        for name in (r"\\server\pipe\test", r"C:\file", PIPE_NAME + "\\other"):
            with self.assertRaises(ValueError):
                NamedPipeClient(Mock(), Mock(), name)
        client = NamedPipeClient(Mock(), Mock())
        with self.assertRaises(DisconnectedError):
            client.send(Message.create("status.get", {}))
        with self.assertRaises(ProtocolError):
            client.send(Message.create("locked", {}))

    def test_receive_send_and_stop_idle_pipe(self):
        pipe = FakePipe(PIPE_NAME)
        connected, received = threading.Event(), Queue()
        client = NamedPipeClient(received.put, lambda online: connected.set() if online else None,
                                 transport_factory=lambda name: pipe)
        client.start()
        try:
            self.assertTrue(connected.wait(2))
            request = Message.create("time.request", {"minutes": 15})
            self.assertEqual(client.send(request), request.request_id)
            self.assertEqual(Message.decode(pipe.writes.get(timeout=2).rstrip(b"\n")), request)
            notice = Message.create("quota_warning", explanation(remaining_seconds=60))
            pipe.incoming.put(notice.encode())
            self.assertEqual(received.get(timeout=2), notice)
        finally:
            start = time.monotonic()
            client.close()
        self.assertLess(time.monotonic() - start, 1)
        self.assertTrue(pipe.closed.is_set())
        self.assertFalse(client.connected)

    def test_disconnect_drops_ambiguous_request_before_reconnect(self):
        first, second = FakePipe(PIPE_NAME), FakePipe(PIPE_NAME)
        instances = iter((first, second))
        states = Queue()
        client = NamedPipeClient(Mock(), states.put, transport_factory=lambda name: next(instances))
        client.start()
        try:
            self.assertTrue(states.get(timeout=2))
            request = Message.create("time.request", {"minutes": 15})
            client.send(request)
            self.assertEqual(first.writes.get(timeout=2), request.encode())
            first.incoming.put(DisconnectedError("test disconnect"))
            self.assertFalse(states.get(timeout=2))
            self.assertTrue(states.get(timeout=2))
            with self.assertRaises(Empty):
                second.writes.get(timeout=0.2)
        finally:
            client.close()

    def test_invalid_frame_disconnects_and_does_not_leak_payload_to_log(self):
        pipe = FakePipe(PIPE_NAME)
        states, received = Queue(), Mock()
        client = NamedPipeClient(received, states.put, transport_factory=lambda name: pipe)
        with self.assertLogs("ogk_ui.tray.ipc_client", level="WARNING") as logs:
            client.start()
            try:
                self.assertTrue(states.get(timeout=2))
                pipe.incoming.put(b'private-test-value\n')
                self.assertFalse(states.get(timeout=2))
            finally:
                client.close()
        self.assertNotIn("private-test-value", " ".join(logs.output))
        received.assert_not_called()


class ApplicationTests(unittest.TestCase):
    def app(self):
        app = TrayApplication.__new__(TrayApplication)
        app.root = Mock()
        app.dialogs = Mock()
        app.icon = Mock(visible=True)
        app.client = Mock()
        app._status = None
        app._pending = {}
        app._ready = True
        app._online = True
        app._closing = False
        app._last_presence = 0
        return app

    def test_request_only_sends_minutes_without_mutating_quota(self):
        app = self.app()
        app._status = {"remaining_seconds": 60}
        self.assertTrue(app._submit_time(15))
        message = app.client.send.call_args.args[0]
        self.assertEqual((message.type, message.payload), ("time.request", {"minutes": 15}))
        self.assertEqual(app._status["remaining_seconds"], 60)

    def test_presence_requires_visible_icon_and_online_ui(self):
        app = self.app()
        app._presence()
        self.assertEqual(app.client.send.call_args.args[0].type, "tray.presence")
        app.client.reset_mock()
        for ready, online, visible in ((False, True, True), (True, False, True), (True, True, False)):
            app._ready, app._online, app.icon.visible = ready, online, visible
            app._presence()
        app.client.send.assert_not_called()

    def test_warning_retains_rule_setter_reason_and_grace(self):
        dialogs = Dialogs.__new__(Dialogs)
        dialogs.notice = Mock()
        payload = explanation(remaining_seconds=60)
        dialogs.explanation("quota_warning", payload)
        text = dialogs.notice.call_args.args[2]
        self.assertIn(payload["rule_set_by"], text)
        self.assertIn(payload["reason"], text)
        self.assertIn(payload["policy_id"], text)
        self.assertIn("lưu bài", text)
        self.assertTrue(dialogs.notice.call_args.kwargs["can_request"])

    def test_blocked_application_explains_rule(self):
        dialogs = Dialogs.__new__(Dialogs)
        dialogs.notice = Mock()
        payload = explanation(subject="example.exe")
        dialogs.explanation("blocked_app", payload)
        text = dialogs.notice.call_args.args[2]
        for value in payload.values():
            self.assertIn(value, text)

    def test_approval_refreshes_service_state(self):
        app = self.app()
        app._message(Message.create("time.request.result", explanation(status="approved")))
        self.assertIsNone(app._status)
        self.assertEqual(app.client.send.call_args.args[0].type, "status.get")

    def test_disconnect_invalidates_cached_quota_and_pending_requests(self):
        app = self.app()
        app._status = {"remaining_seconds": 60}
        app._pending = {"demo": ("time.request", 123)}
        app._connection(False)
        self.assertIsNone(app._status)
        self.assertFalse(app._pending)
        app.dialogs.notice.assert_called_once()

    def test_ui_failure_closes_pipe_and_stops_tray(self):
        app = self.app()
        with patch("ogk_ui.tray.main._fallback_notice"):
            app._fatal()
        app.client.close.assert_called_once()
        app.icon.stop.assert_called_once()
        app.root.destroy.assert_called_once()
        self.assertFalse(app._ready)
        self.assertTrue(app._closing)

    def test_invalid_explanation_ends_ui_presence(self):
        app = self.app()
        app._fatal = Mock()
        app._message(Message.create("locked", {"reason": "test"}))
        app._fatal.assert_called_once()
        app.dialogs.explanation.assert_not_called()

    def test_time_format(self):
        self.assertEqual(format_remaining(60), "1 phút 00 giây")
        self.assertEqual(format_remaining(3601), "1 giờ 00 phút 01 giây")


if __name__ == "__main__":
    unittest.main()
