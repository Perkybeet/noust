# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The health check beyond the path: what 3.4 lets an application say (checklist B-12).

Pinned here:

- each value is validated once, where it is stored, and a bad one never reaches
  the row;
- a request check can name the method, the scheme, the Host header and the
  port; the connection is always to 127.0.0.1;
- a command check runs as the application and is up when it exits 0;
- the interval, the most probes and the start period shape the wait, and
  without them the gate waits exactly as before;
- the settings live in a table of their own, so a redeploy's full-row write of
  the application cannot put back what an operator changed.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest

from noust.core import app_health_store
from noust.core.app_health_store import HealthExtras
from noust.core.exceptions import DeploymentError, ValidationError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.deployers import lifecycle
from noust.deployers.helpers import health as health_module
from noust.deployers.helpers.health import wait_until_command_succeeds, wait_until_healthy
from noust.deployers.helpers.health_gate import (
    HEALTH_GATE_ATTEMPTS,
    HEALTH_GATE_DELAY,
    HealthCheck,
    HealthGate,
)
from noust.validators.health import (
    check_health_command,
    check_health_host,
    check_health_interval,
    check_health_kind,
    check_health_method,
    check_health_port,
    check_health_retries,
    check_health_scheme,
    check_health_start_period,
)

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A store of the test's own, where the lifecycle looks for it."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(lifecycle, "get_store", lambda: instance)
    yield instance
    NoustStore.reset_instance()


def _register(store: NoustStore, **fields: Any) -> App:
    values: dict[str, Any] = {
        "domain": DOMAIN,
        "app_type": "nodejs",
        "port": 3100,
        "app_path": "/srv/shop",
    }
    values.update(fields)
    return store.create_app(App(**values))


class FakeUnits:
    """The part of the service manager the gate uses."""

    def __init__(self) -> None:
        self.restarts: list[str] = []

    def restart(self, name: str) -> None:
        self.restarts.append(name)

    def logs(self, name: str, lines: int = 50) -> str:
        return "journal line"


class RecordingProbe:
    """Stands in for wait_until_healthy and keeps what it was asked."""

    def __init__(self, result: bool = True) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result = result

    def __call__(self, url: str, **kwargs: Any) -> bool:
        self.calls.append((url, kwargs))
        return self.result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("given", "kept"), [("get", "GET"), ("HEAD", "HEAD"), ("Post", "POST")])
def test_a_method_is_kept_in_capitals(given: str, kept: str) -> None:
    assert check_health_method(given) == kept


@pytest.mark.parametrize("method", ["DELETE", "PUT", "", "GET /", None])
def test_a_method_that_changes_things_is_refused(method: Any) -> None:
    with pytest.raises(ValidationError):
        check_health_method(method)


def test_the_scheme_is_http_or_https() -> None:
    assert check_health_scheme("HTTPS") == "https"
    for scheme in ("ftp", "file", "", "http://"):
        with pytest.raises(ValidationError):
            check_health_scheme(scheme)


@pytest.mark.parametrize("host", ["shop.example.com", "Localhost", "a", "my-app.internal"])
def test_a_host_name_is_accepted(host: str) -> None:
    assert check_health_host(host) == host.lower()


@pytest.mark.parametrize(
    "host",
    [
        "",
        "http://shop.example.com",
        "shop.example.com/path",
        "shop example",
        "a:80",
        "-a.com",
        "a" * 300,
    ],
)
def test_something_that_is_not_a_host_name_is_refused(host: str) -> None:
    """A header injection, a URL or a port would not be a name the application answers to."""
    with pytest.raises(ValidationError):
        check_health_host(host)


@pytest.mark.parametrize("port", [1, 80, 3000, 65535])
def test_a_tcp_port_is_accepted(port: int) -> None:
    assert check_health_port(port) == port


@pytest.mark.parametrize("port", [0, -1, 65536, True, "80", 80.5])
def test_a_value_that_is_not_a_tcp_port_is_refused(port: Any) -> None:
    with pytest.raises(ValidationError):
        check_health_port(port)


