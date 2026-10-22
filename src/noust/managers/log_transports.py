# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a batch of log lines reaches a destination (checklist B-73).

One class per kind of destination, each turning a batch of :class:`LogEvent` into
what that destination takes and sending it with the standard library alone (no log
agent to install, and no new dependency):

- **Loki**: ``POST /loki/api/v1/push``, one stream per application and process.
- **Axiom**: ``POST /v1/datasets/<dataset>/ingest`` with an API token.
- **HTTP**: a JSON array of events, for Fluent Bit's ``http`` input, Vector,
  Logstash or anything else that takes one.
- **OTLP**: ``POST <endpoint>/v1/logs`` as OTLP/HTTP JSON.
- **syslog**: RFC 5424 over UDP, TCP or TLS to a remote receiver, with the same
  connection and certificate checks as the audit trail's.

A transport raises :class:`DeliveryError` when a batch was not accepted, with what
the destination said, and the shipper sends it again next time: the cursor moves
only after a batch was accepted. Nothing here logs a token: the secret reaches the
transport as an argument and leaves in a header.
"""

from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from noust.core.audit.settings import SyslogDestination
from noust.core.audit.sinks import MAX_UDP_BYTES, SyslogSink, octet_counted
from noust.core.exceptions import NoustError

#: Seconds a destination gets to accept a batch.
HTTP_TIMEOUT = 20.0

#: Most lines in one request, and most bytes of one request's body.
BATCH_LINES = 500
BATCH_BYTES = 1024 * 1024

#: The longest one line is sent: more is cut, and says so.
MAX_LINE_CHARS = 16000

#: What a cut line ends with.
CUT_MARK = " [cut by noust]"

#: The most of a destination's answer an error keeps.
ANSWER_CHARS = 300


class DeliveryError(NoustError):
    """A destination did not accept a batch; it is sent again."""


@dataclass(frozen=True)
class LogEvent:
    """
    One line of an application's log, ready to be shipped.

    Attributes:
        ts_ns: When it was written, in nanoseconds since the epoch.
        message: The line, with the application's secrets scrubbed out.
        app: The application's domain.
        process: ``web`` or the name of one of its processes; ``container`` for a
            stack's service.
        unit: The unit (or service) it came from.
        priority: Syslog severity, 0 to 7; 6 is informational, 3 an error.
        host: This server's name.
    """

    ts_ns: int
    message: str
    app: str
    process: str
    unit: str
    priority: int
    host: str

    @property
    def level(self) -> str:
        """The severity as a word."""
        return _LEVELS.get(self.priority, "info")

    @property
    def iso(self) -> str:
        """When it was written, ISO 8601 in UTC with milliseconds."""
        moment = datetime.fromtimestamp(self.ts_ns / 1e9, tz=timezone.utc)
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


_LEVELS = {
    0: "emerg",
    1: "alert",
    2: "crit",
    3: "error",
    4: "warning",
    5: "notice",
    6: "info",
    7: "debug",
}


def fit(message: str) -> str:
    """
    Cut a line that is longer than a destination should be asked to take.

    Args:
        message: The line.

    Returns:
        The line, or its start and a mark.
    """
    if len(message) <= MAX_LINE_CHARS:
        return message
    return message[: MAX_LINE_CHARS - len(CUT_MARK)] + CUT_MARK


def batches(events: Sequence[LogEvent]) -> list[list[LogEvent]]:
    """
    Split events into requests of a size a destination takes.

    Args:
        events: The events, in order.

    Returns:
        Consecutive groups of at most :data:`BATCH_LINES` lines and about
        :data:`BATCH_BYTES` bytes.
    """
    groups: list[list[LogEvent]] = []
    current: list[LogEvent] = []
    size = 0
    for event in events:
        weight = len(event.message) + 200
        if current and (len(current) >= BATCH_LINES or size + weight > BATCH_BYTES):
            groups.append(current)
            current, size = [], 0
        current.append(event)
        size += weight
    if current:
        groups.append(current)
    return groups


class Transport:
    """A destination the shipper sends batches to."""

    def send(self, events: Sequence[LogEvent]) -> None:
        """
        Deliver a batch.

        Args:
            events: The events, in order, at most :data:`BATCH_LINES`.

        Raises:
            DeliveryError: The destination did not accept it.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release a connection held open."""


# -- HTTP ------------------------------------------------------------------------


def _auth_header(auth: str | None, username: str | None, secret: str | None) -> dict[str, str]:
    """
    Build the ``Authorization`` header a destination asks for.

    Args:
        auth: ``bearer``, ``basic`` or None for none.
        username: The user, for basic.
        secret: The token or password.

    Returns:
        The header, or an empty mapping.
    """
    if not secret or auth in (None, "none"):
        return {}
    if auth == "basic":
        pair = base64.b64encode(f"{username or ''}:{secret}".encode()).decode("ascii")
        return {"Authorization": f"Basic {pair}"}
    return {"Authorization": f"Bearer {secret}"}


class HttpTransport(Transport):
    """Shared by the destinations that take a JSON body over HTTP."""

    def __init__(self, url: str, headers: Mapping[str, str], *, opener: Any | None = None) -> None:
        """
        Args:
            url: Where the body is posted.
            headers: Request headers; ``Content-Type`` is JSON unless given.
            opener: Opens a request; ``urllib.request.urlopen`` by default.
        """
        self.url = url
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "noust-logship",
            **headers,
        }
        self._open = opener or urllib.request.urlopen

    def body(self, events: Sequence[LogEvent]) -> Any:
        """
        Build the JSON document for a batch.

        Args:
            events: The batch.

        Returns:
            What is posted, before it is encoded.
        """
        raise NotImplementedError

    def send(self, events: Sequence[LogEvent]) -> None:
        payload = json.dumps(self.body(events), separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=payload, headers=self.headers, method="POST"
        )
        try:
            with self._open(request, timeout=HTTP_TIMEOUT) as response:
                status = response.status
                answer = response.read(ANSWER_CHARS).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            said = exc.read(ANSWER_CHARS).decode("utf-8", errors="replace").strip()
            raise DeliveryError(
                f"{self.where()} answered HTTP {exc.code}", details=said or exc.reason
            ) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DeliveryError(f"{self.where()} could not be reached", details=str(exc)) from exc
        if not 200 <= status < 300:
            raise DeliveryError(f"{self.where()} answered HTTP {status}", details=answer.strip())

    def where(self) -> str:
        """
        Returns:
            The host the batch goes to, for an error.
        """
        return urlsplit(self.url).netloc or self.url


class LokiTransport(HttpTransport):
    """Grafana Loki's push API."""

    def __init__(
        self,
        url: str,
        *,
        tenant: str | None,
        labels: Mapping[str, str],
        auth: str | None,
        username: str | None,
        secret: str | None,
        opener: Any | None = None,
    ) -> None:
        headers = _auth_header(auth, username, secret)
        if tenant:
            headers["X-Scope-OrgID"] = tenant
        endpoint = url.rstrip("/")
        if not endpoint.endswith("/loki/api/v1/push"):
            endpoint += "/loki/api/v1/push"
        super().__init__(endpoint, headers, opener=opener)
        self.labels = dict(labels)

    def body(self, events: Sequence[LogEvent]) -> Any:
        streams: dict[tuple[str, str, str], list[list[str]]] = {}
        for event in events:
            key = (event.app, event.process, event.host)
            streams.setdefault(key, []).append([str(event.ts_ns), fit(event.message)])
        return {
            "streams": [
                {
                    "stream": {
                        "job": "noust",
                        "app": app,
                        "process": process,
                        "host": host,
                        **self.labels,
                    },
                    "values": values,
                }
                for (app, process, host), values in streams.items()
            ]
        }


