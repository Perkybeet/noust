"""
``noust health`` and ``GET /api/system/health`` report the build sandbox and the audit trail.

Two regimes a server can be in without anything looking broken: applications
that still build as root (or run containers that are root on the host), and
an audit trail that stopped recording. Both are in the one report both clients
read (:func:`noust.managers.health.collect_health_report`).
"""

from __future__ import annotations

import pytest

from noust.core import audit
from noust.core.audit import AuditHealth
from noust.deployers.helpers import sandbox as build_sandbox
from noust.managers import health


class TestTheBuildSandbox:
    def test_every_warning_is_listed_and_the_check_warns(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            build_sandbox,
            "sandbox_warnings",
            lambda store=None: {"a.example.com": "a.example.com still builds as root."},
        )
        warnings: list[str] = []

        check = health._check_builds(warnings)

        assert check.name == "Build sandbox"
        assert check.status == "warning"
        assert warnings == ["a.example.com still builds as root."]

    def test_nothing_to_say_is_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(build_sandbox, "sandbox_warnings", lambda store=None: {})
        warnings: list[str] = []

        assert health._check_builds(warnings).status == "ok"
        assert warnings == []


class TestTheAuditTrail:
    def test_a_failing_trail_is_an_issue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            audit,
            "health",
            lambda log=None: AuditHealth(
                status="error", problems=["Audit events are not being written: disk full."]
            ),
        )
        issues: list[str] = []
        warnings: list[str] = []

        check = health._check_audit(issues, warnings)

        assert (check.name, check.status) == ("Audit trail", "error")
        assert issues == ["Audit: Audit events are not being written: disk full."]

    def test_a_warning_is_a_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            audit,
            "health",
            lambda log=None: AuditHealth(status="warning", problems=["A sink stopped receiving."]),
        )
        issues: list[str] = []
        warnings: list[str] = []

        assert health._check_audit(issues, warnings).status == "warning"
        assert (issues, warnings) == ([], ["Audit: A sink stopped receiving."])

    def test_a_working_trail_is_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(audit, "health", lambda log=None: AuditHealth(status="ok"))

        assert health._check_audit([], []).status == "ok"


def test_both_are_in_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_sandbox, "sandbox_warnings", lambda store=None: {})
    monkeypatch.setattr(audit, "health", lambda log=None: AuditHealth(status="ok"))
    for name in (
        "_check_disk_space",
        "_check_applications",
        "_check_certificates",
        "_check_memory",
    ):
        monkeypatch.setattr(health, name, lambda *a, **k: health.HealthCheck("x", "y", "ok"))
    monkeypatch.setattr(
        health,
        "_check_web_servers",
        lambda *a, **k: (health.HealthCheck("n", "y", "ok"), health.HealthCheck("a", "y", "ok")),
    )

    report = health.collect_health_report(hardening="off")

    names = [check.name for check in report.checks]
    assert "Build sandbox" in names and "Audit trail" in names
