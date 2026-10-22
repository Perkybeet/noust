# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust push``: what the command line says and asks (checklist A-23).

The commands are a client of :mod:`noust.pushdeploy`, which the tests of that package cover; what
is pinned here is what each adds: where a key may come from (a file, standard input, an
argument, never two), what is printed (the remote and the two commands to type), the JSON, the
rehearsal that writes nothing, and the refusals that reach the terminal.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import push_store
from tests.push_support import DOMAIN, PushRig, build_rig, ed25519


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PushRig]:
    yield from build_rig(tmp_path, monkeypatch)


@pytest.fixture
def cli(rig: PushRig, monkeypatch: pytest.MonkeyPatch):
    """Run ``noust push ...`` against the rig's server."""
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli
    from noust.core.runner import set_runner

    monkeypatch.setattr("noust.cli.commands.push.PushHost", lambda: rig.world.host())
    monkeypatch.setattr("noust.cli.commands.push._repos", lambda: rig.repos)
    monkeypatch.setattr("noust.cli.commands.push._actor", lambda: "root")

    def invoke(*args: str, stdin: str | None = None) -> Any:
        return CliRunner().invoke(root_cli, ["push", *args], input=stdin)

    yield invoke
    set_runner(None)


@pytest.fixture
def ready(rig: PushRig) -> PushRig:
    rig.set_up()
    rig.app()
    return rig