class AxiomTransport(HttpTransport):
    """Axiom's ingest API."""

    def __init__(
        self,
        url: str,
        *,
        dataset: str,
        secret: str | None,
        labels: Mapping[str, str],
        opener: Any | None = None,
    ) -> None:
        super().__init__(
            f"{url.rstrip('/')}/v1/datasets/{dataset}/ingest",
            _auth_header("bearer", None, secret),
            opener=opener,
        )
        self.labels = dict(labels)

    def body(self, events: Sequence[LogEvent]) -> Any:
        return [
            {
                "_time": event.iso,
                "message": fit(event.message),
                "level": event.level,
                "app": event.app,
                "process": event.process,
                "unit": event.unit,
                "host": event.host,
                **self.labels,
            }
            for event in events
        ]


#: New Relic's Log API, by the region the account lives in.
NEW_RELIC_ENDPOINTS = {
    "us": "https://log-api.newrelic.com/log/v1",
    "eu": "https://log-api.eu.newrelic.com/log/v1",
}

#: The longest message New Relic keeps whole.
NEW_RELIC_MAX_MESSAGE = 4000


class NewRelicTransport(HttpTransport):
    """New Relic's Log API: a license key in ``Api-Key``, one block of common attributes."""

    def __init__(
        self,
        *,
        region: str,
        secret: str | None,
        labels: Mapping[str, str],
        url: str | None = None,
        opener: Any | None = None,
    ) -> None:
        """
        Args:
            region: ``us`` or ``eu``: where the account lives.
            secret: The license key.
            labels: Attributes added to every line.
            url: An endpoint of its own (a regional or government one), instead of the region's.
            opener: Opens a request; ``urllib.request.urlopen`` by default.
        """
        super().__init__(
            url or NEW_RELIC_ENDPOINTS.get(region, NEW_RELIC_ENDPOINTS["us"]),
            {"Api-Key": secret} if secret else {},
            opener=opener,
        )
        self.labels = dict(labels)

    def body(self, events: Sequence[LogEvent]) -> Any:
        host = events[0].host if events else ""
        return [
            {
                "common": {"attributes": {"service": "noust", "host": host, **self.labels}},
                "logs": [
                    {
                        "timestamp": event.ts_ns // 1_000_000,
                        "message": fit(event.message)[:NEW_RELIC_MAX_MESSAGE],
                        "attributes": {
                            "app": event.app,
                            "process": event.process,
                            "unit": event.unit,
                            "level": event.level,
                        },
                    }
                    for event in events
                ],
            }
        ]


