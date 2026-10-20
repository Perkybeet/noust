# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the renaming of 4.0 shows: ``noust setup rename``, the doctor and ``noust health``.

What waits for the operator is a warning with the exact thing to change; what Noust finishes by
itself is not one. The command that finishes the rest changes nothing a rehearsal may not.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from noust.core import legacy_names
from noust.core.runner import FakeRunner
from noust.managers import health, name_migration, setup_doctor
from noust.managers.name_migration import Finding, NameMigration, StepOutcome
from tests.test_cli_setup import Wasm, prepared, wasm  # noqa: F401 - fixtures

WAITING = Finding(
    "upstream",
    "wasm_bg_shop",
    "A site somebody else wrote names the upstream wasm_bg_shop",
    fix="change the line to noust_bg_shop, then run: noust setup rename",
    waits_for_operator=True,
    notes=("/etc/nginx/sites-available/edge:6  proxy_pass http://wasm_bg_shop;",),
)
IN_PROGRESS = Finding(
    "unit",
    "wasm-shop",
    "The unit wasm-shop has the prefix of before 0.14.1",
    fix="noust update shop",
)


class TestTheCommand:
    def test_it_reports_every_step_and_what_it_kept(
        self,
        wasm: Wasm,  # noqa: F811
        prepared: dict[str, Any],  # noqa: F811
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        outcomes = [
            StepOutcome("markers", changed=["/etc/nginx/sites-available/a"]),
            StepOutcome(
                "upstreams", kept=["wasm_bg_shop: /etc/nginx/sites-available/edge:6 names it"]
            ),
            StepOutcome("images"),
        ]
        monkeypatch.setattr(NameMigration, "run", lambda self, steps=None: outcomes)
        monkeypatch.setattr(NameMigration, "findings", lambda self: [WAITING])

        result = wasm("setup", "rename")

        assert result.exit_code == 0, result.output
        assert "/etc/nginx/sites-available/a" in result.output
        assert "wasm_bg_shop: /etc/nginx/sites-available/edge:6 names it" in result.output
        assert "Nothing to rename" in result.output
        assert "These wait for you" in result.output
        assert "change the line to noust_bg_shop" in result.output
        assert "proxy_pass http://wasm_bg_shop;" in result.output

    def test_a_step_that_failed_makes_the_exit_non_zero_and_says_why(
        self,
        wasm: Wasm,  # noqa: F811
        prepared: dict[str, Any],  # noqa: F811
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            NameMigration,
            "run",
            lambda self, steps=None: [
                StepOutcome("upstreams", failed=["nginx: [emerg] host not found"])
            ],
        )
        monkeypatch.setattr(NameMigration, "findings", lambda self: [])

        result = wasm("setup", "rename")

        assert result.exit_code == 1
        assert "nginx: [emerg] host not found" in result.output

    def test_it_is_a_rehearsal_under_dry_run(
        self,
        wasm: Wasm,  # noqa: F811
        prepared: dict[str, Any],  # noqa: F811
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.core.fs import get_fs

        seen: list[type] = []
        monkeypatch.setattr(
            NameMigration,
            "run",
            lambda self, steps=None: seen.append(type(self.fs)) or [],
        )
        monkeypatch.setattr(NameMigration, "findings", lambda self: [])

        result = wasm("--dry-run", "setup", "rename")

        assert result.exit_code == 0, result.output
        assert seen and seen[0].__name__ == "DryRunFileSystem"
        assert get_fs() is not None


class TestTheDoctor:
    def test_what_waits_for_the_operator_is_a_warning_with_the_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(name_migration, "findings", lambda: [WAITING, IN_PROGRESS])

        section = setup_doctor._names_section()

        assert section is not None and section.title == "Names from before 4.0"
        waiting, in_progress = section.items
        assert waiting.status == "warn" and "noust setup rename" in (waiting.fix or "")
        assert waiting.notes == WAITING.notes
        assert in_progress.status == "info"

    def test_nothing_to_say_is_no_section(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(name_migration, "findings", lambda: [])

        assert setup_doctor._names_section() is None

    def test_a_store_that_cannot_be_read_does_not_break_the_doctor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken() -> list[Finding]:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(name_migration, "findings", broken)

        assert setup_doctor._names_section() is None

    def test_the_command_prints_it(
        self,
        wasm: Wasm,  # noqa: F811
        prepared: dict[str, Any],  # noqa: F811
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(name_migration, "findings", lambda: [WAITING])

        result = wasm("setup", "doctor")

        assert "Names from before 4.0" in result.output
        assert "/etc/nginx/sites-available/edge:6" in result.output
        assert "Fix: change the line to noust_bg_shop" in result.output


class TestHealth:
    def test_a_token_a_script_still_uses_is_a_warning_with_what_to_do(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = Finding(
            "token",
            "deploy-ci",
            "The API token deploy-ci signed in with the prefix of before 3.0 (2026-10-01)",
            fix="noust token create deploy-ci-new --scope deploy, then: noust token revoke 4",
            waits_for_operator=True,
        )
        monkeypatch.setattr(name_migration, "findings", lambda: [token, IN_PROGRESS])
        warnings: list[str] = []

        check = health._check_names(warnings)

        assert check is not None and check.status == "warning"
        assert "1 thing(s)" in check.value
        assert warnings == [
            "The API token deploy-ci signed in with the prefix of before 3.0 (2026-10-01). "
            "noust token create deploy-ci-new --scope deploy, then: noust token revoke 4"
        ]

    def test_what_noust_finishes_by_itself_is_not_a_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(name_migration, "findings", lambda: [IN_PROGRESS])
        warnings: list[str] = []

        assert health._check_names(warnings) is None
        assert warnings == []

    def test_a_server_with_nothing_left_has_no_such_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(name_migration, "findings", lambda: [])

        assert health._check_names([]) is None

    def test_an_error_reading_it_is_a_warning_about_the_check_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken() -> list[Finding]:
            raise OSError("no such directory")

        monkeypatch.setattr(name_migration, "findings", broken)
        warnings: list[str] = []

        check = health._check_names(warnings)

        assert check is not None and "Could not check" in check.value
        assert warnings == []

    def test_the_report_carries_it_where_the_checks_are_listed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        names = health.HealthCheck("Names before 4.0", "1 thing(s)", "warning")
        report = health.HealthReport(
            disk=None,
            nginx=health.HealthCheck("Nginx", "Running", "ok"),
            apache=health.HealthCheck("Apache", "Not installed", "info"),
            applications=health.HealthCheck("Applications", "0", "ok"),
            certificates=health.HealthCheck("Certificates", "0", "ok"),
            memory=health.HealthCheck("Memory", "ok", "ok"),
            names=names,
        )

        assert names in report.checks


def test_the_tokens_a_script_still_holds_are_found_through_the_token_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner
) -> None:
    """The finding is built from what the server recorded when the token signed in."""
    import secrets
    import time

    from noust.web.auth import SecurityConfig, TokenManager

    manager = TokenManager(SecurityConfig(state_dir=tmp_path / "state"))
    token = legacy_names.API_TOKEN_PREFIX + secrets.token_urlsafe(24)
    token_id = manager.sessions.create_api_token(
        "deploy-ci", manager._hash_token(token), "deploy", time.time(), None, created_by="test"
    )
    assert manager.verify_api_token(token, "127.0.0.1") is not None
    monkeypatch.setattr("noust.web.auth.get_global_token_manager", lambda: manager)
    migration = NameMigration(store=None, unit_dir=tmp_path / "units", marker_roots=[])  # type: ignore[arg-type]
    monkeypatch.setattr(NameMigration, "apps", lambda self: [])
    monkeypatch.setattr(NameMigration, "_upstream_findings", lambda self: [])
    monkeypatch.setattr(NameMigration, "_folder_findings", lambda self: [])

    (finding,) = migration.findings()

    assert finding.kind == "token" and finding.waits_for_operator
    assert "deploy-ci" in finding.text
    assert f"noust token revoke {token_id}" in (finding.fix or "")
