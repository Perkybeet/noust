# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Shipping the audit log off the machine (ENS G04).

The local chain can be rewritten by root; a receiver that got the events as
they happened cannot. These tests hold the wire formats to their RFCs
(5424 for the message, 6587 for stream framing, journald's native protocol)
against real listeners on loopback, and the shipper to its promise: a
receiver that is down gets what it missed when it is back, and one that stays
down is reported.

TLS end to end would need a certificate, and generating one needs either the
``openssl`` binary (which only the runner may execute) or a library Noust
does not depend on; the TLS context and the framing it carries are tested
instead.
"""

from __future__ import annotations

import io
import json
import re
import socket
import ssl
import struct
import threading
import time
from pathlib import Path

import pytest

from noust.core.audit import Actor, health
from noust.core.audit.log import AuditLog
from noust.core.audit.settings import AuditSettings, SyslogDestination
from noust.core.audit.shipper import Shipper, load_state, sink_report
from noust.core.audit.sinks import (
    JournaldSink,
    StdoutSink,
    SyslogSink,
    format_rfc5424,
    inline_sinks,
    octet_counted,
    tls_context,
)

ENTRY = {
    "v": 2,
    "seq": 18422,
    "id": "0198f1c27b1e7a559d1e3c2f1b7e5a10",
    "ts": "2026-09-29T21:14:07.123456+00:00",
    "action": "apps.delete",
    "result": "ok",
    "sev": 5,
    "cat": "change",
    "actor": "maria.adm",
    "who": {"kind": "user", "name": "maria.adm", "role": "admin", "via": "web"},
    "ip": "10.20.0.15",
    "resource": 'app:shop"]\\x',
    "corr": "7f3c19aa",
    "host": "central1.corp.example",
    "pid": 4123,
    "prev": "9f2c",
    "mac": "e1a7",
}

RFC5424 = re.compile(
    r"^<(?P<pri>\d{1,3})>1 (?P<ts>\S+) (?P<host>\S+) (?P<app>\S+) (?P<procid>\S+) "
    r"(?P<msgid>\S+) (?P<sd>(?:\[.*?(?<!\\)\])+) (?P<msg>.*)$"
)


@pytest.fixture
def log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "web-audit.log", settings=AuditSettings(journald="off"))


class TestRfc5424:
    def test_the_header_follows_the_rfc(self) -> None:
        message = format_rfc5424(ENTRY, enterprise_id=32473).decode()
        match = RFC5424.match(message)
        assert match, message
        assert match["pri"] == "109"  # facility 13 (log audit) * 8 + notice
        assert match["ts"] == "2026-09-29T21:14:07.123Z"
        assert match["host"] == "central1.corp.example"
        assert match["app"] == "noust-audit"
        assert match["procid"] == "4123"
        assert match["msgid"] == "apps.delete"

    def test_structured_data_uses_the_enterprise_number(self) -> None:
        message = format_rfc5424(ENTRY, enterprise_id=54321).decode()
        assert '[noust@54321 seq="18422" id="0198f1c27b1e7a559d1e3c2f1b7e5a10"' in message
        assert 'actor="maria.adm" atype="user" role="admin" via="web"' in message
        assert '[origin enterpriseId="54321" software="noust"' in message
        assert '[timeQuality tzKnown="1"]' in message
        assert 'mac="e1a7"' in message

    def test_param_values_are_escaped(self) -> None:
        """RFC 5424 6.3.3: '"', '\\' and ']' are escaped inside a PARAM-VALUE."""
        message = format_rfc5424(ENTRY, enterprise_id=32473).decode()
        assert 'res="app:shop\\"\\]\\\\x"' in message

    def test_control_characters_cannot_forge_a_line(self) -> None:
        forged = {**ENTRY, "resource": "x\n<13>1 2026-01-01T00:00:00Z evil - - - - forged"}
        message = format_rfc5424(forged, enterprise_id=32473).decode()
        assert "\n" not in message

    def test_a_failure_raises_the_severity(self) -> None:
        message = format_rfc5424({**ENTRY, "sev": 4}, enterprise_id=32473, facility=10)
        assert message.startswith(b"<84>1 ")

    def test_octet_counting(self) -> None:
        assert octet_counted("<14>1 é".encode()) == b"8 <14>1 \xc3\xa9"


class TestJournald:
    def test_the_native_protocol_on_a_datagram_socket(self, tmp_path: Path) -> None:
        path = tmp_path / "journal.socket"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(str(path))
        try:
            sink = JournaldSink(path)
            assert sink.available()
            sink.send({**ENTRY, "details": {"note": "two\nlines"}})
            datagram = server.recv(65536)
        finally:
            server.close()
        fields = dict(line.split(b"=", 1) for line in datagram.split(b"\n") if b"=" in line[:40])
        assert fields[b"SYSLOG_IDENTIFIER"] == b"noust-audit"
        assert fields[b"PRIORITY"] == b"5"
        assert fields[b"NOUST_ACTION"] == b"apps.delete"
        assert fields[b"NOUST_SEQ"] == b"18422"
        assert fields[b"NOUST_ROLE"] == b"admin"
        event = json.loads(fields[b"NOUST_EVENT"].decode())
        assert event["details"] == {"note": "two\nlines"}

    def test_a_value_with_a_newline_uses_the_binary_form(self) -> None:
        """journald's protocol: NAME, newline, 64-bit little-endian length, value."""
        from noust.core.audit.sinks import _journal_field

        assert _journal_field("NOTE", "a\nb") == b"NOTE\n" + struct.pack("<Q", 3) + b"a\nb\n"
        assert _journal_field("NOTE", "ab") == b"NOTE=ab\n"

    def test_auto_means_only_when_journald_is_there(self, tmp_path: Path) -> None:
        assert inline_sinks(AuditSettings(journald="auto")) == []
        assert [sink.sink_id for sink in inline_sinks(AuditSettings(journald="on"))] == ["journald"]
        assert inline_sinks(AuditSettings(journald="off")) == []

    def test_every_recorded_event_reaches_journald(self, tmp_path: Path) -> None:
        path = tmp_path / "journal.socket"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(str(path))
        server.settimeout(2)
        try:
            log = AuditLog(tmp_path / "web-audit.log", settings=AuditSettings(journald="on"))
            log._inline = [JournaldSink(path)]
            log.append("apps.restart", actor=Actor(kind="user", name="maria"))
            actions = {
                server.recv(65536).split(b"NOUST_ACTION=")[1].split(b"\n")[0] for _ in range(2)
            }
        finally:
            server.close()
        assert actions == {b"audit.chain_start", b"apps.restart"}


def udp_listener() -> tuple[socket.socket, int]:
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(3)
    return server, server.getsockname()[1]


class TcpListener:
    """A syslog receiver on loopback that reads octet-counted frames."""

    def __init__(self) -> None:
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.port = self.server.getsockname()[1]
        self.frames: list[bytes] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        connection, _ = self.server.accept()
        buffer = b""
        with connection:
            while chunk := connection.recv(65536):
                buffer += chunk
                while b" " in buffer:
                    length, _, rest = buffer.partition(b" ")
                    if len(rest) < int(length):
                        break
                    self.frames.append(rest[: int(length)])
                    buffer = rest[int(length) :]

    def wait_for(self, count: int) -> list[bytes]:
        deadline = time.monotonic() + 3
        while len(self.frames) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.frames

    def close(self) -> None:
        self.server.close()


class TestSyslogTransports:
    def test_udp(self) -> None:
        server, port = udp_listener()
        try:
            sink = SyslogSink(
                SyslogDestination(transport="udp", host="127.0.0.1", port=port), enterprise_id=32473
            )
            sink.send(ENTRY)
            message = server.recv(65536)
        finally:
            server.close()
        assert RFC5424.match(message.decode())

    def test_tcp_frames_each_message_with_its_length(self) -> None:
        listener = TcpListener()
        try:
            sink = SyslogSink(
                SyslogDestination(transport="tcp", host="127.0.0.1", port=listener.port),
                enterprise_id=32473,
            )
            sink.send(ENTRY)
            sink.send({**ENTRY, "seq": 18423})
            frames = listener.wait_for(2)
            sink.close()
        finally:
            listener.close()
        assert len(frames) == 2
        assert b'seq="18423"' in frames[1]
        assert all(RFC5424.match(frame.decode()) for frame in frames)

    def test_unix_datagram(self, tmp_path: Path) -> None:
        path = tmp_path / "log.socket"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(str(path))
        try:
            SyslogSink(SyslogDestination(transport="unix", path=str(path)), enterprise_id=1).send(
                ENTRY
            )
            assert RFC5424.match(server.recv(65536).decode())
        finally:
            server.close()

    def test_an_unreachable_receiver_raises_for_the_shipper_to_retry(self) -> None:
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        sink = SyslogSink(
            SyslogDestination(transport="tcp", host="127.0.0.1", port=port), enterprise_id=1
        )
        with pytest.raises(OSError):
            sink.send(ENTRY)


class TestTlsContext:
    def test_it_verifies_and_refuses_anything_below_tls_1_2(self) -> None:
        context = tls_context(SyslogDestination(transport="tls", host="siem", port=6514))
        assert context.minimum_version == ssl.TLSVersion.TLSv1_2
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname

    def test_a_configured_ca_is_the_only_trust_anchor(self, tmp_path: Path) -> None:
        missing = tmp_path / "no-such-ca.pem"
        with pytest.raises(OSError):
            tls_context(SyslogDestination(transport="tls", host="siem", port=6514, ca=str(missing)))

    def test_a_wrong_certificate_pin_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The pin is checked after the handshake; a mismatch closes the connection."""

        class Wrapped:
            closed = False

            def getpeercert(self, binary_form: bool = False) -> bytes:
                return b"another certificate"

            def close(self) -> None:
                Wrapped.closed = True

        class Context:
            def wrap_socket(self, raw, server_hostname=None):
                return Wrapped()

        monkeypatch.setattr("noust.core.audit.sinks.tls_context", lambda destination: Context())
        monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: io.BytesIO())
        sink = SyslogSink(
            SyslogDestination(transport="tls", host="siem", port=6514, pin_sha256="00" * 32),
            enterprise_id=1,
        )
        with pytest.raises(ssl.SSLError, match="pin_sha256"):
            sink.send(ENTRY)
        assert Wrapped.closed


class FakeSink:
    """A destination that records what it got, and can be made to fail."""

    def __init__(self, sink_id: str = "fake", *, backfill: bool = True) -> None:
        self.sink_id = sink_id
        self.received: list[dict] = []
        self.down = False
        self.destination = SyslogDestination(transport="udp", host="x", port=1, backfill=backfill)

    def send(self, entry: dict) -> None:
        if self.down:
            raise ConnectionRefusedError("receiver down")
        self.received.append(entry)

    def close(self) -> None:
        pass


def fill(log: AuditLog, count: int) -> None:
    for _ in range(count):
        log.append("apps.update", actor=Actor(kind="user", name="maria"))


class TestShipper:
    def test_what_follows_the_cursor_is_sent_once(self, log: AuditLog) -> None:
        sink = FakeSink()
        shipper = Shipper(log, [sink])
        fill(log, 3)
        assert shipper.ship_once() == {"fake": 4}
        fill(log, 2)
        assert shipper.ship_once() == {"fake": 2}
        assert [entry["seq"] for entry in sink.received] == [1, 2, 3, 4, 5, 6]
        assert load_state(shipper.state_path)["fake"]["seq"] == 6
        assert shipper.state_path.stat().st_mode & 0o777 == 0o600

    def test_a_new_destination_starts_from_now_unless_it_backfills(self, log: AuditLog) -> None:
        fill(log, 3)
        fresh = FakeSink("fresh", backfill=False)
        shipper = Shipper(log, [fresh])
        shipper.ship_once()
        assert fresh.received == []
        fill(log, 1)
        shipper.ship_once()
        assert [entry["seq"] for entry in fresh.received] == [5]

    def test_a_receiver_that_was_down_gets_what_it_missed(self, log: AuditLog) -> None:
        clock = [1000.0]
        sink = FakeSink()
        shipper = Shipper(log, [sink], clock=lambda: clock[0])
        fill(log, 2)
        sink.down = True
        assert shipper.ship_once() == {"fake": 0}
        state = load_state(shipper.state_path)["fake"]
        assert state["error"] == "receiver down"
        assert state["seq"] == 0

        sink.down = False
        assert shipper.ship_once() == {"fake": 0}, "retried before its back-off ended"
        clock[0] += 60
        assert shipper.ship_once() == {"fake": 3}
        assert "error" not in load_state(shipper.state_path)["fake"]

    def test_a_receiver_that_stays_down_is_reported(self, log: AuditLog) -> None:
        clock = [time.time()]
        sink = FakeSink()
        shipper = Shipper(log, [sink], clock=lambda: clock[0])
        fill(log, 1)
        sink.down = True
        shipper.ship_once()
        clock[0] += 16 * 60
        shipper.ship_once()

        assert [entry["action"] for entry in log.read(action="audit.sink.degraded")]
        report = {item["sink_id"]: item for item in sink_report(log)}
        assert report["fake"]["degraded"]
        assert health(log).status == "error"

        sink.down = False
        clock[0] += 600
        shipper.ship_once()
        assert log.read(action="audit.sink.recovered")
        assert not sink_report(log)[0]["degraded"]

    def test_retention_waits_for_the_slowest_destination(self, log: AuditLog) -> None:
        fast, slow = FakeSink("fast"), FakeSink("slow")
        shipper = Shipper(log, [fast, slow])
        fill(log, 3)
        slow.down = True
        shipper.ship_once()
        assert shipper.lowest_cursor() == 0
        assert Shipper(log, []).lowest_cursor() is None

    def test_stdout_writes_one_json_line_per_event(self, log: AuditLog) -> None:
        stream = io.StringIO()
        shipper = Shipper(log, [StdoutSink(stream)])
        load_state(shipper.state_path)
        fill(log, 1)
        # stdout has no backfill: it starts from the head it first sees.
        shipper.ship_once()
        fill(log, 2)
        shipper.ship_once()
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert [event["seq"] for event in events] == [3, 4]

    def test_through_a_real_udp_receiver(self, log: AuditLog) -> None:
        server, port = udp_listener()
        try:
            sink = SyslogSink(
                SyslogDestination(transport="udp", host="127.0.0.1", port=port, backfill=True),
                enterprise_id=32473,
            )
            fill(log, 1)
            assert Shipper(log, [sink]).ship_once() == {sink.sink_id: 2}
            messages = [server.recv(65536).decode() for _ in range(2)]
        finally:
            server.close()
        assert [RFC5424.match(message)["msgid"] for message in messages] == [
            "audit.chain_start",
            "apps.update",
        ]