class JsonTransport(HttpTransport):
    """Any endpoint that takes a JSON array of events: Fluent Bit's http input, Vector, Logstash."""

    def __init__(
        self,
        url: str,
        *,
        auth: str | None,
        username: str | None,
        secret: str | None,
        labels: Mapping[str, str],
        opener: Any | None = None,
    ) -> None:
        super().__init__(url, _auth_header(auth, username, secret), opener=opener)
        self.labels = dict(labels)

    def body(self, events: Sequence[LogEvent]) -> Any:
        return [
            {
                "date": event.ts_ns / 1e9,
                "time": event.iso,
                "log": fit(event.message),
                "level": event.level,
                "app": event.app,
                "process": event.process,
                "unit": event.unit,
                "host": event.host,
                **self.labels,
            }
            for event in events
        ]


def _attribute(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


class OtlpTransport(HttpTransport):
    """An OpenTelemetry collector's OTLP/HTTP logs endpoint, as JSON."""

    def __init__(
        self,
        url: str,
        *,
        auth: str | None,
        username: str | None,
        secret: str | None,
        labels: Mapping[str, str],
        opener: Any | None = None,
    ) -> None:
        endpoint = url.rstrip("/")
        if not endpoint.endswith("/v1/logs"):
            endpoint += "/v1/logs"
        super().__init__(endpoint, _auth_header(auth, username, secret), opener=opener)
        self.labels = dict(labels)

    def body(self, events: Sequence[LogEvent]) -> Any:
        by_app: dict[tuple[str, str], list[LogEvent]] = {}
        for event in events:
            by_app.setdefault((event.app, event.host), []).append(event)
        return {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            _attribute("service.name", app),
                            _attribute("host.name", host),
                            *(_attribute(key, value) for key, value in self.labels.items()),
                        ]
                    },
                    "scopeLogs": [
                        {
                            "scope": {"name": "noust"},
                            "logRecords": [
                                {
                                    "timeUnixNano": str(event.ts_ns),
                                    "severityNumber": _OTLP_SEVERITY.get(event.priority, 9),
                                    "severityText": event.level.upper(),
                                    "body": {"stringValue": fit(event.message)},
                                    "attributes": [
                                        _attribute("process", event.process),
                                        _attribute("unit", event.unit),
                                    ],
                                }
                                for event in group
                            ],
                        }
                    ],
                }
                for (app, host), group in by_app.items()
            ]
        }