def test_the_interval_the_retries_and_the_start_period_have_their_ranges() -> None:
    assert check_health_interval(1) == 1
    assert check_health_interval(60) == 60
    assert check_health_retries(1) == 1
    assert check_health_retries(100) == 100
    assert check_health_start_period(0) == 0
    assert check_health_start_period(600) == 600
    for check, value in (
        (check_health_interval, 0),
        (check_health_interval, 61),
        (check_health_retries, 0),
        (check_health_retries, 101),
        (check_health_start_period, -1),
        (check_health_start_period, 601),
        (check_health_interval, True),
    ):
        with pytest.raises(ValidationError):
            check(value)  # type: ignore[operator]


def test_the_kind_is_a_request_or_a_command() -> None:
    assert check_health_kind("http") == "http"
    assert check_health_kind("command") == "command"
    with pytest.raises(ValidationError):
        check_health_kind("tcp")


def test_a_command_is_a_program_and_its_arguments() -> None:
    assert check_health_command("  test -s /app/ready ") == "test -s /app/ready"
    assert shlex.split(check_health_command("php artisan 'health check'"))[-1] == "health check"


@pytest.mark.parametrize("command", ["", "   ", "echo 'unclosed", "a\nb", "x" * 2000, None])
def test_a_command_that_cannot_be_an_argument_vector_is_refused(command: Any) -> None:
    with pytest.raises(ValidationError):
        check_health_command(command)


# ---------------------------------------------------------------------------
# The extras, as a whole
# ---------------------------------------------------------------------------


def test_a_command_check_has_no_request_settings() -> None:
    with pytest.raises(ValidationError) as caught:
        app_health_store.validated(HealthExtras(kind="command", command="true", method="POST"))
    assert caught.value.field == "method"


def test_a_command_check_needs_a_command() -> None:
    with pytest.raises(ValidationError) as caught:
        app_health_store.validated(HealthExtras(kind="command"))
    assert caught.value.field == "command"


def test_a_request_check_has_no_command() -> None:
    with pytest.raises(ValidationError) as caught:
        app_health_store.validated(HealthExtras(command="true"))
    assert caught.value.field == "command"


def test_the_refusal_names_the_field() -> None:
    with pytest.raises(ValidationError) as caught:
        app_health_store.validated(HealthExtras(port=70000))
    assert caught.value.field == "port"


def test_a_stack_service_is_a_name_compose_accepts() -> None:
    ok = app_health_store.validated(
        HealthExtras(kind="command", command="pg_isready", service="db")
    )
    assert ok.service == "db"
    with pytest.raises(ValidationError):
        app_health_store.validated(
            HealthExtras(kind="command", command="pg_isready", service="db; rm")
        )


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def test_an_application_that_set_nothing_has_the_defaults(store: NoustStore) -> None:
    _register(store)
    assert app_health_store.get_extras(DOMAIN) == HealthExtras()
    assert app_health_store.get_extras(DOMAIN).is_default


def test_the_extras_are_written_and_read_back_normalised(store: NoustStore) -> None:
    _register(store)

    written = app_health_store.set_extras(
        DOMAIN,
        HealthExtras(method="post", scheme="HTTPS", host="Shop.Example.com", port=8443, interval=5),
        updated_by="ana",
    )

    assert written is True
    got = app_health_store.get_extras(DOMAIN)
    assert (got.method, got.scheme, got.host, got.port, got.interval) == (
        "POST",
        "https",
        "shop.example.com",
        8443,
        5,
    )


def test_a_bad_value_is_refused_and_writes_nothing(store: NoustStore) -> None:
    _register(store)
    app_health_store.set_extras(DOMAIN, HealthExtras(retries=3))

    with pytest.raises(ValidationError):
        app_health_store.set_extras(DOMAIN, HealthExtras(retries=3, interval=999))

    assert app_health_store.get_extras(DOMAIN) == HealthExtras(retries=3)


def test_setting_the_defaults_deletes_the_row(store: NoustStore) -> None:
    _register(store)
    app_health_store.set_extras(DOMAIN, HealthExtras(retries=3))

    app_health_store.set_extras(DOMAIN, HealthExtras())

    with store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) FROM app_health")
        assert cursor.fetchone()[0] == 0


