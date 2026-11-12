# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust health`` says when the registration of a domain expires, once the registries have been asked (item 173).

Nothing is asked to say it. With ``monitor.domain_expiry`` off, and no look ever kept, the report has
no such line at all (a server that never turned it on is not told about a check it does not run).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.domain_registrations import ERROR, NO_RDAP, OK, Registration
from noust.managers import health as health_module
from tests.support.cli_health import (  # noqa: F401
    _FakeCerts,
    _FakeServices,
    _FakeStore,
    _FakeWebServer,
    server,
)

# ruff: noqa: F811


def registration(domain: str, days: float, *, status: str = OK) -> Registration:
    now = datetime.now(timezone.utc)
    return Registration(
        domain=domain,
        status=status,
        expires_at=(now + timedelta(days=days)).isoformat(timespec="seconds")
        if status == OK
        else None,
        rdap_base="https://rdap.verisign.com/com/v1/",
        checked_at=now.isoformat(timespec="seconds"),
        next_check_at=(now + timedelta(days=1)).isoformat(timespec="seconds"),
    )


def known(monkeypatch: pytest.MonkeyPatch, *registrations: Registration) -> None:
    monkeypatch.setattr(
        health_module,
        "RegistrationStore",
        lambda: SimpleNamespace(all=lambda: list(registrations)),
    )


def check(server: Any) -> Any:
    report = health_module.collect_health_report()
    return report.domains, report


def test_a_server_that_never_asked_a_registry_has_no_such_line(server: Any) -> None:
    domains, report = check(server)

    assert domains is None
    assert all(item.name != "Domain registrations" for item in report.checks)


def test_far_from_the_end_is_ok_and_names_the_next_to_expire(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    known(monkeypatch, registration("a.example.com", 300), registration("b.example.org", 120))

    domains, report = check(server)

    assert domains.status == "ok"
    assert "2 domain(s)" in domains.value
    assert "next to expire: b.example.org in 119 days" in domains.value
    assert report.warnings == [] and report.issues == []


def test_within_30_days_is_a_warning_naming_the_domain(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    known(monkeypatch, registration("soon.example.com", 20), registration("far.example.org", 300))

    domains, report = check(server)

    assert domains.status == "warning"
    assert any(
        ("soon.example.com" in line and "expires in 19 days" in line)
        or "expires in 20 days" in line
        for line in report.warnings
    )


def test_within_7_days_is_an_issue(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    known(monkeypatch, registration("soon.example.com", 5))

    domains, report = check(server)

    assert domains.status == "error"
    assert any("soon.example.com" in line for line in report.issues)


def test_an_expired_registration_is_an_issue(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    known(monkeypatch, registration("gone.example.com", -3))

    domains, report = check(server)

    assert domains.status == "error"
    assert any("gone.example.com" in line and "expired" in line for line in report.issues)


def test_a_registry_that_publishes_nothing_or_could_not_be_asked_is_not_a_finding(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    known(
        monkeypatch,
        registration("example.es", 0, status=NO_RDAP),
        registration("example.org", 0, status=ERROR),
    )

    domains, report = check(server)

    assert domains.status == "info"
    assert "no expiry" in domains.value
    assert report.warnings == [] and report.issues == []


def test_a_store_that_cannot_be_read_leaves_the_report_whole(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    def broken() -> Any:
        raise sqlite3.OperationalError("no such table: domain_registrations")

    monkeypatch.setattr(health_module, "RegistrationStore", broken)

    domains, _ = check(server)

    assert domains is None


def test_the_command_prints_it_among_the_checks(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import functools
    import io

    from click.testing import CliRunner

    from noust.cli.commands import health as cli_health
    from noust.core.logger import Logger

    known(monkeypatch, registration("soon.example.com", 20))
    buffer = io.StringIO()
    monkeypatch.setattr(cli_health, "Logger", functools.partial(Logger, stream=buffer))

    CliRunner().invoke(cli_health.cli, [])

    assert "Domain registrations" in buffer.getvalue()
    assert "soon.example.com" in buffer.getvalue()
