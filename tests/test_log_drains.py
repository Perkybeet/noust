# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the logs of applications are shipped to (checklist B-73): the destinations.

Pinned here:

- what each kind of destination may be told, and that a refusal names the field;
- that a token lives in the secret store and never in a column, the audit trail or an
  error, and leaves in one header;
- the body each kind of destination takes: Loki's streams, Axiom's events, a JSON
  array, OTLP's log records and an RFC 5424 line;
- that a destination that does not accept a batch says what it answered.
"""

from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import audit
from noust.core.audit.settings import SyslogDestination
from noust.core.exceptions import ValidationError
from noust.core.log_drain_store import Drain, secret_name
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore
from noust.managers import log_drains
from noust.managers.log_drains import LogDrains, build_transport, check_url, validate_settings
from noust.managers.log_transports import (
    AxiomTransport,
    DeliveryError,
    JsonTransport,
    LogEvent,
    LokiTransport,
    OtlpTransport,
    SyslogTransport,
    batches,
    fit,
    format_app_line,
)

TOKEN = "glc_eyJrIjoiN2xkc2Zhc2RmIn0"


def event(message: str = "hello", **fields: Any) -> LogEvent:
    values: dict[str, Any] = {
        "ts_ns": 1_790_000_000_123_456_789,
        "message": message,
        "app": "shop.example.com",
        "process": "web",
        "unit": "shop-example-com.service",
        "priority": 6,
        "host": "web-1",
    }
    values.update(fields)
    return LogEvent(**values)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop"))
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def secrets(tmp_path: Path) -> SecretStore:
    return SecretStore(root=tmp_path / "secrets")


@pytest.fixture
def drains(store: NoustStore, secrets: SecretStore) -> LogDrains:
    return LogDrains(store=store, secrets=secrets)


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://logs.example.com",
        "https://logs.example.com/loki",
        "http://127.0.0.1:3100",
        "http://localhost:3100/",
    ],
)
def test_an_http_or_https_address_is_accepted(url: str) -> None:
    assert check_url(url).startswith("http")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "logs.example.com",
        "ftp://logs.example.com",
        "https://user:pass@logs.example.com",
        "https://logs.example.com/?token=abc",
        "https://logs.example.com/#frag",
        "https://",
        "https://logs.example.com/\n",
    ],
)
def test_what_is_not_an_address_or_carries_credentials_is_refused(url: str) -> None:
    with pytest.raises(ValidationError) as caught:
        check_url(url)
    assert caught.value.field == "url"


def test_plaintext_to_another_machine_is_refused_where_the_control_is_on(
    real_config: Any,
) -> None:
    real_config.set("security.log_shipping_tls", True)

    with pytest.raises(ValidationError, match="only over TLS"):
        check_url("http://logs.example.com")
    assert check_url("http://127.0.0.1:3100")
    assert check_url("https://logs.example.com")


# ---------------------------------------------------------------------------
# What each kind is told
# ---------------------------------------------------------------------------


def test_loki_takes_its_address_tenant_and_labels() -> None:
    cleaned = validate_settings(
        "loki",
        {"url": "https://logs.example.com", "tenant": "acme", "labels": {"env": "prod"}},
        has_secret=True,
    )

    assert cleaned == {
        "url": "https://logs.example.com",
        "tenant": "acme",
        "labels": {"env": "prod"},
    }


def test_axiom_needs_its_dataset_and_its_token() -> None:
    assert validate_settings("axiom", {"dataset": "app-logs"}, has_secret=True) == {
        "url": "https://api.axiom.co",
        "dataset": "app-logs",
    }
    with pytest.raises(ValidationError) as no_dataset:
        validate_settings("axiom", {}, has_secret=True)
    with pytest.raises(ValidationError) as no_token:
        validate_settings("axiom", {"dataset": "x"}, has_secret=False)
    assert no_dataset.value.field == "dataset" and no_token.value.field == "secret"


def test_a_json_endpoint_and_otlp_choose_how_to_authenticate() -> None:
    assert (
        validate_settings("http", {"url": "https://x.example.com"}, has_secret=True)["auth"]
        == "bearer"
    )
    assert (
        validate_settings(
            "otlp", {"url": "https://x.example.com", "username": "ana"}, has_secret=True
        )["auth"]
        == "basic"
    )
    with pytest.raises(ValidationError) as caught:
        validate_settings(
            "http", {"url": "https://x.example.com", "auth": "basic"}, has_secret=True
        )
    assert caught.value.field == "username"
    with pytest.raises(ValidationError) as odd:
        validate_settings(
            "http", {"url": "https://x.example.com", "auth": "magic"}, has_secret=True
        )
    assert odd.value.field == "auth"


def test_syslog_defaults_to_tls_and_its_port() -> None:
    cleaned = validate_settings("syslog", {"host": "logs.example.com"}, has_secret=False)

    assert (cleaned["transport"], cleaned["port"], cleaned["facility"]) == ("tls", 6514, 16)
    assert (
        validate_settings(
            "syslog", {"host": "logs.example.com", "transport": "udp"}, has_secret=False
        )["port"]
        == 514
    )


@pytest.mark.parametrize(
    ("settings", "field"),
    [
        ({}, "host"),
        ({"host": "a b"}, "host"),
        ({"host": "h", "transport": "carrier-pigeon"}, "transport"),
        ({"host": "h", "port": 70000}, "port"),
        ({"host": "h", "facility": 99}, "facility"),
        ({"host": "h", "pin_sha256": "abc"}, "pin_sha256"),
        ({"host": "h", "ca": "relative.pem"}, "ca"),
        ({"host": "h", "client_cert": "/nonexistent/cert.pem"}, "client_cert"),
    ],
)
def test_a_syslog_setting_a_receiver_cannot_have_names_the_field(
    settings: dict[str, Any], field: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        validate_settings("syslog", settings, has_secret=False)
    assert caught.value.field == field


def test_a_client_certificate_needs_its_key(tmp_path: Path) -> None:
    cert = tmp_path / "c.pem"
    cert.write_text("x")

    with pytest.raises(ValidationError, match="needs its key"):
        validate_settings("syslog", {"host": "h", "client_cert": str(cert)}, has_secret=False)


def test_a_setting_a_kind_does_not_take_is_refused_by_name() -> None:
    with pytest.raises(ValidationError) as caught:
        validate_settings("loki", {"url": "https://x.example.com", "dataset": "x"}, has_secret=True)
    assert caught.value.field == "dataset"


@pytest.mark.parametrize(
    "labels",
    [
        {"job": "x"},
        {"app": "x"},
        {"1bad": "x"},
        {"ok": "a" * 101},
        {"ok": "a\nb"},
        {f"l{n}": "v" for n in range(11)},
    ],
)
def test_labels_noust_owns_or_a_destination_would_refuse_are_refused(
    labels: dict[str, str],
) -> None:
    with pytest.raises(ValidationError) as caught:
        validate_settings(
            "loki", {"url": "https://x.example.com", "labels": labels}, has_secret=True
        )
    assert caught.value.field == "labels"


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        validate_settings("splunk", {}, has_secret=False)
    assert caught.value.field == "kind"


@pytest.mark.parametrize("name", ["Loki", "a b", "", "1x", "-x", "x-", "x" * 41])
def test_a_name_that_is_not_a_short_label_is_refused(drains: LogDrains, name: str) -> None:
    with pytest.raises(ValidationError) as caught:
        drains.save(name, "http", {"url": "https://x.example.com"})
    assert caught.value.field == "name"


# ---------------------------------------------------------------------------
# The destinations and their tokens
# ---------------------------------------------------------------------------


def test_a_destination_is_saved_with_its_token_in_the_secret_store(
    drains: LogDrains, secrets: SecretStore, store: NoustStore
) -> None:
    saved = drains.save(
        "grafana",
        "loki",
        {"url": "https://logs.example.com", "tenant": "acme"},
        secret=TOKEN,
        apps=["shop.example.com"],
        actor="ana",
    )

    assert saved.apps == ("shop.example.com",)
    assert secrets.read(secret_name("grafana")) == TOKEN
    assert drains.has_secret("grafana")
    with store._transaction() as cursor:
        cursor.execute("SELECT * FROM log_drains")
        assert TOKEN not in str(cursor.fetchall())


def test_the_token_is_nowhere_the_audit_trail_can_say(drains: LogDrains) -> None:
    drains.save("grafana", "loki", {"url": "https://logs.example.com"}, secret=TOKEN, actor="ana")

    for event_ in audit.get_log().read(action="logs.drain"):
        assert TOKEN not in json.dumps(event_)
    [recorded] = audit.get_log().read(action="logs.drain")
    assert recorded["details"]["authenticated"] is True and recorded["details"]["by"] == "ana"


def test_editing_without_a_token_keeps_the_one_it_has(
    drains: LogDrains, secrets: SecretStore
) -> None:
    drains.save("grafana", "loki", {"url": "https://logs.example.com"}, secret=TOKEN)

    drains.save(
        "grafana", "loki", {"url": "https://logs2.example.com"}, keep_secret=True, enabled=False
    )

    assert secrets.read(secret_name("grafana")) == TOKEN
    changed = drains.get("grafana")
    assert changed.settings["url"] == "https://logs2.example.com" and not changed.enabled


def test_saving_without_asking_to_keep_the_token_forgets_it(
    drains: LogDrains, secrets: SecretStore
) -> None:
    drains.save("grafana", "loki", {"url": "https://logs.example.com"}, secret=TOKEN)

    drains.save("grafana", "loki", {"url": "https://logs.example.com"})

    assert secrets.read(secret_name("grafana")) is None


def test_a_destination_cannot_change_kind(drains: LogDrains) -> None:
    drains.save("grafana", "loki", {"url": "https://logs.example.com"})

    with pytest.raises(ValidationError, match="remove it"):
        drains.save("grafana", "http", {"url": "https://x.example.com"})


def test_removing_forgets_the_destination_and_its_token(
    drains: LogDrains, secrets: SecretStore
) -> None:
    drains.save("grafana", "loki", {"url": "https://logs.example.com"}, secret=TOKEN)

    drains.remove("grafana", actor="ana")

    assert drains.list_drains() == []
    assert secrets.read(secret_name("grafana")) is None
    with pytest.raises(ValidationError, match="no destination"):
        drains.get("grafana")


def test_a_destination_can_be_turned_off_and_on(drains: LogDrains) -> None:
    drains.save("grafana", "loki", {"url": "https://logs.example.com"})

    assert drains.set_enabled("grafana", False).enabled is False
    assert drains.get("grafana").enabled is False
    drains.set_enabled("grafana", True)
    assert drains.get("grafana").enabled is True


def test_a_destination_is_narrowed_only_to_applications_of_this_server(drains: LogDrains) -> None:
    with pytest.raises(ValidationError, match="not deployed here") as caught:
        drains.save(
            "grafana", "loki", {"url": "https://logs.example.com"}, apps=["ghost.example.com"]
        )
    assert caught.value.field == "apps"


def test_a_redeploy_does_not_touch_the_destinations(drains: LogDrains, store: NoustStore) -> None:
    drains.save("grafana", "loki", {"url": "https://logs.example.com"})
    app = store.get_app("shop.example.com")
    assert app is not None
    app.status = "running"

    store.update_app(app)

    assert [d.name for d in drains.list_drains()] == ["grafana"]


def test_what_the_shipper_recorded_survives_the_destination_being_edited(
    drains: LogDrains, store: NoustStore
) -> None:
    from noust.core import log_drain_store

    drains.save("grafana", "loki", {"url": "https://logs.example.com"})
    log_drain_store.record_delivery("grafana", {"journal": "s=abc"}, 12, store=store)

    drains.save("grafana", "loki", {"url": "https://logs2.example.com"}, keep_secret=True)

    kept = drains.get("grafana")
    assert kept.cursors == {"journal": "s=abc"} and kept.shipped == 12


# ---------------------------------------------------------------------------
# The test line
# ---------------------------------------------------------------------------


class Opener:
    """Answers the requests a transport makes."""

    def __init__(
        self, status: int = 204, body: bytes = b"", error: Exception | None = None
    ) -> None:
        self.status = status
        self.body = body
        self.error = error
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        outer = self

        class Response:
            status = outer.status

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self, count: int = -1) -> bytes:
                return outer.body[:count]

        return Response()


def test_the_test_line_goes_through_the_transport_and_says_what_was_sent(
    drains: LogDrains, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[list[LogEvent]] = []

    class Fake:
        def send(self, events: list[LogEvent]) -> None:
            sent.append(events)

        def close(self) -> None:
            pass

    drains.save("grafana", "loki", {"url": "https://logs.example.com"}, secret=TOKEN)
    monkeypatch.setattr(log_drains, "build_transport", lambda drain, secret: Fake())

    said = drains.test("grafana", actor="ana")

    assert said.startswith("Noust log drain test from ")
    assert sent[0][0].message == said and sent[0][0].app == "noust-test"


def test_a_destination_that_refuses_the_test_line_says_what_it_answered_not_the_token(
    drains: LogDrains, monkeypatch: pytest.MonkeyPatch
) -> None:
    drains.save("grafana", "loki", {"url": "https://logs.example.com"}, secret=TOKEN)
    opener = Opener(
        error=urllib.error.HTTPError(
            "https://logs.example.com", 401, "Unauthorized", {}, io.BytesIO(b"invalid token")
        )
    )  # type: ignore[arg-type]
    real = log_drains.build_transport

    def build(drain: Drain, secret: str | None) -> Any:
        transport = real(drain, secret)
        transport._open = opener  # type: ignore[attr-defined]
        return transport

    monkeypatch.setattr(log_drains, "build_transport", build)

    with pytest.raises(DeliveryError) as caught:
        drains.test("grafana")

    assert "HTTP 401" in caught.value.message and "invalid token" in str(caught.value.details)
    assert TOKEN not in str(caught.value) and TOKEN not in str(caught.value.details)


# ---------------------------------------------------------------------------
# What each destination is sent
# ---------------------------------------------------------------------------


def posted(opener: Opener) -> Any:
    return json.loads(opener.requests[0].data)


def test_loki_gets_one_stream_per_application_and_process_with_nanosecond_timestamps() -> None:
    opener = Opener()
    loki = LokiTransport(
        "https://logs.example.com",
        tenant="acme",
        labels={"env": "prod"},
        auth="bearer",
        username=None,
        secret=TOKEN,
        opener=opener,
    )

    loki.send([event("one"), event("two"), event("three", process="worker")])

    request = opener.requests[0]
    assert request.full_url == "https://logs.example.com/loki/api/v1/push"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-scope-orgid"] == "acme"
    streams = posted(opener)["streams"]
    assert len(streams) == 2
    web = next(s for s in streams if s["stream"]["process"] == "web")
    assert web["stream"] == {
        "job": "noust",
        "app": "shop.example.com",
        "process": "web",
        "host": "web-1",
        "env": "prod",
    }
    assert web["values"] == [["1790000000123456789", "one"], ["1790000000123456789", "two"]]


def test_loki_with_a_user_authenticates_with_basic() -> None:
    opener = Opener()
    LokiTransport(
        "https://logs.example.com/loki/api/v1/push",
        tenant=None,
        labels={},
        auth="basic",
        username="12345",
        secret=TOKEN,
        opener=opener,
    ).send([event()])

    assert opener.requests[0].full_url == "https://logs.example.com/loki/api/v1/push"
    assert opener.requests[0].headers["Authorization"].startswith("Basic ")


def test_axiom_gets_events_with_the_dataset_in_the_address() -> None:
    opener = Opener()

    AxiomTransport(
        "https://api.eu.axiom.co",
        dataset="app-logs",
        secret=TOKEN,
        labels={"env": "prod"},
        opener=opener,
    ).send([event("boom", priority=3)])

    assert opener.requests[0].full_url == "https://api.eu.axiom.co/v1/datasets/app-logs/ingest"
    assert opener.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    [body] = posted(opener)
    assert body["message"] == "boom" and body["level"] == "error"
    assert body["_time"] == "2026-09-21T14:13:20.123Z"
    assert (body["app"], body["process"], body["env"]) == ("shop.example.com", "web", "prod")


def test_a_json_endpoint_gets_an_array_fluent_bit_reads() -> None:
    opener = Opener(status=201)

    JsonTransport(
        "http://127.0.0.1:9880/noust",
        auth="none",
        username=None,
        secret=None,
        labels={},
        opener=opener,
    ).send([event("hello")])

    [body] = posted(opener)
    assert body["log"] == "hello" and isinstance(body["date"], float)
    assert "Authorization" not in opener.requests[0].headers


def test_otlp_gets_log_records_grouped_by_application() -> None:
    opener = Opener()

    OtlpTransport(
        "https://otel.example.com",
        auth="bearer",
        username=None,
        secret=TOKEN,
        labels={"env": "prod"},
        opener=opener,
    ).send([event("hello", priority=4)])

    assert opener.requests[0].full_url == "https://otel.example.com/v1/logs"
    [resource] = posted(opener)["resourceLogs"]
    attributes = {a["key"]: a["value"]["stringValue"] for a in resource["resource"]["attributes"]}
    assert attributes == {"service.name": "shop.example.com", "host.name": "web-1", "env": "prod"}
    [record] = resource["scopeLogs"][0]["logRecords"]
    assert record["body"] == {"stringValue": "hello"}
    assert (record["severityNumber"], record["severityText"]) == (13, "WARNING")
    assert record["timeUnixNano"] == "1790000000123456789"


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_a_destination_that_answers_an_error_says_what_it_answered(status: int) -> None:
    opener = Opener(
        error=urllib.error.HTTPError(
            "u", status, "Bad", {}, io.BytesIO(b"rate limited by the tenant")
        )  # type: ignore[arg-type]
    )
    transport = JsonTransport(
        "https://x.example.com", auth="none", username=None, secret=None, labels={}, opener=opener
    )

    with pytest.raises(DeliveryError) as caught:
        transport.send([event()])

    assert f"HTTP {status}" in caught.value.message
    assert "rate limited" in str(caught.value.details)


def test_a_destination_that_cannot_be_reached_says_why() -> None:
    opener = Opener(error=urllib.error.URLError("connection refused"))
    transport = JsonTransport(
        "https://x.example.com", auth="none", username=None, secret=None, labels={}, opener=opener
    )

    with pytest.raises(DeliveryError, match="could not be reached") as caught:
        transport.send([event()])

    assert "connection refused" in str(caught.value.details)


def test_a_status_the_destination_does_not_call_success_is_not_accepted() -> None:
    transport = JsonTransport(
        "https://x.example.com",
        auth="none",
        username=None,
        secret=None,
        labels={},
        opener=Opener(status=302, body=b"moved"),
    )

    with pytest.raises(DeliveryError, match="HTTP 302"):
        transport.send([event()])


def test_a_line_longer_than_a_destination_takes_is_cut_and_says_so() -> None:
    cut = fit("x" * 20000)

    assert len(cut) < 20000 and cut.endswith("[cut by noust]")
    assert fit("short") == "short"


def test_a_big_batch_is_split_into_requests_a_destination_takes() -> None:
    many = [event(f"line {n}") for n in range(1200)]
    groups = batches(many)

    assert [len(g) for g in groups] == [500, 500, 200]
    assert [e.message for g in groups for e in g] == [e.message for e in many]


# ---------------------------------------------------------------------------
# Remote syslog
# ---------------------------------------------------------------------------


def test_a_line_is_an_rfc_5424_message_with_the_application_as_its_name() -> None:
    message = format_app_line(event("a\nb"), facility=16, hostname="web-1").decode()

    assert message.startswith("<134>1 2026-09-21T14:13:20.123Z web-1 shop.example.com - - ")
    assert '[noust@32473 process="web" unit="shop-example-com.service"]' in message
    assert message.endswith("a b"), "a newline cannot forge a second record"


def test_an_error_line_has_the_error_severity() -> None:
    message = format_app_line(event("boom", priority=3), facility=16, hostname="h").decode()

    assert message.startswith("<131>1 ")


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        pass


def test_syslog_over_tcp_frames_each_message_by_its_length(monkeypatch: pytest.MonkeyPatch) -> None:
    from noust.managers import log_transports

    sock = FakeSocket()
    monkeypatch.setattr(log_transports.SyslogSink, "_connect", lambda self: sock)
    transport = SyslogTransport(
        SyslogDestination(transport="tcp", host="h", port=514), hostname="web-1"
    )

    transport.send([event("one"), event("two")])

    assert len(sock.sent) == 2
    length, _, rest = sock.sent[0].partition(b" ")
    assert int(length) == len(rest) and rest.endswith(b"one")


def test_syslog_over_udp_sends_the_bare_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from noust.managers import log_transports

    sock = FakeSocket()
    monkeypatch.setattr(log_transports.SyslogSink, "_connect", lambda self: sock)

    SyslogTransport(
        SyslogDestination(transport="udp", host="h", port=514, facility=16), hostname="web-1"
    ).send([event("one")])

    assert sock.sent[0].startswith(b"<134>1 ") and sock.sent[0].endswith(b"one")


def test_a_receiver_that_is_down_is_a_delivery_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from noust.managers import log_transports

    def refuse(self: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(log_transports.SyslogSink, "_connect", refuse)
    transport = SyslogTransport(
        SyslogDestination(transport="tcp", host="logs.example.com", port=514)
    )

    with pytest.raises(DeliveryError, match=r"logs\.example\.com could not be reached") as caught:
        transport.send([event()])

    assert "connection refused" in str(caught.value.details)


def test_each_kind_builds_its_transport() -> None:
    expectations = {
        "loki": ({"url": "https://x.example.com"}, LokiTransport),
        "axiom": ({"url": "https://api.axiom.co", "dataset": "d"}, AxiomTransport),
        "http": ({"url": "https://x.example.com", "auth": "bearer"}, JsonTransport),
        "otlp": ({"url": "https://x.example.com", "auth": "bearer"}, OtlpTransport),
        "syslog": ({"transport": "udp", "host": "h", "port": 514, "facility": 16}, SyslogTransport),
    }
    for kind, (settings, expected) in expectations.items():
        assert isinstance(
            build_transport(Drain(name="x", kind=kind, settings=settings), TOKEN), expected
        )
