# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust server software``: the command line client of the software manager (item 118).

Parity with the console is by construction (both call the manager). What is pinned here is what an
operator at a terminal sees: what is installed and who depends on it, a removal that says what it
would do before it does it, a refusal that says why and exits 1, an origin Noust does not know
needing a stronger yes than ``--yes``, and ``--dry-run`` removing nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import server_software
from noust.core.config import Config
from noust.core.fs import RecordingFileSystem
from noust.core.runner import FakeRunner
from noust.core.software_ledger import SoftwareLedger
from noust.core.store import App, get_store
from noust.managers.server.software import SoftwareManager
from tests.support.server_support import make_host, platform_for


@pytest.fixture(autouse=True)
def fresh_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    yield
    Config.reset_instance()


@pytest.fixture
def runner(runner: FakeRunner) -> FakeRunner:
    runner.only_knows("apt-get")
    runner.script(["dpkg-query"], stdout="")
    return runner


@pytest.fixture
def software(
    tmp_path: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> SoftwareManager:
    host = make_host(tmp_path / "root")
    manager = SoftwareManager(
        runner=runner, fs=RecordingFileSystem(), host=host, platform=platform_for("apt")
    )
    monkeypatch.setattr(server_software, "_software", lambda: manager)
    monkeypatch.setattr(server_software, "_require_root", lambda ctx, action: None)
    return manager


def installed(runner: FakeRunner, **versions: str) -> None:
    runner.script(
        ["dpkg-query"],
        stdout="".join(f"{n.replace('_', '-')} ii  {v}\n" for n, v in versions.items()),
    )


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["server", "software", *args], input=input)


def removals(runner: FakeRunner) -> list[tuple[str, ...]]:
    return [call for call in runner.calls if call[:2] == ("apt-get", "remove")]


