# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db install --version``, ``noust db catalog`` and ``noust db settings`` (3.3, item 71).

The commands parse and print; the service decides. What is pinned: the
version and the flavour the name says reach the service, ``KEY=VALUE``
arguments reach it as a mapping, an exposure is only confirmed with
``--yes``, and showing settings is a read the audit policy does not record.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.audit_policy import is_read_only
from noust.cli.commands import db as db_cli
from noust.core.exceptions import ValidationError
from noust.managers.database import flavours
from noust.managers.database.service import DatabaseService
from noust.managers.server.host import HostPaths


@pytest.fixture
def cli_runner() -> CliRunner:
    """
    Returns:
        A Click test runner.
    """
    return CliRunner()


def outcome(**fields: Any) -> SimpleNamespace:
    data = {
        "engine": "postgresql",
        "display_name": "PostgreSQL",
        "file": "/etc/postgresql/16/main/conf.d/90-noust.conf",
        "changed": ["work_mem"],
        "action": "reload",
        "exposed": False,
        "warnings": [],
        **fields,
    }
    return SimpleNamespace(**data, to_dict=lambda: data)


def test_settings_assignments_reach_the_service(
    cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, str], bool]] = []

    def change(self, engine, values, *, confirm_exposure=False):
        calls.append((engine, dict(values), confirm_exposure))
        return outcome()

    monkeypatch.setattr(DatabaseService, "change_engine_settings", change)

    result = cli_runner.invoke(
        cli_app.cli, ["db", "settings", "postgresql", "work_mem=64MB", "save=", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert calls == [("postgresql", {"work_mem": "64MB", "save": ""}, True)]
    assert "work_mem changed" in result.output


def test_an_assignment_without_equals_is_a_usage_error(
    cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DatabaseService, "change_engine_settings", lambda *a, **k: outcome())

    result = cli_runner.invoke(cli_app.cli, ["db", "settings", "postgresql", "work_mem"])

    assert result.exit_code == 2
    assert "KEY=VALUE" in result.output


def test_an_exposure_asks_for_yes(
    cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def change(self, engine, values, *, confirm_exposure=False):
        raise ValidationError(
            "PostgreSQL will accept connections from beyond this server (10.0.0.5).",
            field="confirm_exposure",
        )

    monkeypatch.setattr(DatabaseService, "change_engine_settings", change)

    result = cli_runner.invoke(
        cli_app.cli, ["db", "settings", "postgresql", "listen_addresses=10.0.0.5"]
    )

    assert result.exit_code == 1
    assert "--yes" in result.output


def test_settings_without_assignments_show_the_report(
    cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = {
        "engine": "redis",
        "display_name": "Redis",
        "file": "/etc/redis/noust.conf",
        "running": True,
        "memory_bytes": 8 * 1024**3,
        "cpus": 4,
        "settings": [
            {
                "key": "maxmemory",
                "kind": "size",
                "unit": "MB",
                "current": "0",
                "configured": None,
                "recommended": "1GB",
                "restart": False,
                "editable": True,
            }
        ],
    }
    monkeypatch.setattr(
        DatabaseService, "engine_settings", lambda self, e: SimpleNamespace(to_dict=lambda: report)
    )

    shown = cli_runner.invoke(cli_app.cli, ["db", "settings", "redis"])
    as_json = cli_runner.invoke(cli_app.cli, ["db", "settings", "redis", "--json"])

    assert shown.exit_code == 0, shown.output
    assert "maxmemory" in shown.output and "1GB" in shown.output
    assert json.loads(as_json.output) == report


def test_showing_settings_is_a_read_and_changing_them_is_not() -> None:
    command = db_cli.cli.commands["settings"]

    assert is_read_only(command, "db settings", {"assignments": ()})
    assert not is_read_only(command, "db settings", {"assignments": ("work_mem=64MB",)})
    assert is_read_only(db_cli.cli.commands["catalog"], "db catalog", {})


def test_install_hands_the_typed_flavour_and_the_version_to_the_service(
    cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[str, str | None]] = []
    decided = SimpleNamespace(
        already_installed=False, describe=lambda: "MariaDB 11.4", manager=None
    )

    def plan(self, engine, *, flavour=None, version=None):
        seen.append((engine, version))
        return decided

    def install(self, engine, *, flavour=None, version=None):
        return SimpleNamespace(display_name="MariaDB", version="11.4.8", warnings=[])

    monkeypatch.setattr(DatabaseService, "plan_engine_install", plan)
    monkeypatch.setattr(DatabaseService, "install_engine", install)

    result = cli_runner.invoke(cli_app.cli, ["db", "install", "mariadb", "--version", "11.4"])

    assert result.exit_code == 0, result.output
    assert seen == [("mariadb", "11.4")]
    assert "MariaDB v11.4.8 installed" in result.output


def test_a_refused_version_is_a_failure_with_its_reason(
    cli_runner: CliRunner, runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "os-release").write_text(
        'ID=debian\nVERSION_CODENAME=trixie\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n'
    )
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    runner.only_knows("apt-get")

    result = cli_runner.invoke(cli_app.cli, ["db", "install", "mariadb", "--version", "11.4"])

    assert result.exit_code == 1
    assert "MariaDB 11.4 publishes no packages" in result.output
    assert not [call for call in runner.calls if call[0] in ("apt-get", "curl")]


def test_the_catalog_lists_what_can_be_installed(
    cli_runner: CliRunner, runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "os-release").write_text(
        'ID=ubuntu\nVERSION_CODENAME=noble\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\n'
    )
    monkeypatch.setattr(flavours, "HOST", HostPaths(tmp_path))
    runner.only_knows("apt-get")

    result = cli_runner.invoke(cli_app.cli, ["db", "catalog", "--json"])

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    by_name = {entry["flavour"]: entry for entry in body["flavours"]}
    assert by_name["valkey"]["installable"] is True
    assert [v["version"] for v in by_name["mongodb"]["versions"]] == ["8.0"]