#: OpenTelemetry's severity numbers for syslog's.
_OTLP_SEVERITY = {0: 24, 1: 23, 2: 21, 3: 17, 4: 13, 5: 10, 6: 9, 7: 5}


# -- syslog ------------------------------------------------------------------------


def format_app_line(event: LogEvent, *, facility: int, hostname: str) -> bytes:
    """
    Render one application line as an RFC 5424 message.

    Args:
        event: The line.
        facility: Syslog facility.
        hostname: HOSTNAME.

    Returns:
        The message, UTF-8, without framing. The application is the APP-NAME, so a
        receiver filters on it; the process and unit travel as structured data.
    """

    def printable(value: str, limit: int) -> str:
        text = "".join(char for char in value if 33 <= ord(char) <= 126)
        return text[:limit] or "-"

    def sd(value: str) -> str:
        cleaned = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in value)
        return cleaned.replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")

    header = " ".join(
        [
            f"<{facility * 8 + max(0, min(7, event.priority))}>1",
            event.iso,
            printable(hostname, 255),
            printable(event.app, 48),
            "-",
            "-",
        ]
    )
    structured = f'[noust@32473 process="{sd(event.process)}" unit="{sd(event.unit)}"]'
    text = "".join(" " if char in "\r\n" else char for char in fit(event.message))
    return f"{header} {structured} {text}".encode()


class _LineSink(SyslogSink):
    """The audit trail's connection to a receiver, carrying application lines instead of events."""

    def deliver(self, message: bytes, *, stream: bool) -> None:
        """
        Put one message on the connection, opening it first.

        Args:
            message: An RFC 5424 message.
            stream: Whether the transport frames messages (TCP, TLS).

        Raises:
            OSError: It could not be delivered; the connection is dropped.
        """
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


class SyslogTransport(Transport):
    """A remote RFC 5424 receiver, over UDP, TCP or TLS."""

    def __init__(self, destination: SyslogDestination, *, hostname: str | None = None) -> None:
        """
        Args:
            destination: The receiver and how to trust it.
            hostname: HOSTNAME of every message; this server's by default.
        """
        self._sink = _LineSink(destination, enterprise_id=32473, hostname=hostname)
        self.destination = destination
        self.hostname = hostname or socket.gethostname()

    def send(self, events: Sequence[LogEvent]) -> None:
        stream = self.destination.transport in ("tcp", "tls")
        try:
            for event in events:
                message = format_app_line(
                    event, facility=self.destination.facility, hostname=self.hostname
                )
                if not stream:
                    message = message[:MAX_UDP_BYTES]
                self._sink.deliver(message, stream=stream)
        except OSError as exc:
            raise DeliveryError(
                f"{self.destination.host or self.destination.path} could not be reached",
                details=str(exc),
            ) from exc

    def close(self) -> None:
        self._sink.close()