class TestList:
    def test_it_says_what_is_installed_and_who_depends_on_it(
        self, software: SoftwareManager, runner: FakeRunner
    ) -> None:
        installed(runner, nginx="1.24.0-2")
        get_store().create_app(
            App(domain="shop.example.com", app_type="static", app_path="/x", webserver="nginx")
        )

        result = run("list")

        assert result.exit_code == 0, result.output
        line = next(line for line in result.output.splitlines() if line.strip().startswith("nginx"))
        assert "1.24.0-2" in line
        assert "application: shop.example.com" in line
        assert "cloudflared" in result.output

    def test_json_is_what_the_api_says(self, software: SoftwareManager, runner: FakeRunner) -> None:
        installed(runner, nginx="1.24.0-2")

        result = run("list", "--json")

        body = json.loads(result.output)
        assert body["family"] == "apt" and body["managed"] is True
        by_id = {item["id"]: item for item in body["items"]}
        assert by_id["nginx"]["installed"] is True and by_id["git"]["severity"] == "never"

    def test_it_records_nothing_because_it_changes_nothing(
        self, software: SoftwareManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core import audit

        recorded: list[str] = []
        monkeypatch.setattr(audit, "record", lambda event, **kw: recorded.append(event))

        run("list")

        assert not [event for event in recorded if event.startswith("cli.command")]


class TestInstall:
    def test_it_installs_with_the_package_manager_and_says_what_it_brought(
        self, software: SoftwareManager, runner: FakeRunner
    ) -> None:
        result = run("install", "chrony", "--yes")

        assert result.exit_code == 0, result.output
        assert ("apt-get", "install", "-y", "chrony") in runner.calls
        assert "chrony" in result.output

    def test_what_is_there_is_left_alone(
        self, software: SoftwareManager, runner: FakeRunner
    ) -> None:
        installed(runner, chrony="4.5")

        result = run("install", "chrony", "--yes")

        assert result.exit_code == 0, result.output
        assert "already installed" in result.output
        assert not [c for c in runner.calls if c[:2] == ("apt-get", "install")]

    def test_a_component_the_registry_does_not_have_is_an_error_that_says_how_to_list_them(
        self, software: SoftwareManager
    ) -> None:
        result = run("install", "nope", "--yes")

        assert result.exit_code != 0
        assert "noust server software list" in result.output + str(result.exception)

    def test_it_asks_before_it_installs(
        self, software: SoftwareManager, runner: FakeRunner
    ) -> None:
        result = run("install", "chrony", input="n\n")

        assert result.exit_code != 0
        assert not [c for c in runner.calls if c[:2] == ("apt-get", "install")]

    def test_json_without_yes_is_not_an_answer(self, software: SoftwareManager) -> None:
        assert run("install", "chrony", "--json").exit_code != 0


class TestRemove:
    def test_a_plan_says_what_would_go_and_changes_nothing(
        self, software: SoftwareManager, runner: FakeRunner
    ) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\nRemv libfoo1 [1]\n")

        result = run("remove", "fail2ban", "--plan")

        assert result.exit_code == 0, result.output
        assert "fail2ban" in result.output and "libfoo1" in result.output
        assert not removals(runner)

    def test_a_removal_noust_installed_goes_ahead_after_yes(
        self, software: SoftwareManager, runner: FakeRunner
    ) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")
        SoftwareLedger().record("fail2ban", packages=["fail2ban"], via="fail2ban")

        result = run("remove", "fail2ban", "--yes")

        assert result.exit_code == 0, result.output
        assert removals(runner) == [("apt-get", "remove", "-y", "fail2ban")]
        assert not [c for c in runner.calls if "purge" in c]

    def test_a_refusal_says_why_exits_one_and_changes_nothing(
        self, software: SoftwareManager, runner: FakeRunner
    ) -> None:
        installed(runner, nginx="1.24.0")
        get_store().create_app(
            App(domain="shop.example.com", app_type="static", app_path="/x", webserver="nginx")
        )

        result = run("remove", "nginx", "--yes", "--confirm-unknown-origin")

        assert result.exit_code == 1, result.output
        assert "shop.example.com" in result.output + str(result.exception)
        assert not removals(runner)

    def test_what_is_never_removed_says_so(
        self, software: SoftwareManager, runner: FakeRunner
    ) -> None:
        runner.only_knows("apt-get", "git")

        result = run("remove", "git", "--yes", "--confirm-unknown-origin")

        assert result.exit_code == 1, result.output
        assert "Every deployment from a repository needs it" in result.output + str(
            result.exception
        )

    def test_an_origin_noust_does_not_know_needs_more_than_yes(
        self, software: SoftwareManager, runner: FakeRunner
    ) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")

        result = run("remove", "fail2ban", "--yes")

        assert result.exit_code != 0
        assert "--confirm-unknown-origin" in result.output + str(result.exception)
        assert not removals(runner)

        confirmed = run("remove", "fail2ban", "--yes", "--confirm-unknown-origin")

        assert confirmed.exit_code == 0, confirmed.output
        assert removals(runner)

    def test_at_a_terminal_it_asks_the_stronger_question_in_words(
        self, software: SoftwareManager, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")
        monkeypatch.setattr(server_software, "_interactive", lambda ctx: True)

        result = run("remove", "fail2ban", input="y\ny\n")

        assert "Noust did not install fail2ban" in result.output
        assert removals(runner)

    def test_a_dry_run_removes_nothing(
        self,
        tmp_path: Path,
        runner: FakeRunner,
        software: SoftwareManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        installed(runner, fail2ban="1.0")
        runner.script(["apt-get", "-s"], stdout="Remv fail2ban [1.0]\n")
        get_store()  # a server that exists has its store: a rehearsal never makes one
        # The manager a real invocation builds takes the runner and the filesystem in force,
        # which --dry-run swaps; the rehearsal's runner still looks, through the fake.
        monkeypatch.setattr("noust.cli.app.SubprocessRunner", lambda: runner)
        rehearsed = SoftwareManager(host=software.host, platform=platform_for("apt"))
        monkeypatch.setattr(server_software, "_software", lambda: rehearsed)

        result = CliRunner().invoke(
            root_cli,
            ["--dry-run", "server", "software", "remove", "fail2ban", "--confirm-unknown-origin"],
        )

        assert result.exit_code == 0, result.output
        assert not removals(runner)
        assert "Would remove fail2ban" in result.output

    def test_the_command_the_error_suggests_works_as_typed(
        self, software: SoftwareManager, runner: FakeRunner
    ) -> None:
        # software.refused.unknown_origin ends with "repeat the request confirming it"; and the
        # not-found error says to list them: both are commands that exist.
        for arguments in (["list"], ["remove", "fail2ban", "--plan"]):
            assert run(*arguments, "--help").exit_code == 0
