"""Loopback UDP/TCP DNS proxy, with explicit authenticated per-query scope.

DNS has no SID. context_for_query must be provided by a separately validated
per-user routing layer, including isolation of the Windows shared DNS cache.
Without it this proxy only forwards: no child attribution, filtering or logging.
It never changes adapter DNS, hosts, browser settings or TLS traffic.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import ipaddress
import logging
import re
import secrets
import select
import socket
import struct
import threading
import time
from typing import Callable

from dnslib import CNAME, DNSHeader, DNSRecord, DNSError, QTYPE, RCODE, RR

LOG = logging.getLogger(__name__)
SAFESEARCH_TARGET = "forcesafesearch.google.com"


def normalize_domain(value: str) -> str:
    value = value.rstrip(".").encode("idna").decode("ascii").lower()
    if not value or len(value) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in value.split(".")
    ):
        raise ValueError("Expected a domain name, not a URL or wildcard")
    return value


def matches(domain: str, rules: frozenset[str]) -> bool:
    return any(domain == rule or domain.endswith("." + rule) for rule in rules)


@dataclass(frozen=True)
class DnsScope:
    binding_id: str
    policy_id: str
    policy_version: int
    blocked_domains: frozenset[str]
    allowed_domains: frozenset[str]
    safety_domains: frozenset[str]
    # Explicit Google country names only; no broad *.google.* matching.
    google_search_domains: frozenset[str]
    reason: str
    rule_set_by: str
    can_enforce: Callable[[], bool]

    def __post_init__(self) -> None:
        if not all((self.binding_id, self.policy_id, self.reason, self.rule_set_by)) or self.policy_version < 1:
            raise ValueError("Scope and explanation are required")
        for field in ("blocked_domains", "allowed_domains", "safety_domains", "google_search_domains"):
            object.__setattr__(self, field, frozenset(normalize_domain(value) for value in getattr(self, field)))


@dataclass(frozen=True)
class BlockedDomain:
    domain: str
    binding_id: str
    policy_id: str
    reason: str
    rule_set_by: str


class DNSProxy:
    def __init__(
        self, *, upstream: tuple[str, int], mandatory_safety_domains: frozenset[str],
        context_for_query: Callable[[bytes, tuple[str, int], str], DnsScope | None] | None = None,
        notify: Callable[[BlockedDomain], bool | None] | None = None,
        port: int = 53, timeout: float = 1.5, workers: int = 8,
    ) -> None:
        address = ipaddress.ip_address(upstream[0])
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ValueError("Upstream must be a unicast resolver IP outside loopback")
        if not 1 <= upstream[1] <= 65535 or not 0 <= port <= 65535 or not 0 < timeout <= 2:
            raise ValueError("Invalid DNS endpoint or timeout")
        if not 1 <= workers <= 32:
            raise ValueError("Invalid worker count")
        self.upstream, self.port, self.timeout, self.workers = upstream, port, timeout, workers
        self.mandatory_safety = frozenset(normalize_domain(value) for value in mandatory_safety_domains)
        if not self.mandatory_safety:
            raise ValueError("Installer must supply the immutable emergency/school safety list")
        self.context_for_query, self.notify = context_for_query, notify
        self._notices: OrderedDict[tuple, float] = OrderedDict()
        self._notice_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sockets: list[socket.socket] = []

    def _recv_exact(self, connection: socket.socket, count: int) -> bytes:
        data = bytearray()
        deadline = time.monotonic() + self.timeout
        while len(data) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("DNS stream timeout")
            connection.settimeout(remaining)
            part = connection.recv(count - len(data))
            if not part:
                raise OSError("DNS stream closed")
            data.extend(part)
        return bytes(data)

    @staticmethod
    def _valid_response(query: DNSRecord, data: bytes) -> DNSRecord:
        reply = DNSRecord.parse(data)
        if (not reply.header.qr or reply.header.id != query.header.id
                or reply.header.opcode != query.header.opcode or len(reply.questions) != 1
                or reply.q != query.q):
            raise DNSError("Unrelated upstream response")
        return reply

    @staticmethod
    def _error(query: DNSRecord, code: int) -> bytes:
        reply = query.reply(ra=1, aa=0)
        reply.header.rcode = code
        return reply.pack()

    def _exchange(self, request: DNSRecord, *, tcp: bool = False) -> DNSRecord:
        query = DNSRecord.parse(request.pack())
        query.header.id = secrets.randbits(16)
        family = socket.AF_INET6 if ":" in self.upstream[0] else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as remote:
            remote.settimeout(self.timeout)
            remote.connect(self.upstream)
            packet = query.pack()
            if tcp:
                remote.sendall(struct.pack("!H", len(packet)) + packet)
                size = struct.unpack("!H", self._recv_exact(remote, 2))[0]
                response = self._recv_exact(remote, size)
            else:
                remote.send(packet)
                response = remote.recv(65535)
        reply = self._valid_response(query, response)
        if reply.header.tc and not tcp:
            return self._exchange(request, tcp=True)
        reply.header.id = request.header.id
        return reply

    def _notice(self, scope: DnsScope, domain: str) -> bool:
        if self.notify is None:
            return False  # NT4: cannot block without an explanation channel.
        key = (scope.binding_id, scope.policy_id, scope.policy_version, domain)
        now = time.monotonic()
        with self._notice_lock:
            last = self._notices.get(key)
            if last is not None and now - last < 60:
                return True
            try:
                # Must be a fast bounded enqueue into the verified Tray channel.
                accepted = self.notify(BlockedDomain(domain, scope.binding_id, scope.policy_id,
                                                     scope.reason, scope.rule_set_by))
                if accepted is False:
                    return False
            except Exception as error:
                LOG.warning("DNS explanation unavailable: class=%s", type(error).__name__)
                return False
            self._notices[key] = now
            self._notices.move_to_end(key)
            while len(self._notices) > 4096:
                self._notices.popitem(last=False)
        return True

    def resolve(self, packet: bytes, scope: DnsScope | None = None, *, tcp: bool = False) -> bytes:
        try:
            query = DNSRecord.parse(packet)
        except (DNSError, ValueError, UnicodeError):
            ident = struct.unpack("!H", packet[:2])[0] if len(packet) >= 2 else 0
            return DNSRecord(DNSHeader(id=ident, qr=1, rcode=RCODE.FORMERR)).pack()
        if (query.header.qr or query.header.opcode != 0 or len(query.questions) != 1
                or query.q.qclass != 1):
            return self._error(query, RCODE.NOTIMP)
        try:
            try:
                domain = normalize_domain(str(query.q.qname))
            except (ValueError, UnicodeError):
                domain = ""  # Valid non-host DNS names (e.g. SRV) still forward.
            managed = bool(scope and scope.can_enforce())
            safety = self.mandatory_safety | (scope.safety_domains if managed else frozenset())
            exempt = bool(domain and matches(domain, safety))
            if managed and not exempt and domain:
                if not matches(domain, scope.allowed_domains) and matches(domain, scope.blocked_domains):
                    if self._notice(scope, domain) and scope.can_enforce():
                        return self._error(query, RCODE.NXDOMAIN)
                # Ordinary allow rules cannot disable SafeSearch; safety rules win.
                if domain in scope.google_search_domains and scope.can_enforce():
                    reply = query.reply(ra=1, aa=0)
                    reply.header.ad = 0  # Synthetic records are not DNSSEC signed.
                    reply.add_answer(RR(query.q.qname, QTYPE.CNAME, ttl=60,
                                        rdata=CNAME(SAFESEARCH_TARGET)))
                    if query.q.qtype in (QTYPE.A, QTYPE.AAAA):
                        safe_query = DNSRecord.question(SAFESEARCH_TARGET, QTYPE[query.q.qtype])
                        safe = self._exchange(safe_query, tcp=tcp)
                        if safe.header.rcode != RCODE.NOERROR:
                            raise OSError("SafeSearch target resolution failed")
                        for answer in safe.rr:
                            reply.add_answer(answer)
                    if scope.can_enforce():
                        return self._udp_limit(query, reply, tcp)
            return self._udp_limit(query, self._exchange(query, tcp=tcp), tcp)
        except (OSError, DNSError, ValueError):
            # No exception text, query packet or domain is logged.
            return self._error(query, RCODE.SERVFAIL)

    @staticmethod
    def _udp_limit(query: DNSRecord, reply: DNSRecord, tcp: bool) -> bytes:
        limit = 512
        for rr in query.ar:
            if rr.rtype == QTYPE.OPT:
                limit = min(1232, max(512, rr.rclass))
        packet = reply.pack()
        if not tcp and len(packet) > limit:
            small = query.reply(ra=1, aa=0)
            small.header.rcode = reply.header.rcode
            small.header.tc = 1
            return small.pack()
        return packet

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("DNS proxy already started")
        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sockets.append(udp)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                udp.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            udp.bind(("127.0.0.1", self.port))
            self.port = udp.getsockname()[1]
            tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sockets.append(tcp)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                tcp.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            tcp.bind(("127.0.0.1", self.port))
            tcp.listen(16)
            self._thread = threading.Thread(target=self._serve, name="ogk-dns", daemon=True)
            self._thread.start()
        except Exception:
            for listener in self._sockets:
                listener.close()
            self._sockets.clear()
            raise OSError("Cannot bind loopback DNS UDP/TCP; check port conflict") from None

    def _serve(self) -> None:
        slots = threading.BoundedSemaphore(self.workers)

        def handle(packet, peer, transport, connection=None):
            try:
                if connection is not None:
                    size = struct.unpack("!H", self._recv_exact(connection, 2))[0]
                    if not 12 <= size <= 4096:
                        return
                    packet = self._recv_exact(connection, size)
                scope = self.context_for_query(packet, peer, transport) if self.context_for_query else None
                response = self.resolve(packet, scope, tcp=connection is not None)
                if connection is None:
                    self._sockets[0].sendto(response, peer)
                else:
                    connection.settimeout(self.timeout)
                    connection.sendall(struct.pack("!H", len(response)) + response)
            except Exception as error:
                LOG.debug("DNS worker failed: class=%s", type(error).__name__)
            finally:
                if connection is not None:
                    connection.close()
                slots.release()

        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="ogk-dns-query") as pool:
            while not self._stop.is_set():
                ready, _, _ = select.select(self._sockets, [], [], 0.2)
                for listener in ready:
                    connection = None
                    if listener.type == socket.SOCK_DGRAM:
                        packet, peer = listener.recvfrom(4097)
                        if len(packet) > 4096:
                            continue
                        transport = "udp"
                    else:
                        connection, peer = listener.accept()
                        packet, transport = b"", "tcp"
                    if not slots.acquire(blocking=False):
                        if connection is not None:
                            connection.close()
                        continue
                    pool.submit(handle, packet, peer, transport, connection)

    def check_health(self) -> None:
        if self._thread and not self._thread.is_alive() and not self._stop.is_set():
            raise RuntimeError("DNS listener stopped unexpectedly")

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            # Outstanding upstream operations have finite deadlines.
            self._thread.join(timeout=12)
            if self._thread.is_alive():
                raise TimeoutError("DNS worker failed to stop")
        for listener in self._sockets:
            listener.close()
        self._sockets.clear()
