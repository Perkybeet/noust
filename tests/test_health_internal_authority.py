# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The server's own certificate authority in ``noust health``.

A certificate that expires is signed again by itself; the authority is not, because a new root has
to be trusted again on every device. So the report warns six months before and calls it an issue in
the last month, and a server that has none says nothing of it. (Coolify's own CA expires after ten
years without a word.)
"""

# ruff: noqa: F811
from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import CertificateError
from noust.managers import health as health_module
from noust.managers.internal_ca import CaStatus
from tests.test_cli_health import _FakeCerts, server  # noqa: F401


def status(days_left: int) -> CaStatus:
    warnings = (
        (f"The authority expires in {days_left} days. A new root has to be trusted again.",)
        if days_left < 180
        else ()
    )
    return CaStatus(
        subject="CN = Noust internal CA (nas)",
        fingerprint="AA:BB",
        not_before="2026-01-01T00:00:00+00:00",
        not_after="2036-01-01T00:00:00+00:00",
        days_left=days_left,
        permitted_dns=("lan",),
        permitted_networks=(),
        warnings=warnings,
    )


class Authority:
    def __init__(self, present: bool = True, days_left: int = 3000, broken: bool = False) -> None:
        self.present = present
        self.days_left = days_left
        self.broken = broken

    def exists(self) -> bool:
        return self.present

    def status(self) -> CaStatus | None:
        if self.broken:
            raise CertificateError("openssl could not read it")
        return status(self.days_left)


def install(monkeypatch: pytest.MonkeyPatch, authority: Authority) -> None:
    monkeypatch.setattr(health_module, "InternalCA", lambda: authority)


def test_a_server_without_one_says_nothing_of_it(
    server: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Authority(present=False))

    report = health_module.collect_health_report()

    assert report.warnings == [] and report.issues == []
    assert report.certificates.status == "info"


def test_one_with_years_left_is_not_news(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    server.certs = _FakeCerts([{"name": "example.com", "expiry": "2999-01-01"}])
    install(monkeypatch, Authority(days_left=3000))

    report = health_module.collect_health_report()

    assert report.warnings == [] and report.certificates.status == "ok"


def test_within_six_months_it_is_a_warning_in_the_certificates_row(
    server: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, Authority(days_left=120))

    report = health_module.collect_health_report()

    assert any("expires in 120 days" in line for line in report.warnings)
    assert report.certificates.status == "warning"
    assert "internal authority is about to expire" in report.certificates.value
    assert report.issues == []


def test_in_the_last_month_it_is_an_issue_and_the_row_is_an_error(
    server: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, Authority(days_left=12))

    report = health_module.collect_health_report()

    assert any("expires in 12 days" in line for line in report.issues)
    assert report.certificates.status == "error" and report.verdict == "error"


def test_an_authority_that_cannot_be_read_is_a_warning_not_a_crash(
    server: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, Authority(broken=True))

    report = health_module.collect_health_report()

    assert any("could not be read: openssl could not read it" in line for line in report.warnings)
