# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where audit events go besides the local log (ENS G04).

The local, chained log is the source of truth, and root on the machine can
rewrite it (see :mod:`noust.core.audit.chain`); a copy on another machine,
written as the events happened, is what turns that into a detectable act.
Everything here is the standard library: no logging agent to install.

- **journald**, through its native protocol: one datagram to
  ``/run/systemd/journal/socket`` with ``SYSLOG_IDENTIFIER=noust-audit`` and
  ``NOUST_*`` fields, so ``journalctl -t noust-audit -o json`` reads them
  and ``rsyslog``'s ``imjournal``, ``systemd-journal-upload``, Wazuh or
  Filebeat forward them. Written as each event is recorded.
- **syslog**, RFC 5424 over a UNIX socket, UDP (RFC 5426), TCP with octet
  counting (RFC 6587) or TLS (RFC 5425) with an optional client certificate,
  a CA that is the only trust anchor and an optional certificate pin.
- **stdout**, one JSON object per line: the central's container has no
  journald, and its log is what the operator collects.

syslog and stdout are fed by :class:`~noust.core.audit.shipper.Shipper`,
which reads the log from a saved cursor: a receiver that is down receives
what it missed when it is back.
"""

from __future__ import annotations

import errno
import hashlib
import json
import socket
import ssl
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Protocol

from noust import __version__
from noust.core.audit.settings import AuditSettings, SyslogDestination

#: journald's native socket.
JOURNALD_SOCKET = Path("/run/systemd/journal/socket")

#: The APP-NAME of every syslog message and the journal's SYSLOG_IDENTIFIER.
APP_NAME = "noust-audit"

#: Largest UDP message sent; RFC 5426 asks receivers to take at least 2048.
MAX_UDP_BYTES = 8192

#: Seconds to connect to a stream receiver before giving up this round.
CONNECT_TIMEOUT = 5.0


class Sink(Protocol):
    """A destination events are delivered to, one at a time."""

    sink_id: str

    def send(self, entry: dict[str, Any]) -> None:
        """
        Deliver one event.

        Args:
            entry: The event as the log stores it.

        Raises:
            OSError: It could not be delivered; the caller retries later.
        """

    def close(self) -> None:
        """Release any connection held open."""


# -- journald ----------------------------------------------------------------


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _journal_field(name: str, value: str) -> bytes:
    data = value.encode("utf-8", errors="replace")
    if b"\n" in data:
        return name.encode("ascii") + b"\n" + struct.pack("<Q", len(data)) + data + b"\n"
    return name.encode("ascii") + b"=" + data + b"\n"


def summary(entry: dict[str, Any]) -> str:
    """
    One line saying what an event was, for a human reading a log.

    Args:
        entry: The event.

    Returns:
        ``<actor> <action> <target>: <outcome>``, control characters
        replaced, so a crafted target cannot forge a second log line
        (CWE-117).
    """
    parts = [str(entry.get("actor", "-")), str(entry.get("action", "-"))]
    if entry.get("resource"):
        parts.append(str(entry["resource"]))
    text = f"{' '.join(parts)}: {entry.get('result', '-')}"
    count = (
        (entry.get("details") or {}).get("count")
        if isinstance(entry.get("details"), dict)
        else None
    )
    if count:
        text += f" ({count} occurrences)"
    return "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in text)


def journal_fields(entry: dict[str, Any]) -> list[tuple[str, str]]:
    """
    The journal fields of one event.

    Args:
        entry: The event.

    Returns:
        ``(NAME, value)`` pairs; empty values are left out.
    """
    who = _mapping(entry.get("who"))
    fields = [
        ("MESSAGE", summary(entry)),
        ("PRIORITY", str(entry.get("sev", 6))),
        ("SYSLOG_IDENTIFIER", APP_NAME),
        ("SYSLOG_FACILITY", "13"),
        ("NOUST_SEQ", str(entry.get("seq", ""))),
        ("NOUST_ID", str(entry.get("id", ""))),
        ("NOUST_ACTION", str(entry.get("action", ""))),
        ("NOUST_CATEGORY", str(entry.get("cat", ""))),
        ("NOUST_ACTOR", str(entry.get("actor", ""))),
        ("NOUST_ACTOR_KIND", str(who.get("kind", ""))),
        ("NOUST_ROLE", str(who.get("role", "") or "")),
        ("NOUST_OUTCOME", str(entry.get("result", ""))),
        ("NOUST_SRC_IP", str(entry.get("ip", "") or "")),
        ("NOUST_TARGET", str(entry.get("resource", "") or "")),
        ("NOUST_CORRELATION", str(entry.get("corr", "") or "")),
        ("NOUST_MAC", str(entry.get("mac", ""))),
        ("NOUST_EVENT", json.dumps(entry, sort_keys=True, ensure_ascii=True)),
    ]
    return [(name, value) for name, value in fields if value]


class JournaldSink:
    """Write events to journald through its native datagram protocol."""

    sink_id = "journald"

    def __init__(self, socket_path: Path | None = None) -> None:
        """
        Args:
            socket_path: journald's socket; :data:`JOURNALD_SOCKET` by default.
        """
        self.path = socket_path or JOURNALD_SOCKET
        self._socket: socket.socket | None = None

    def available(self) -> bool:
        """
        Whether journald is listening on this machine.

        Returns:
            True when the socket exists.
        """
        return self.path.is_socket()

    def send(self, entry: dict[str, Any]) -> None:
        """
        Send one event as one journal entry.

        A datagram too large for the socket buffer is sent again without
        the full ``NOUST_EVENT`` copy, rather than not at all.

        Args:
            entry: The event.

        Raises:
            OSError: journald did not take it.
        """
        fields = journal_fields(entry)
        datagram = b"".join(_journal_field(name, value) for name, value in fields)
        if self._socket is None:
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            self._socket.sendto(datagram, str(self.path))
        except OSError as exc:
            if exc.errno not in (errno.EMSGSIZE, errno.ENOBUFS):
                raise
            smaller = b"".join(
                _journal_field(name, value) for name, value in fields if name != "NOUST_EVENT"
            )
            self._socket.sendto(smaller, str(self.path))

    def close(self) -> None:
        """Close the socket."""
        if self._socket is not None:
            self._socket.close()
            self._socket = None


# -- syslog (RFC 5424) ------------------------------------------------------


def _printable(value: str, limit: int) -> str:
    text = "".join(char for char in value if 33 <= ord(char) <= 126)
    return text[:limit] or "-"


def _sd_value(value: Any) -> str:
    text = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in str(value))
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def _timestamp(value: Any) -> str:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return "-"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def format_rfc5424(
    entry: dict[str, Any], *, enterprise_id: int, facility: int = 13, hostname: str | None = None
) -> bytes:
    """
    Render one event as an RFC 5424 message.

    ``MSGID`` is the event name, which the catalog keeps within the 32
    printable characters the RFC allows. Structured data goes under
    ``noust@<enterprise_id>`` (names without an ``@`` are reserved to the
    IETF), with ``origin`` and ``timeQuality`` from RFC 5424 section 7.

    Args:
        entry: The event.
        enterprise_id: The IANA Private Enterprise Number of the SD-ID.
        facility: Syslog facility; 13 is "log audit".
        hostname: HOSTNAME when the event does not carry one.

    Returns:
        The message, UTF-8, without framing.
    """
    severity = int(entry.get("sev", 6))
    who = _mapping(entry.get("who"))
    params = {
        "seq": entry.get("seq"),
        "id": entry.get("id"),
        "actor": entry.get("actor"),
        "atype": who.get("kind"),
        "role": who.get("role"),
        "via": who.get("via"),
        "ip": entry.get("ip"),
        "out": entry.get("result"),
        "res": entry.get("resource"),
        "cat": entry.get("cat"),
        "corr": entry.get("corr"),
        "sens": "1" if entry.get("sensitive") else None,
        "prev": entry.get("prev"),
        "mac": entry.get("mac"),
    }
    noust = " ".join(
        f'{name}="{_sd_value(value)}"' for name, value in params.items() if value not in (None, "")
    )
    structured = (
        f"[noust@{enterprise_id} {noust}]"
        f'[origin enterpriseId="{enterprise_id}" software="noust" swVersion="{_sd_value(__version__)}"]'
        '[timeQuality tzKnown="1"]'
    )
    header = " ".join(
        [
            f"<{facility * 8 + severity}>1",
            _timestamp(entry.get("ts")),
            _printable(str(entry.get("host") or hostname or "-"), 255),
            APP_NAME,
            _printable(str(entry.get("pid") or "-"), 128),
            _printable(str(entry.get("action") or "-"), 32),
        ]
    )
    return f"{header} {structured} {summary(entry)}".encode()


def octet_counted(message: bytes) -> bytes:
    """
    Frame a message for a stream transport (RFC 6587 section 3.4.1).

    Args:
        message: The RFC 5424 message.

    Returns:
        ``MSG-LEN SP SYSLOG-MSG``.
    """
    return str(len(message)).encode("ascii") + b" " + message


def tls_context(destination: SyslogDestination) -> ssl.SSLContext:
    """
    The TLS client context for a receiver.

    Certificate and host name are always checked, TLS 1.2 is the floor, and
    a configured CA is the only trust anchor: the system store is not
    consulted, so a certificate any public CA would sign for the name is not
    enough.

    Args:
        destination: The receiver.

    Returns:
        The context.

    Raises:
        OSError: A CA, certificate or key file cannot be read.
    """
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=destination.ca)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    if destination.client_cert:
        context.load_cert_chain(destination.client_cert, destination.client_key)
    return context


class SyslogSink:
    """Ship events to one RFC 5424 receiver."""

    def __init__(
        self,
        destination: SyslogDestination,
        *,
        enterprise_id: int,
        hostname: str | None = None,
        timeout: float = CONNECT_TIMEOUT,
    ) -> None:
        """
        Args:
            destination: The receiver.
            enterprise_id: The PEN of the structured data ID.
            hostname: HOSTNAME for events that do not carry one.
            timeout: Seconds allowed to connect and to send.
        """
        self.destination = destination
        self.sink_id = destination.sink_id
        self.enterprise_id = enterprise_id
        self.hostname = hostname or socket.gethostname()
        self.timeout = timeout
        self._socket: socket.socket | None = None

    def _connect(self) -> socket.socket:
        destination = self.destination
        if destination.transport == "unix":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            sock.settimeout(self.timeout)
            sock.connect(str(destination.path))
            return sock
        if destination.host is None or destination.port is None:
            raise OSError(f"{self.sink_id} has no host and port to send to")
        if destination.transport == "udp":
            family, kind, proto, _, address = socket.getaddrinfo(
                destination.host, destination.port, type=socket.SOCK_DGRAM
            )[0]
            sock = socket.socket(family, kind, proto)
            sock.settimeout(self.timeout)
            sock.connect(address)
            return sock
        raw = socket.create_connection((destination.host, destination.port), timeout=self.timeout)
        if destination.transport == "tcp":
            return raw
        try:
            wrapped = tls_context(destination).wrap_socket(
                raw, server_hostname=destination.server_name or destination.host
            )
        except OSError:
            raw.close()
            raise
        if destination.pin_sha256:
            peer = wrapped.getpeercert(binary_form=True) or b""
            if hashlib.sha256(peer).hexdigest() != destination.pin_sha256:
                wrapped.close()
                raise ssl.SSLError(
                    f"the certificate of {destination.host} does not match pin_sha256"
                )
        return wrapped

    def send(self, entry: dict[str, Any]) -> None:
        """
        Send one event, connecting first when needed.

        Args:
            entry: The event.

        Raises:
            OSError: The receiver cannot be reached or refused it; the
                connection is dropped so the next attempt starts afresh.
        """
        message = format_rfc5424(
            entry,
            enterprise_id=self.enterprise_id,
            facility=self.destination.facility,
            hostname=self.hostname,
        )
        stream = self.destination.transport in ("tcp", "tls")
        if not stream:
            message = message[:MAX_UDP_BYTES]
        try:
            if self._socket is None:
                self._socket = self._connect()
            if stream:
                self._socket.sendall(octet_counted(message))
            else:
                self._socket.send(message)
        except OSError:
            self.close()
            raise

    def close(self) -> None:
        """Drop the connection."""
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None


# -- stdout -----------------------------------------------------------------


class StdoutSink:
    """Write each event as one JSON line to standard output."""

    sink_id = "stdout"

    def __init__(self, stream: IO[str] | None = None) -> None:
        """
        Args:
            stream: Where to write; the process's standard output by default.
        """
        self._stream = stream

    def send(self, entry: dict[str, Any]) -> None:
        """
        Write one event.

        Args:
            entry: The event.

        Raises:
            OSError: The stream is closed or full.
        """
        stream = self._stream or sys.stdout
        try:
            stream.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")
            stream.flush()
        except ValueError as exc:
            # A closed stream raises ValueError, not OSError: the same failure.
            raise OSError(str(exc)) from exc

    def close(self) -> None:
        """Nothing to release."""


def shipping_sinks(settings: AuditSettings) -> list[Sink]:
    """
    The destinations the shipper feeds.

    Args:
        settings: The audit settings.

    Returns:
        One sink per syslog receiver, plus standard output when enabled.
    """
    sinks: list[Sink] = [
        SyslogSink(destination, enterprise_id=settings.enterprise_id)
        for destination in settings.syslog
    ]
    if settings.stdout_enabled():
        sinks.append(StdoutSink())
    return sinks


def inline_sinks(settings: AuditSettings) -> list[Sink]:
    """
    The destinations written as each event is recorded.

    Args:
        settings: The audit settings.

    Returns:
        journald when it is switched on, or on ``auto`` when its socket exists.
    """
    if settings.journald == "off":
        return []
    journald = JournaldSink()
    if settings.journald == "on" or journald.available():
        return [journald]
    return []
