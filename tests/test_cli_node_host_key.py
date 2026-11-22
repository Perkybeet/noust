# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust node host-key``: accept the new SSH host key of a server, having seen both fingerprints.

The command shows the fingerprint that is pinned and the one the server presents now, and takes the new
one only when the person says it: by typing the fingerprint they read on the server itself, or by giving it
in ``--fingerprint`` for a script. There is no ``--yes``: a confirmation that can be given without looking
is the thing that makes a changed host key worthless to check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.store import NoustStore
from noust.fleet.keys import known_hosts_line
from tests.support.fleet_support import HOST_KEY, build_fleet, ed25519_line, fingerprint

NEW_KEY = ed25519_line(2)
NEW_FINGERPRINT = fingerprint(NEW_KEY)
PINNED_FINGERPRINT = fingerprint(HOST_KEY)


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    built = build_fleet(tmp_path)
    built.added()
    built.runner.script(["ssh-keyscan"], stdout=f"web2.example.com {NEW_KEY}\n")
    monkeypatch.setattr("noust.cli.commands.node._manager", lambda: built.manager)
    monkeypatch.setenv("NOUST_WEB_STATE_DIR", str(tmp_path / "state"))
    yield built
    NoustStore.reset_instance()


@pytest.fixture
def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.cli.commands.node._interactive", lambda: True)


def invoke(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["node", "host-key", "web-2", *args], input=input)


def pinned(fleet) -> str:
    return fleet.manager.get("web-2").host_key


class TestShowing:
    def test_both_fingerprints_are_shown_and_nothing_changes(self, fleet) -> None:
        result = invoke("--show")

        assert result.exit_code == 0, result.output
        assert PINNED_FINGERPRINT in result.output and NEW_FINGERPRINT in result.output
        assert pinned(fleet) == known_hosts_line("web-2", HOST_KEY)
        # What the central says it saw is never the thing to paste back: the person reads it elsewhere.
        assert f"--fingerprint {NEW_FINGERPRINT}" not in result.output

    def test_the_json_carries_both(self, fleet) -> None:
        result = invoke("--show", "--json")

        body = json.loads(result.stdout)
        assert (body["pinned"], body["presented"], body["changed"]) == (
            PINNED_FINGERPRINT,
            NEW_FINGERPRINT,
            True,
        )

    def test_a_server_that_presents_the_pinned_key_says_there_is_nothing_to_accept(
        self, fleet
    ) -> None:
        fleet.runner.script(["ssh-keyscan"], stdout=f"web2.example.com {HOST_KEY}\n")

        result = invoke("--show")

        assert result.exit_code == 0
        assert "same" in result.output.lower()


class TestAccepting:
    def test_typing_the_fingerprint_that_was_read_on_the_server_accepts_it(
        self, fleet, at_a_terminal
    ) -> None:
        result = invoke(input=NEW_FINGERPRINT + "\n")

        assert result.exit_code == 0, result.output
        assert PINNED_FINGERPRINT in result.output and NEW_FINGERPRINT in result.output
        assert pinned(fleet) == known_hosts_line("web-2", NEW_KEY)

    def test_typing_another_fingerprint_changes_nothing(self, fleet, at_a_terminal) -> None:
        result = invoke(input=fingerprint(ed25519_line(3)) + "\n")

        assert result.exit_code != 0
        assert pinned(fleet) == known_hosts_line("web-2", HOST_KEY)

    def test_a_script_gives_the_fingerprint_with_an_option(self, fleet) -> None:
        result = invoke("--fingerprint", NEW_FINGERPRINT)

        assert result.exit_code == 0, result.output
        assert pinned(fleet) == known_hosts_line("web-2", NEW_KEY)

    def test_a_wrong_fingerprint_in_a_script_fails_and_changes_nothing(self, fleet) -> None:
        result = invoke("--fingerprint", fingerprint(ed25519_line(3)))

        assert result.exit_code != 0
        assert NEW_FINGERPRINT in result.output
        assert pinned(fleet) == known_hosts_line("web-2", HOST_KEY)

    def test_with_nobody_to_ask_and_no_option_it_shows_and_does_not_accept(self, fleet) -> None:
        result = invoke(input="")

        assert result.exit_code != 0
        assert NEW_FINGERPRINT in result.output
        assert "--fingerprint" in str(result.exception)
        assert pinned(fleet) == known_hosts_line("web-2", HOST_KEY)

    def test_there_is_no_option_that_accepts_without_looking(self) -> None:
        from noust.cli.commands.node import host_key_command

        names = {name for param in host_key_command.params for name in param.opts}

        assert "--yes" not in names and "-y" not in names and "--force" not in names

    def test_a_dry_run_asks_nothing_and_changes_nothing(self, fleet) -> None:
        result = CliRunner().invoke(root_cli, ["--dry-run", "node", "host-key", "web-2"])

        assert result.exit_code == 0, result.output
        assert pinned(fleet) == known_hosts_line("web-2", HOST_KEY)
        assert not any(call[:1] == ("ssh-keyscan",) for call in fleet.runner.calls)

    def test_it_is_audited_when_it_accepts_and_when_it_is_refused(self, fleet, tmp_path) -> None:
        invoke("--fingerprint", fingerprint(ed25519_line(3)))
        invoke("--fingerprint", NEW_FINGERPRINT)

        lines = (tmp_path / "state" / "web-audit.log").read_text().splitlines()
        events = [json.loads(line) for line in lines]
        found = [(e["action"], e["result"]) for e in events if e["action"] == "fleet.node.host_key"]
        assert found == [("fleet.node.host_key", "failure"), ("fleet.node.host_key", "success")]
