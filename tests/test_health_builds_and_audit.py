# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

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
        monkeypatch.setattr(build_sandbox, "compose_warnings", lambda store=None: {})
        warnings: list[str] = []

        assert health._check_builds(warnings).status == "ok"
        assert warnings == []

    def test_a_stack_that_has_the_server_is_listed_and_is_not_called_a_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(build_sandbox, "sandbox_warnings", lambda store=None: {})
        monkeypatch.setattr(
            build_sandbox,
            "compose_warnings",
            lambda store=None: {
                "shop.example.com": "shop.example.com runs with what gives this server"
            },
        )
        warnings: list[str] = []

        check = health._check_builds(warnings)

        assert check.status == "warning"
        assert warnings == ["shop.example.com runs with what gives this server"]
        assert "Compose stack" in check.value
        assert "build as root" not in check.value

    def test_builds_and_stacks_are_counted_apart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            build_sandbox,
            "sandbox_warnings",
            lambda store=None: {"a.example.com": "a.example.com still builds as root."},
        )
        monkeypatch.setattr(
            build_sandbox,
            "compose_warnings",
            lambda store=None: {"b.example.com": "b.example.com runs"},
        )
        warnings: list[str] = []

        check = health._check_builds(warnings)

        assert "1 application(s) build as root" in check.value
        assert "1 Compose stack(s)" in check.value
        assert len(warnings) == 2


class TestTheCheckOfHowComposeStacksStart:
    """What is to be looked at about how Compose stacks start is a line of its own, not a part of Build sandbox."""

    def stacks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sentences: list[str],
        domains: list[str] | None = None,
    ) -> None:
        from noust.deployers import compose_upgrade

        monkeypatch.setattr(compose_upgrade, "attention", lambda **_k: list(sentences))
        monkeypatch.setattr(
            compose_upgrade,
            "stack_domains",
            lambda **_k: ["shop.example.com"] if domains is None else domains,
        )

    def test_the_sentences_are_warnings_under_their_own_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.stacks(
            monkeypatch, ["shop.example.com: its unit is yours", "b.example.com: held back"]
        )
        warnings: list[str] = []

        check = health._check_compose_stacks(warnings)

        assert check is not None
        assert check.name == "Compose stacks"
        assert check.status == "warning"
        assert check.value == "2 thing(s) to look at about how Compose stacks start"
        assert warnings == ["shop.example.com: its unit is yours", "b.example.com: held back"]

    def test_nothing_to_say_is_ok_and_says_how_many_stacks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.stacks(monkeypatch, [], ["a.example.com", "b.example.com"])
        warnings: list[str] = []

        check = health._check_compose_stacks(warnings)

        assert check is not None and check.status == "ok"
        assert "2 stack(s)" in check.value
        assert warnings == []

    def test_a_server_without_a_compose_stack_has_no_such_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.stacks(monkeypatch, [], [])

        assert health._check_compose_stacks([]) is None

    def test_a_store_that_cannot_be_read_is_a_warning_that_says_so_and_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3

        from noust.deployers import compose_upgrade

        def broken(**_kwargs: object) -> list[str]:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(compose_upgrade, "stack_domains", broken)

        check = health._check_compose_stacks([])

        assert check is not None and check.status == "warning"
        assert "Could not check" in check.value and "database is locked" in check.value

    def test_build_sandbox_no_longer_carries_them(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(build_sandbox, "sandbox_warnings", lambda store=None: {})
        monkeypatch.setattr(build_sandbox, "compose_warnings", lambda store=None: {})
        self.stacks(monkeypatch, ["shop.example.com: its unit is yours"])
        warnings: list[str] = []

        check = health._check_builds(warnings)

        assert check.status == "ok" and "Compose stacks start" not in check.value
        assert warnings == []

    def test_it_is_a_check_of_the_report_in_the_order_the_command_prints_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(build_sandbox, "sandbox_warnings", lambda store=None: {})
        monkeypatch.setattr(audit, "health", lambda log=None: AuditHealth(status="ok"))
        self.stacks(monkeypatch, ["shop.example.com: its unit is yours"])
        for name in (
            "_check_disk_space",
            "_check_applications",
            "_check_certificates",
            "_check_memory",
            "_check_descriptors",
        ):
            monkeypatch.setattr(health, name, lambda *a, **k: health.HealthCheck("x", "y", "ok"))
        monkeypatch.setattr(
            health,
            "_check_web_servers",
            lambda *a, **k: (
                health.HealthCheck("n", "y", "ok"),
                health.HealthCheck("a", "y", "ok"),
            ),
        )

        report = health.collect_health_report(hardening="off")

        names = [check.name for check in report.checks]
        assert names.index("Build sandbox") + 1 == names.index("Compose stacks")
        assert "shop.example.com: its unit is yours" in report.warnings
        assert report.compose is not None and report.compose.status == "warning"


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
        "_check_descriptors",
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