class TestSetup:
    def test_it_says_what_it_made(self, rig: PushRig, cli) -> None:
        result = cli("setup")

        assert result.exit_code == 0, result.output
        assert "Prepared this server for git push" in result.output
        assert "noust-git (created)" in result.output
        assert str(rig.world.home) in result.output
        assert rig.world.dropin.exists()

    def test_json(self, cli) -> None:
        result = cli("setup", "--json")
        shown = json.loads(result.output)
        assert shown["account"] == "noust-git" and shown["account_created"] is True
        assert shown["ssh_port"] == 22

    def test_a_rehearsal_writes_nothing(
        self, rig: PushRig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from click.testing import CliRunner

        from noust.cli.app import cli as root_cli

        monkeypatch.setattr("noust.cli.commands.push.PushHost", lambda: rig.world.host())
        result = CliRunner().invoke(root_cli, ["--dry-run", "push", "setup"])
        assert result.exit_code == 0, result.output
        assert "Would prepare this server" in result.output
        assert not rig.world.dropin.exists()


class TestStatus:
    def test_before_and_after(self, rig: PushRig, cli) -> None:
        before = cli("status")
        assert "Takes pushes" in before.output and "no" in before.output
        assert "noust push setup" in before.output

        rig.set_up()
        after = json.loads(cli("status", "--json").output)
        assert after["ready"] is True and after["problems"] == []


class TestEnable:
    def test_it_prints_the_remote_and_the_two_commands_to_type(
        self, ready: PushRig, cli, tmp_path: Path
    ) -> None:
        pub = tmp_path / "id.pub"
        pub.write_text(ed25519(1, "ana@laptop") + "\n")

        result = cli("enable", DOMAIN, "--key-file", str(pub), "--label", "laptop")

        assert result.exit_code == 0, result.output
        assert f"{DOMAIN} deploys from git push to main" in result.output
        assert f"git remote add noust noust-git@{DOMAIN}:{DOMAIN}.git" in result.output
        assert "git push noust main" in result.output
        assert "Key laptop" in result.output
        assert push_store.get(DOMAIN, store=ready.store) is not None

    def test_without_a_key_it_says_nobody_can_push_yet(self, ready: PushRig, cli) -> None:
        result = cli("enable", DOMAIN)
        assert result.exit_code == 0, result.output
        assert "No key may push yet" in result.output
        assert f"noust push key add {DOMAIN} LABEL --key-file" in result.output

    def test_the_key_can_come_from_standard_input(self, ready: PushRig, cli) -> None:
        result = cli("enable", DOMAIN, "--key-file", "-", "--label", "ci", stdin=ed25519(2) + "\n")
        assert result.exit_code == 0, result.output
        assert [key.label for key in ready.repos.keys.list(DOMAIN)] == ["ci"]

    def test_or_as_an_argument(self, ready: PushRig, cli) -> None:
        result = cli("enable", DOMAIN, "--key", ed25519(3), "--label", "ana")
        assert result.exit_code == 0, result.output

    def test_both_ways_at_once_are_refused(self, ready: PushRig, cli) -> None:
        result = cli("enable", DOMAIN, "--key", ed25519(3), "--key-file", "-", "--label", "x")
        assert result.exit_code != 0
        assert "not both" in str(result.exception)
        assert push_store.get(DOMAIN, store=ready.store) is None

    def test_a_key_file_that_cannot_be_read(self, ready: PushRig, cli, tmp_path: Path) -> None:
        result = cli("enable", DOMAIN, "--key-file", str(tmp_path / "nope.pub"), "--label", "x")
        assert result.exit_code != 0
        assert "Cannot read" in str(result.exception)

    def test_json(self, ready: PushRig, cli) -> None:
        shown = json.loads(cli("enable", DOMAIN, "--branch", "release", "--json").output)
        assert shown["enabled"] is True and shown["branch"] == "release"
        assert shown["remote"] == f"noust-git@{DOMAIN}:{DOMAIN}.git"

    def test_a_refusal_reaches_the_terminal(self, rig: PushRig, cli) -> None:
        rig.app()
        result = cli("enable", DOMAIN)
        assert result.exit_code != 0
        assert "not set up to take git pushes" in str(result.exception)

    def test_a_rehearsal_changes_nothing(
        self, ready: PushRig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from click.testing import CliRunner

        from noust.cli.app import cli as root_cli

        monkeypatch.setattr("noust.cli.commands.push._repos", lambda: ready.repos)
        result = CliRunner().invoke(root_cli, ["--dry-run", "push", "enable", DOMAIN])
        assert result.exit_code == 0, result.output
        assert "Would turn git push on" in result.output
        assert push_store.get(DOMAIN, store=ready.store) is None


class TestShowing:
    def test_a_application_without_push(self, ready: PushRig, cli) -> None:
        result = cli("show", DOMAIN)
        assert "off" in result.output and f"noust push enable {DOMAIN}" in result.output

    def test_show_prints_the_ssh_configuration_that_selects_the_key(
        self, ready: PushRig, cli
    ) -> None:
        cli("enable", DOMAIN, "--key", ed25519(1), "--label", "laptop")

        result = cli("show", DOMAIN, "--host", "git.example.org")

        assert "Last push" in result.output and "never" in result.output
        assert "In ~/.ssh/config" in result.output
        assert f"Host noust-{DOMAIN}" in result.output
        assert "HostName git.example.org" in result.output

    def test_list(self, ready: PushRig, cli) -> None:
        assert "No application deploys from git push" in cli("list").output
        cli("enable", DOMAIN, "--key", ed25519(1), "--label", "laptop")
        listed = json.loads(cli("list", "--json").output)
        assert [item["domain"] for item in listed] == [DOMAIN]
        assert DOMAIN in cli("list").output


class TestKeys:
    def test_adding_listing_and_removing(self, ready: PushRig, cli) -> None:
        cli("enable", DOMAIN)

        added = cli("key", "add", DOMAIN, "ci", "--key", ed25519(5))
        assert added.exit_code == 0 and "may now push" in added.output
        listed = json.loads(cli("key", "list", DOMAIN, "--json").output)
        assert [item["label"] for item in listed] == ["ci"]
        assert "ci" in cli("key", "list", DOMAIN).output

        removed = cli("key", "remove", DOMAIN, "ci")
        assert removed.exit_code == 0 and "may no longer push" in removed.output
        assert "No key may push" in cli("key", "list", DOMAIN).output

    def test_a_key_is_needed(self, ready: PushRig, cli) -> None:
        cli("enable", DOMAIN)
        result = cli("key", "add", DOMAIN, "ci")
        assert result.exit_code != 0
        assert "--key-file or --key" in result.output

    def test_removing_what_is_not_there(self, ready: PushRig, cli) -> None:
        cli("enable", DOMAIN)
        result = cli("key", "remove", DOMAIN, "ghost")
        assert result.exit_code != 0
        assert "has no key ghost" in str(result.exception)


class TestDisablingAndRefreshing:
    def test_disable(self, ready: PushRig, cli) -> None:
        cli("enable", DOMAIN, "--key", ed25519(1), "--label", "laptop")

        result = cli("disable", DOMAIN)

        assert result.exit_code == 0, result.output
        assert f"{DOMAIN} no longer deploys from git push" in result.output
        assert push_store.get(DOMAIN, store=ready.store) is None
        assert (ready.world.home / f"{DOMAIN}.git").is_dir()

    def test_disable_and_delete_the_repository(self, ready: PushRig, cli) -> None:
        cli("enable", DOMAIN)
        cli("disable", DOMAIN, "--delete-repository")
        assert not (ready.world.home / f"{DOMAIN}.git").exists()

    def test_refresh(self, ready: PushRig, cli) -> None:
        cli("enable", DOMAIN)
        result = cli("refresh", DOMAIN)
        assert result.exit_code == 0 and "Wrote the hook" in result.output
        assert json.loads(cli("refresh", DOMAIN, "--json").output)["hook_ok"] is True

    def test_an_application_without_push_cannot_be_refreshed(self, ready: PushRig, cli) -> None:
        result = cli("refresh", DOMAIN)
        assert result.exit_code != 0
        assert "does not deploy from git push" in str(result.exception)
