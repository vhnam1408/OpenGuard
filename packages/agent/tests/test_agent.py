"""Synthetic accounts/rules and local test sockets; no installation or real kills.

Run: python -m unittest discover -s packages/agent/tests -v
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "packages/agent/src"), str(ROOT / "packages/contracts/src"),
                str(ROOT / "packages/interfaces/src")]

from dnslib import A, AAAA, DNSRecord, QTYPE, RCODE, RR
from ogk_agent.app_control.blocker import AppBlocker, AppRule, HashCache
from ogk_agent.dns.proxy import DNSProxy, DnsScope, matches, normalize_domain
from ogk_agent.ipc.server import ClientIdentity, NamedPipeServer, PresenceRegistry
from ogk_agent.service.windows_service import AgentRuntime, OpenGuardKidsService
from ogk_agent.storage.checkpoints import SQLiteCheckpointStore
from ogk_agent.time_control.counter import SessionSample, UsageCounter
from ogk_contracts.ipc import FrameDecoder, Message

SID = "S-1-5-21-100-100-100-1001"
TEST_ROOT = (ROOT / "runtime" / "agent-tests").resolve()
TEST_ROOT.mkdir(parents=True, exist_ok=True)


def temporary_directory():
    directory = tempfile.TemporaryDirectory(dir=TEST_ROOT)
    # Cleanup is recursive: verify its absolute boundary before registering it.
    assert Path(directory.name).resolve().is_relative_to(TEST_ROOT)
    return directory


class Clock:
    def __init__(self, stamp=None):
        self.seconds = 0.0
        self.wall = stamp or datetime(2026, 11, 3, 8, tzinfo=timezone.utc)

    def advance(self, seconds=1):
        self.seconds += seconds
        self.wall += timedelta(seconds=seconds)


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.directory = temporary_directory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "agent.db"
        self.store = SQLiteCheckpointStore(self.path)
        self.addCleanup(self.store.close)
        self.clock = Clock()
        self.sample = SessionSample(SID, 1, 0)

    def counter(self, quota=180, **kwargs):
        return UsageCounter(binding_id="b1", windows_sid=SID, session_id=1,
                            quota_seconds=quota, store=self.store, monotonic=lambda: self.clock.seconds,
                            utcnow=lambda: self.clock.wall, **kwargs)

    def test_three_minutes_grace_inside_quota(self):
        counter = self.counter()
        counter.tick(self.sample, tray_present=True)
        observed = []
        for second in range(1, 181):
            self.clock.advance()
            result = counter.tick(self.sample, tray_present=True)
            observed.extend(result.warnings)
            self.assertEqual(result.lock_required, second == 180)
            self.assertEqual(result.grace, 120 <= second < 180)
        self.assertEqual(observed, [60])  # No backfilled 10/5 minute warnings.
        self.assertEqual(result.used_seconds, 180)

    def test_all_warning_thresholds_once(self):
        counter = self.counter(601)
        counter.tick(self.sample, tray_present=True)
        observed = []
        for _ in range(601):
            self.clock.advance()
            observed.extend(counter.tick(self.sample, tray_present=True).warnings)
        self.assertEqual(observed, [600, 300, 60])

    def test_idle_counts_only_first_five_minutes(self):
        counter = self.counter(3600)
        counter.tick(self.sample, tray_present=True)
        for second in range(1, 601):
            self.clock.advance()
            result = counter.tick(replace(self.sample, idle_seconds=second), tray_present=True)
        self.assertEqual(result.used_seconds, 300)
        self.clock.advance()
        result = counter.tick(self.sample, tray_present=True)
        self.assertEqual(result.used_seconds, 300)
        self.clock.advance()
        self.assertEqual(counter.tick(self.sample, tray_present=True).used_seconds, 301)

    def test_pause_for_wrong_sid_session_lock_sleep_or_no_tray(self):
        counter = self.counter(3600)
        counter.tick(self.sample, tray_present=True)
        for sample in (replace(self.sample, windows_sid="other"), replace(self.sample, session_id=2),
                       replace(self.sample, locked=True), replace(self.sample, suspended=True),
                       replace(self.sample, active=False)):
            self.clock.advance()
            self.assertEqual(counter.tick(sample, tray_present=True).used_seconds, 0)
        self.clock.advance()
        self.assertEqual(counter.tick(self.sample, tray_present=False).used_seconds, 0)
        self.clock.advance(3600)
        self.assertEqual(counter.tick(self.sample, tray_present=True).used_seconds, 0)

    def test_restart_and_three_hour_rollback_keep_usage_and_warning(self):
        counter = self.counter()
        counter.tick(self.sample, tray_present=True)
        for _ in range(120):
            self.clock.advance()
            result = counter.tick(self.sample, tray_present=True)
        self.assertEqual(result.warnings, (60,))
        self.clock.wall -= timedelta(hours=3)
        restored = self.counter()
        result = restored.tick(self.sample, tray_present=True)
        self.assertEqual(result.used_seconds, 120)
        self.assertEqual(result.warnings, ())
        self.assertTrue(result.clock_changed)
        self.clock.advance()
        self.assertEqual(restored.tick(self.sample, tray_present=True).used_seconds, 121)

    def test_quota_increase_cancels_lock_but_not_schedule(self):
        counter = self.counter(0)
        self.assertTrue(counter.tick(self.sample, tray_present=True).lock_required)
        counter.set_quota(900)
        result = counter.tick(self.sample, tray_present=True)
        self.assertFalse(result.lock_required)
        self.assertEqual(result.remaining_seconds, 900)
        forbidden = tuple(tuple(False for _ in range(48)) for _ in range(7))
        scheduled = self.counter(0, schedule=forbidden)
        scheduled.set_quota(900)
        self.assertTrue(scheduled.tick(self.sample, tray_present=True).lock_required)

    def test_schedule_grace_ends_at_boundary_even_while_idle(self):
        self.clock.wall = datetime(2026, 11, 3, 8, 28, 59, tzinfo=timezone.utc)
        schedule = [list([True] * 48) for _ in range(7)]
        schedule[1][17] = False  # Tuesday 08:30 UTC.
        counter = self.counter(3600, schedule=tuple(tuple(day) for day in schedule), timezone_name="UTC")
        sample = replace(self.sample, idle_seconds=600)
        counter.tick(sample, tray_present=True)
        self.clock.advance()
        result = counter.tick(sample, tray_present=True)
        self.assertEqual(result.warnings, (60,))
        self.assertTrue(result.grace)
        self.clock.advance(60)
        result = counter.tick(sample, tray_present=True)
        self.assertTrue(result.lock_required)
        self.assertEqual(result.reason, "outside_schedule")

    def test_reducing_quota_never_reduces_consumed_usage(self):
        counter = self.counter(100)
        counter.tick(self.sample, tray_present=True)
        for _ in range(20):
            self.clock.advance()
            counter.tick(self.sample, tray_present=True)
        counter.set_quota(5)
        self.clock.advance()
        result = counter.tick(self.sample, tray_present=True)
        self.assertEqual(result.used_seconds, 20)
        self.assertTrue(result.lock_required)

    def test_midnight_resets_once_and_bindings_are_isolated(self):
        self.clock.wall = datetime(2026, 11, 3, 23, 59, 58, tzinfo=timezone.utc)
        counter = self.counter(100, timezone_name="UTC")
        counter.tick(self.sample, tray_present=True)
        self.clock.advance()
        counter.tick(self.sample, tray_present=True)
        self.clock.advance()
        self.assertEqual(counter.tick(self.sample, tray_present=True).used_seconds, 0)
        self.clock.advance()
        self.assertEqual(counter.tick(self.sample, tray_present=True).used_seconds, 1)
        self.assertIsNone(self.store.load("other-binding"))


class BlockerTests(unittest.TestCase):
    def setUp(self):
        self.directory = temporary_directory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "renamed.exe"
        self.path.write_bytes(b"synthetic executable fixture")
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.target = Mock(sid=SID, session_id=1, critical=False, path=str(self.path))
        self.target.name = "renamed.exe"
        self.target.same_process.return_value = True
        self.notice, self.record = Mock(), Mock()
        self.gate = Mock(return_value=True)
        self.opener = Mock(side_effect=lambda path: open(path, "rb"))

    def blocker(self, **kwargs):
        options = dict(windows_sid=SID, session_id=1, blocked=[AppRule("original.exe", self.digest)],
                       can_enforce=self.gate, notify=self.notice, record=self.record,
                       reason="Synthetic rule", rule_set_by="Test Parent", policy_id="p1",
                       target_factory=lambda pid: self.target, file_opener=self.opener, pids=lambda: [99999])
        options.update(kwargs)
        return AppBlocker(**options)

    def test_sha256_blocks_renamed_executable_and_explains(self):
        self.assertEqual(self.blocker().scan_once(), 1)
        self.target.terminate.assert_called_once()
        notice = self.notice.call_args.args[0]
        self.assertEqual(notice.name, "renamed.exe")
        self.assertEqual(notice.rule_name, "original.exe")
        self.assertEqual(notice.rule_set_by, "Test Parent")
        self.assertFalse(hasattr(notice, "path"))
        self.record.assert_called_once_with(notice)

    def test_name_alone_does_not_block(self):
        self.path.write_bytes(b"different harmless fixture")
        self.assertEqual(self.blocker().scan_once(), 0)
        self.target.terminate.assert_not_called()

    def test_sid_session_critical_and_essential_exempt_before_hash(self):
        for field, value in (("sid", "other"), ("session_id", 2), ("critical", True), ("name", "utilman.exe")):
            original = getattr(self.target, field)
            setattr(self.target, field, value)
            self.assertEqual(self.blocker().scan_once(), 0)
            self.opener.assert_not_called()
            setattr(self.target, field, original)

    def test_safety_and_allowed_hashes_precede_deny(self):
        self.assertEqual(self.blocker(safety_hashes=[self.digest]).scan_once(), 0)
        self.assertEqual(self.blocker(allowed=[AppRule("allowed.exe", self.digest)]).scan_once(), 0)
        self.target.terminate.assert_not_called()

    def test_pid_exit_and_lost_tray_prevent_termination(self):
        self.target.same_process.return_value = False
        self.assertEqual(self.blocker().scan_once(), 0)
        self.target.same_process.return_value = True
        self.gate.side_effect = [True, True, False]
        self.assertEqual(self.blocker().scan_once(), 0)
        self.target.terminate.assert_not_called()

    def test_hash_cache_invalidates_after_file_replacement(self):
        cache = HashCache()
        with self.path.open("rb") as stream:
            first = cache.digest(stream)
        replacement = self.path.with_name("replacement.exe")
        replacement.write_bytes(b"changed executable fixture")
        replacement.replace(self.path)
        with self.path.open("rb") as stream:
            self.assertNotEqual(first, cache.digest(stream))


def scope(**kwargs):
    values = dict(binding_id="b1", policy_id="p1", policy_version=1,
                  blocked_domains=frozenset({"blocked.test"}), allowed_domains=frozenset(),
                  safety_domains=frozenset(), google_search_domains=frozenset({"www.google.com"}),
                  reason="Synthetic domain rule", rule_set_by="Test Parent", can_enforce=lambda: True)
    values.update(kwargs)
    return DnsScope(**values)


class DNSTests(unittest.TestCase):
    def setUp(self):
        self.notice = Mock()
        self.proxy = DNSProxy(upstream=("192.0.2.53", 53),
                              mandatory_safety_domains=frozenset({"school.test", "help.test"}),
                              notify=self.notice)
        def exchange(query, **kwargs):
            reply = query.reply(aa=0, ra=1)
            if query.q.qtype == QTYPE.A:
                reply.add_answer(RR(query.q.qname, QTYPE.A, rdata=A("192.0.2.10"), ttl=60))
            elif query.q.qtype == QTYPE.AAAA:
                reply.add_answer(RR(query.q.qname, QTYPE.AAAA, rdata=AAAA("2001:db8::10"), ttl=60))
            return reply
        self.proxy._exchange = Mock(side_effect=exchange)

    def resolve(self, name, policy=None, qtype="A"):
        query = DNSRecord.question(name, qtype)
        answer = DNSRecord.parse(self.proxy.resolve(query.pack(), policy))
        self.assertEqual(answer.header.id, query.header.id)
        self.assertEqual(answer.q, query.q)
        return answer

    def test_label_matching_normalization_and_nxdomain(self):
        self.assertEqual(normalize_domain("BlOcKeD.TeSt."), "blocked.test")
        self.assertEqual(self.resolve("a.blocked.test", scope()).header.rcode, RCODE.NXDOMAIN)
        self.assertEqual(self.resolve("notblocked.test", scope()).header.rcode, RCODE.NOERROR)
        self.assertFalse(matches("notblocked.test", frozenset({"blocked.test"})))

    def test_safety_and_allowlists_precede_deny(self):
        policy = scope(blocked_domains=frozenset({"test"}), allowed_domains=frozenset({"allowed.test"}))
        for domain in ("school.test", "a.school.test", "help.test", "allowed.test"):
            self.assertEqual(self.resolve(domain, policy).header.rcode, RCODE.NOERROR)
        self.notice.assert_not_called()

    def test_unknown_sid_or_no_tray_forwards_without_attribution(self):
        self.assertEqual(self.resolve("blocked.test").header.rcode, RCODE.NOERROR)
        self.assertEqual(self.resolve("blocked.test", scope(can_enforce=lambda: False)).header.rcode, RCODE.NOERROR)
        self.notice.assert_not_called()

    def test_notices_are_throttled_per_binding_policy_and_domain(self):
        for _ in range(3):
            self.resolve("blocked.test", scope())
        self.assertEqual(self.notice.call_count, 1)
        self.resolve("blocked.test", scope(binding_id="b2"))
        self.resolve("blocked.test", scope(policy_version=2))
        self.assertEqual(self.notice.call_count, 3)

    def test_safesearch_a_aaaa_and_https_are_forced(self):
        for qtype in ("A", "AAAA", "HTTPS"):
            answer = self.resolve("www.google.com", scope(), qtype)
            self.assertEqual(answer.rr[0].rtype, QTYPE.CNAME)
            self.assertEqual(str(answer.rr[0].rdata), "forcesafesearch.google.com.")
            self.assertFalse(answer.header.ad)
            if qtype in ("A", "AAAA"):
                self.assertEqual(answer.rr[1].rtype, getattr(QTYPE, qtype))
            else:
                self.assertEqual(len(answer.rr), 1)  # No unfiltered HTTPS IP hints.

    def test_allowed_search_domain_cannot_disable_safesearch(self):
        policy = scope(allowed_domains=frozenset({"www.google.com"}))
        self.assertEqual(self.resolve("www.google.com", policy).rr[0].rtype, QTYPE.CNAME)

    def test_malformed_query_and_upstream_timeout_are_bounded_errors(self):
        answer = DNSRecord.parse(self.proxy.resolve(b"\x12\x34bad"))
        self.assertEqual(answer.header.rcode, RCODE.FORMERR)
        self.assertEqual(answer.header.id, 0x1234)
        self.proxy._exchange.side_effect = TimeoutError()
        self.assertEqual(self.resolve("neutral.test", scope()).header.rcode, RCODE.SERVFAIL)

    def test_reject_loopback_upstream(self):
        with self.assertRaises(ValueError):
            DNSProxy(upstream=("127.0.0.1", 53), mandatory_safety_domains=frozenset({"help.test"}))

    def test_no_silent_block_if_explanation_channel_fails(self):
        self.notice.side_effect = ConnectionError()
        self.assertEqual(self.resolve("blocked.test", scope()).header.rcode, RCODE.NOERROR)
        # Failure must not create a throttle entry that silently blocks the retry.
        self.assertEqual(self.resolve("blocked.test", scope()).header.rcode, RCODE.NOERROR)
        self.assertEqual(self.notice.call_count, 2)

    def test_upstream_reply_must_match_id_question_and_response_flag(self):
        query = DNSRecord.question("neutral.test")
        for change in (lambda reply: setattr(reply.header, "id", query.header.id ^ 1),
                       lambda reply: setattr(reply.header, "qr", 0),
                       lambda reply: setattr(reply.q, "qname", DNSRecord.question("other.test").q.qname)):
            reply = DNSRecord.parse(query.reply(aa=0, ra=1).pack())
            change(reply)
            with self.assertRaises(Exception):
                DNSProxy._valid_response(query, reply.pack())

    def test_real_upstream_udp_truncation_retries_tcp_with_correct_transaction_id(self):
        # Resolver fixture only. Production configuration rejects loopback upstreams.
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(udp.close)
        self.addCleanup(tcp.close)
        udp.bind(("127.0.0.1", 0))
        tcp.bind(udp.getsockname())
        tcp.listen(1)
        udp.settimeout(3)
        tcp.settimeout(3)
        seen, errors = [], []
        def resolver():
            try:
                packet, peer = udp.recvfrom(4096)
                question = DNSRecord.parse(packet)
                seen.append("udp")
                reply = question.reply(aa=0, ra=1)
                reply.header.tc = 1
                udp.sendto(reply.pack(), peer)
                connection, _ = tcp.accept()
                with connection:
                    size = struct.unpack("!H", self.proxy._recv_exact(connection, 2))[0]
                    question = DNSRecord.parse(self.proxy._recv_exact(connection, size))
                    seen.append("tcp")
                    reply = question.reply(aa=0, ra=1)
                    reply.add_answer(RR(question.q.qname, QTYPE.AAAA,
                                        rdata=AAAA("2001:db8::1"), ttl=60))
                    packet = reply.pack()
                    connection.sendall(struct.pack("!H", len(packet)) + packet)
            except Exception as error:
                errors.append(type(error).__name__)
        worker = threading.Thread(target=resolver, daemon=True)
        worker.start()
        # Restore the real implementation for this one fixture.
        self.proxy._exchange = DNSProxy._exchange.__get__(self.proxy)
        self.proxy.upstream = udp.getsockname()
        query = DNSRecord.question("neutral.test", "AAAA")
        reply = self.proxy._exchange(query)
        worker.join(timeout=4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(seen, ["udp", "tcp"])
        self.assertEqual(reply.header.id, query.header.id)
        self.assertEqual(reply.rr[0].rtype, QTYPE.AAAA)

    def test_loopback_udp_and_tcp_listener_and_port_conflict(self):
        self.proxy.port = 0
        self.proxy.start()
        self.addCleanup(self.proxy.close)
        query = DNSRecord.question("neutral.test").pack()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            client.sendto(query, ("127.0.0.1", self.proxy.port))
            self.assertEqual(DNSRecord.parse(client.recv(4096)).header.rcode, RCODE.NOERROR)
        with socket.create_connection(("127.0.0.1", self.proxy.port), timeout=2) as client:
            client.sendall(struct.pack("!H", len(query)) + query)
            size = struct.unpack("!H", self.proxy._recv_exact(client, 2))[0]
            self.assertEqual(DNSRecord.parse(self.proxy._recv_exact(client, size)).header.rcode, RCODE.NOERROR)
        other = DNSProxy(upstream=("192.0.2.53", 53), mandatory_safety_domains=frozenset({"help.test"}),
                         port=self.proxy.port)
        with self.assertRaises(OSError):
            other.start()


class PresenceTests(unittest.TestCase):
    def test_five_second_lease_and_disconnect_never_leak_to_another_sid(self):
        clock = Clock()
        registry = PresenceRegistry(clock=lambda: clock.seconds)
        identity = ClientIdentity(SID, 1)
        registry.connect("c1", identity)
        self.assertFalse(registry.is_present(identity))
        registry.renew("c1", True)
        self.assertTrue(registry.is_present(identity))
        self.assertFalse(registry.is_present(ClientIdentity("other", 1)))
        self.assertFalse(registry.is_present(ClientIdentity(SID, 2)))
        clock.advance(5)
        self.assertFalse(registry.is_present(identity))
        registry.renew("c1", True)
        registry.disconnect("c1")
        registry.connect("c2", identity)
        self.assertFalse(registry.is_present(identity))

    def test_output_overflow_revokes_presence(self):
        registry = PresenceRegistry()
        identity = ClientIdentity(SID, 1)
        registry.connect("c1", identity)
        registry.renew("c1", True)
        for _ in range(65):
            registry.publish(identity, Message.create("status", {"managed": False}))
        self.assertFalse(registry.is_present(identity))

    def test_unpaired_runtime_does_not_accept_extra_time(self):
        runtime = AgentRuntime()
        request = Message.create("time.request", {"minutes": 15})
        response = runtime.handle(ClientIdentity(SID, 1), request)
        self.assertEqual(response.type, "error")
        self.assertEqual(response.request_id, request.request_id)
        self.assertEqual(runtime.handle(ClientIdentity(SID, 1), Message.create("status.get", {})).payload,
                         {"managed": False})


@unittest.skipUnless(os.name == "nt", "Requires Windows/pywin32")
class NativePipeTests(unittest.TestCase):
    def connect(self):
        from ogk_ui.tray.ipc_client import _WindowsPipe
        import win32api
        import win32con
        import win32security
        from ogk_agent.ipc.server import PIPE_SDDL
        self.identities = []
        def handler(identity, message):
            self.identities.append(identity)
            if message.type == "status.get":
                return Message("status", {"managed": False}, message.request_id)
        name = rf"\\.\pipe\ogk-agent-test-{uuid4()}"
        server = NamedPipeServer(handler, pipe_name=name, instances=2)
        # The production server is SYSTEM. This test runs as a regular User;
        # grant only the fixture creator permission to create its fake instances.
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            runner_sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        finally:
            token.Close()
        with patch("ogk_agent.ipc.server.PIPE_SDDL", PIPE_SDDL + f"(A;;GA;;;{runner_sid})"):
            server.start()
        self.addCleanup(server.close)
        client = _WindowsPipe(name)
        self.addCleanup(client.close)
        return server, client

    def receive(self, client):
        decoder = FrameDecoder()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            chunk = client.read(100)
            if chunk:
                messages = decoder.feed(chunk)
                if messages:
                    return messages[0]
        self.fail("No Named Pipe response")

    def test_real_windows_identity_duplex_presence_and_disconnect(self):
        import win32api
        import win32con
        import win32security
        server, client = self.connect()
        request = Message.create("status.get", {})
        client.write(request.encode())
        self.assertEqual(self.receive(client).request_id, request.request_id)
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            actual_sid = win32security.ConvertSidToStringSid(
                win32security.GetTokenInformation(token, win32security.TokenUser)[0])
            actual_session = win32security.GetTokenInformation(token, win32security.TokenSessionId)
        finally:
            token.Close()
        identity = ClientIdentity(actual_sid, actual_session)
        self.assertEqual(self.identities[0], identity)
        client.write(Message.create("tray.presence", {"visible": True}).encode())
        deadline = time.monotonic() + 2
        while not server.presence.is_present(identity) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(server.presence.is_present(identity))
        # Publish while a server read is pending: writes must not cancel the read.
        server.presence.publish(identity, Message.create("status", {"managed": False}))
        self.assertEqual(self.receive(client).type, "status")
        client.write(Message.create("status.get", {}).encode())
        self.assertEqual(self.receive(client).type, "status")
        client.close()
        deadline = time.monotonic() + 2
        while server.presence.is_present(identity) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(server.presence.is_present(identity))

    def test_client_cannot_create_another_server_instance(self):
        import pywintypes
        import win32pipe
        import win32security
        server, client = self.connect()
        # Inspect IU's explicit rights independent of the test runner's admin SID.
        sd = win32security.GetSecurityInfo(server._handles[0], win32security.SE_KERNEL_OBJECT,
                                           win32security.DACL_SECURITY_INFORMATION)
        acl = sd.GetSecurityDescriptorDacl()
        masks = [acl.GetAce(index)[1] for index in range(acl.GetAceCount())
                 if win32security.ConvertSidToStringSid(acl.GetAce(index)[2]) == "S-1-5-4"]
        self.assertEqual(masks, [0x0012019B])
        self.assertFalse(masks[0] & 4)
        with self.assertRaises(pywintypes.error):
            win32pipe.CreateNamedPipe(server.name, win32pipe.PIPE_ACCESS_DUPLEX
                                     | win32pipe.FILE_FLAG_FIRST_PIPE_INSTANCE, 0, 2, 4096, 4096, 0, None)

    def test_payload_cannot_spoof_sid_or_send_privileged_operations(self):
        server, client = self.connect()
        client.write(Message.create("tray.presence", {"visible": True, "windows_sid": SID}).encode())
        with self.assertRaises(Exception):
            for _ in range(25):
                if client.read(100) == b"":
                    raise ConnectionError()
        self.assertFalse(server.presence.is_present(ClientIdentity(SID, 1)))


@unittest.skipUnless(os.name == "nt", "Requires Windows/pywin32")
class ServiceTests(unittest.TestCase):
    def test_scm_recovery_is_visible_autostart_system_with_restart_only(self):
        from ogk_agent.service import windows_service as service
        import win32service
        with patch.object(win32service, "OpenSCManager", return_value="manager"), \
             patch.object(win32service, "OpenService", return_value="service"), \
             patch.object(win32service, "CloseServiceHandle") as close, \
             patch.object(win32service, "ChangeServiceConfig") as configure, \
             patch.object(win32service, "ChangeServiceConfig2") as recovery:
            service.configure_recovery()
        configuration = configure.call_args.args
        self.assertEqual(configuration[1], win32service.SERVICE_WIN32_OWN_PROCESS)
        self.assertEqual(configuration[2], win32service.SERVICE_AUTO_START)
        self.assertEqual(configuration[8], "LocalSystem")
        actions = recovery.call_args_list[0].args[2]
        self.assertEqual(actions["Actions"], ((win32service.SC_ACTION_RESTART, 5000),
                                            (win32service.SC_ACTION_RESTART, 15000),
                                            (win32service.SC_ACTION_RESTART, 60000)))
        self.assertIsNone(actions["Command"])
        self.assertEqual(close.call_count, 2)

    def test_runtime_failure_cleans_up_and_reaches_pywin32_failure_reporting(self):
        from ogk_agent.service import windows_service as module
        service = OpenGuardKidsService.__new__(OpenGuardKidsService)
        service._stop = Mock()
        service._stop.wait.return_value = False
        runtime = Mock()
        runtime.tick.side_effect = RuntimeError("synthetic worker failure")
        with patch.object(module, "require_system"), \
             patch.object(module.servicemanager, "LogInfoMsg"), \
             patch.object(module.servicemanager, "LogErrorMsg"), \
             patch.object(OpenGuardKidsService, "runtime_factory", return_value=runtime):
            with self.assertRaisesRegex(RuntimeError, "runtime failed"):
                service.SvcDoRun()
        runtime.close.assert_called_once()

    def test_normal_stop_cleans_up_without_failure(self):
        from ogk_agent.service import windows_service as module
        service = OpenGuardKidsService.__new__(OpenGuardKidsService)
        service._stop = Mock()
        service._stop.wait.return_value = True
        runtime = Mock()
        with patch.object(module, "require_system"), \
             patch.object(module.servicemanager, "LogInfoMsg"), \
             patch.object(OpenGuardKidsService, "runtime_factory", return_value=runtime):
            service.SvcDoRun()
        runtime.close.assert_called_once()
        runtime.tick.assert_not_called()

    def test_module_entrypoint_registers_importable_package_class(self):
        from ogk_agent.service import windows_service as module
        with patch.object(module.win32serviceutil, "HandleCommandLine") as command, \
             patch.object(module, "configure_recovery") as recovery, \
             patch.object(sys, "argv", ["windows_service.py", "install"]):
            module.main()
        self.assertEqual(command.call_args.kwargs["serviceClassString"],
                         "ogk_agent.service.windows_service.OpenGuardKidsService")
        recovery.assert_called_once()


@unittest.skipUnless(os.name == "nt", "Requires Windows/pywin32")
class WindowsAdapterTests(unittest.TestCase):
    def test_hash_cache_invalidates_when_write_timestamp_is_restored(self):
        from ogk_agent.app_control.blocker import locked_executable
        directory = temporary_directory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "synthetic.bin"
        path.write_bytes(b"original")
        stamp = path.stat()
        cache = HashCache()
        with locked_executable(str(path)) as stream:
            first = cache.digest(stream)
        path.write_bytes(b"modified")  # Same size and file ID.
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with locked_executable(str(path)) as stream:
            self.assertNotEqual(first, cache.digest(stream))

    def test_process_handle_inspection_without_terminating_anything(self):
        from ogk_agent.app_control.blocker import WindowsProcessTarget
        target = WindowsProcessTarget(os.getpid())
        try:
            self.assertTrue(target.same_process())
            self.assertEqual(target.name.casefold(), Path(sys.executable).name.casefold())
            self.assertTrue(target.sid.startswith("S-1-"))
        finally:
            target.close()

    def test_executable_cannot_be_changed_while_hash_handle_is_held(self):
        from ogk_agent.app_control.blocker import locked_executable
        directory = temporary_directory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "synthetic.bin"
        path.write_bytes(b"neutral fixture")
        with locked_executable(str(path)) as stream:
            self.assertEqual(HashCache().digest(stream), hashlib.sha256(b"neutral fixture").hexdigest())
            with self.assertRaises(OSError):
                path.write_bytes(b"replacement")
        path.write_bytes(b"replacement")

    def test_scan_skips_native_access_denied_instead_of_crashing_worker(self):
        import pywintypes
        def denied(pid):
            raise pywintypes.error(5, "OpenProcess", "Synthetic access denied")
        blocker = AppBlocker(windows_sid=SID, session_id=1,
                             blocked=[AppRule("fixture.exe", "a" * 64)],
                             can_enforce=lambda: True, notify=Mock(), record=Mock(),
                             reason="Test", rule_set_by="Test Parent", policy_id="p1",
                             target_factory=denied, pids=lambda: [99999])
        self.assertEqual(blocker.scan_once(), 0)


if __name__ == "__main__":
    unittest.main()