def test_an_unknown_application_is_not_written(store: NoustStore) -> None:
    assert app_health_store.set_extras("nobody.example.com", HealthExtras(retries=3)) is False


def test_a_redeploy_keeps_the_extras(store: NoustStore) -> None:
    """The full-row write of ``apps`` is not where they live."""
    app = _register(store)
    app_health_store.set_extras(DOMAIN, HealthExtras(method="HEAD", start_period=20))

    app.status = "running"
    store.update_app(app)

    assert app_health_store.get_extras(DOMAIN).method == "HEAD"
    assert app_health_store.get_extras(DOMAIN).start_period == 20


def test_deleting_the_application_deletes_its_extras(store: NoustStore) -> None:
    _register(store)
    app_health_store.set_extras(DOMAIN, HealthExtras(retries=3))

    store.delete_app(DOMAIN)

    with store._transaction() as cursor:
        cursor.execute("SELECT COUNT(*) FROM app_health")
        assert cursor.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# What the check asks
# ---------------------------------------------------------------------------


def test_without_extras_the_check_is_exactly_what_it_was(store: NoustStore) -> None:
    check = HealthCheck.for_app(_register(store))

    assert check.url(3100) == "http://127.0.0.1:3100/"
    assert check.method == "GET"
    assert check.host is None
    assert check.attempts(HEALTH_GATE_DELAY) == HEALTH_GATE_ATTEMPTS
    assert check.delay == HEALTH_GATE_DELAY
    assert check.wall_clock == 30
    assert check.probe_options() == {}
    assert not check.is_command


def test_the_request_options_are_the_checks(store: NoustStore) -> None:
    app = _register(store, health_path="/healthz")
    app_health_store.set_extras(
        DOMAIN,
        HealthExtras(method="HEAD", scheme="https", host="shop.example.com", port=9443),
    )

    check = HealthCheck.for_app(app)

    assert check.url(3100) == "https://127.0.0.1:9443/healthz"
    assert check.probe_options() == {"method": "HEAD", "host": "shop.example.com"}
    assert check.describe() == "HEAD https://127.0.0.1:9443/healthz (Host: shop.example.com)"


def test_the_connection_is_to_127_0_0_1_whatever_the_host_says(store: NoustStore) -> None:
    app = _register(store)
    app_health_store.set_extras(DOMAIN, HealthExtras(host="example.org"))

    assert HealthCheck.for_app(app).url(3100).startswith("http://127.0.0.1:3100")


def test_retries_replace_the_derived_count_and_the_start_period_adds_probes() -> None:
    assert HealthCheck(retries=3).attempts(2.0) == 3
    # 20 s of grace at one probe every 2 s: ten probes that cannot count against the three.
    assert HealthCheck(retries=3, start_period=20).attempts(2.0) == 13
    assert HealthCheck(timeout=30, start_period=10).attempts(2.0) == 15 + 5
    assert HealthCheck(timeout=30, start_period=10).wall_clock == 40


def test_the_interval_is_the_pause_between_probes() -> None:
    gate = HealthGate(
        unit="shop",
        url="http://127.0.0.1:3100/",
        services=FakeUnits(),
        logger=Logger(),
        probe=RecordingProbe(),
        check=HealthCheck(interval=7),
    )
    assert gate._delay == 7.0


def test_an_explicit_delay_still_wins() -> None:
    gate = HealthGate(
        unit="shop",
        url="http://127.0.0.1:3100/",
        services=FakeUnits(),
        logger=Logger(),
        probe=RecordingProbe(),
        check=HealthCheck(interval=7),
        delay=0,
    )
    assert gate._delay == 0


def test_the_gate_hands_the_request_options_to_the_probe() -> None:
    probe = RecordingProbe()
    check = HealthCheck(method="POST", host="shop.example.com", retries=4, interval=3)
    gate = HealthGate(
        unit="shop",
        url=check.url(3100),
        services=FakeUnits(),
        logger=Logger(),
        probe=probe,
        check=check,
    )

    assert gate.restart_and_probe() == (True, "")

    _, kwargs = probe.calls[0]
    assert kwargs["method"] == "POST"
    assert kwargs["host"] == "shop.example.com"
    assert kwargs["retries"] == 4
    assert kwargs["delay"] == 3.0


def test_a_plain_check_calls_the_probe_as_it_always_was() -> None:
    """A probe written before the options existed must not meet a keyword it lacks."""
    probe = RecordingProbe()
    gate = HealthGate(
        unit="shop",
        url="http://127.0.0.1:3100/",
        services=FakeUnits(),
        logger=Logger(),
        probe=probe,
        check=HealthCheck(),
    )
    gate.restart_and_probe()

    _, kwargs = probe.calls[0]
    assert "method" not in kwargs
    assert "host" not in kwargs


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


class _Response:
    status = 204

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _CapturingOpener:
    def __init__(self) -> None:
        self.seen: list[Any] = []

    def open(self, target: Any, timeout: float) -> _Response:
        self.seen.append(target)
        return _Response()


def test_the_probe_sends_the_method_and_the_host_header(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _CapturingOpener()
    monkeypatch.setattr(health_module.urllib.request, "build_opener", lambda *handlers: opener)

    healthy = wait_until_healthy(
        "http://127.0.0.1:3100/healthz",
        retries=1,
        accept=lambda status: status == 204,
        method="HEAD",
        host="shop.example.com",
    )

    assert healthy
    request = opener.seen[0]
    assert request.get_method() == "HEAD"
    assert request.get_header("Host") == "shop.example.com"
    assert request.full_url == "http://127.0.0.1:3100/healthz"


def test_a_plain_get_is_still_a_plain_url(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _CapturingOpener()
    monkeypatch.setattr(health_module.urllib.request, "build_opener", lambda *handlers: opener)

    wait_until_healthy("http://127.0.0.1:3100/", retries=1, accept=lambda status: True)

    assert opener.seen == ["http://127.0.0.1:3100/"]


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_a_command_is_asked_until_it_exits_zero() -> None:
    answers = iter([(False, "exit 1"), (False, "exit 1"), (True, "")])
    seen: list[str] = []

    healthy = wait_until_command_succeeds(
        lambda timeout: next(answers),
        retries=5,
        delay=0,
        on_attempt=seen.append,
        sleep=lambda _s: None,
    )

    assert healthy
    assert seen == [
        "Health check attempt 1 failed: exit 1",
        "Health check attempt 2 failed: exit 1",
    ]


def test_a_command_that_never_succeeds_gives_up_at_the_attempts() -> None:
    calls = []

    def run(timeout: float) -> tuple[bool, str]:
        calls.append(timeout)
        return False, "no"

    assert not wait_until_command_succeeds(run, retries=3, delay=0, sleep=lambda _s: None)
    assert len(calls) == 3


def test_a_command_gets_more_time_than_a_request_but_never_more_than_is_left() -> None:
    class Clock:
        now = 0.0

        def __call__(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.now += seconds

    clock = Clock()
    timeouts: list[float] = []

    def run(timeout: float) -> tuple[bool, str]:
        timeouts.append(timeout)
        clock.now += timeout
        return False, "slow"

    wait_until_command_succeeds(
        run, retries=1000, delay=2.0, within=40, clock=clock, sleep=clock.sleep
    )

    assert timeouts[0] == 30
    assert all(0 < timeout <= 30 for timeout in timeouts)
    assert clock.now <= 40


def test_the_gate_runs_a_command_check_after_the_restart() -> None:
    units = FakeUnits()
    asked: list[float] = []

    def run(timeout: float) -> tuple[bool, str]:
        asked.append(timeout)
        return len(asked) >= 2, "exit 3"

    gate = HealthGate(
        unit="shop",
        url=None,
        services=units,
        logger=Logger(),
        check=HealthCheck(kind="command", command="test -s /app/ready", domain=DOMAIN, interval=1),
        command_run=run,
        delay=0,
    )

    assert gate.restart_and_probe() == (True, "")

    assert units.restarts == ["shop"]
    assert len(asked) == 2


def test_a_failed_command_check_carries_what_it_said_and_the_journal() -> None:
    gate = HealthGate(
        unit="shop",
        url=None,
        services=FakeUnits(),
        logger=Logger(),
        check=HealthCheck(kind="command", command="test -s /app/ready", domain=DOMAIN, retries=2),
        command_run=lambda timeout: (False, "shop.example.com: test exited with status 1"),
        delay=0,
    )

    healthy, evidence = gate.restart_and_probe()

    assert not healthy
    assert "did not pass the health check command" in evidence
    assert "test exited with status 1" in evidence
    assert "journal line" in evidence


def test_a_command_check_is_run_as_the_application(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.redact import Scrubber
    from noust.core.runner import FakeRunner
    from noust.deployers.helpers import health_command
    from noust.terminal.targets import ExecPlan, TerminalTarget

    runner = FakeRunner()
    runner.script(("php", "artisan", "health"), exit_code=0)
    plans: list[tuple[str, list[str], str | None]] = []

    def fake_plan(domain: str, argv: list[str], *, service: str | None, runner: Any) -> ExecPlan:
        plans.append((domain, argv, service))
        return ExecPlan(
            target=TerminalTarget("app", domain, None, domain, "noust-app-shop"),
            argv=tuple(argv),
            cwd=Path("/srv/shop/current"),
            env={"A": "b"},
            sandbox=None,
            scrubber=Scrubber(),
        )

    monkeypatch.setattr(health_command, "plan_app_exec", fake_plan)

    ok, said = health_command.run_health_command(
        DOMAIN, "php artisan health", service=None, timeout=4.2, runner=runner
    )

    assert (ok, said) == (True, "")
    assert plans == [(DOMAIN, ["php", "artisan", "health"], None)]
    assert runner.ran("php", "artisan", "health")


def test_a_failing_command_says_how_it_ended_without_the_secrets(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.redact import Scrubber
    from noust.core.runner import FakeRunner
    from noust.deployers.helpers import health_command
    from noust.terminal.targets import ExecPlan, TerminalTarget

    runner = FakeRunner()
    runner.script(("check",), exit_code=2, stderr="could not connect with hunter2secret")
    monkeypatch.setattr(
        health_command,
        "plan_app_exec",
        lambda domain, argv, *, service, runner: ExecPlan(
            target=TerminalTarget("app", domain, None, domain, "noust-app-shop"),
            argv=tuple(argv),
            cwd=None,
            env={},
            sandbox=None,
            scrubber=Scrubber(["hunter2secret"]),
        ),
    )

    ok, said = health_command.run_health_command(
        DOMAIN, "check", service=None, timeout=5, runner=runner
    )

    assert not ok
    assert "exited with status 2" in said
    assert "hunter2secret" not in said


# ---------------------------------------------------------------------------
# Setting it
# ---------------------------------------------------------------------------


def test_set_health_check_records_the_path_and_the_extras_together(store: NoustStore) -> None:
    _register(store)

    app = lifecycle.set_health_check(
        DOMAIN,
        path="/ready",
        expect="200",
        timeout=45,
        extras=HealthExtras(method="HEAD", retries=4),
        updated_by="ana",
    )

    assert (app.health_path, app.health_expect, app.health_timeout) == ("/ready", "200", 45)
    assert app_health_store.get_extras(DOMAIN) == HealthExtras(method="HEAD", retries=4)


def test_a_refused_extra_leaves_the_whole_check_as_it_was(store: NoustStore) -> None:
    _register(store, health_path="/old")

    with pytest.raises(ValidationError):
        lifecycle.set_health_check(
            DOMAIN, path="/new", expect=None, timeout=None, extras=HealthExtras(port=0)
        )

    assert store.get_app(DOMAIN).health_path == "/old"  # type: ignore[union-attr]


def test_leaving_the_extras_out_keeps_them(store: NoustStore) -> None:
    _register(store)
    app_health_store.set_extras(DOMAIN, HealthExtras(retries=4))

    lifecycle.set_health_check(DOMAIN, path="/x", expect=None, timeout=None)

    assert app_health_store.get_extras(DOMAIN).retries == 4


def test_a_static_site_has_no_check_to_configure(store: NoustStore) -> None:
    _register(store, is_static=True, app_type="static")

    with pytest.raises(DeploymentError):
        lifecycle.set_health_check(
            DOMAIN, path=None, expect=None, timeout=None, extras=HealthExtras(retries=2)
        )
