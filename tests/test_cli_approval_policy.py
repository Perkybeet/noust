# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust approval policy``: the approval policy for a token and the built-in assistant, from the terminal.

The same :class:`~noust.core.caller_policy.CallerPolicy` the console's route calls. Root at the terminal is
not asked for a second person (the command line is the emergency channel) and every change is on the audit
trail with the operating system identity of whoever made it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core import audit
from noust.core.caller_policy import CallerPolicy
from noust.core.store import NoustStore


@pytest.fixture(autouse=True)
def server(tmp_path: Path, real_config: Any) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    yield store
    NoustStore.reset_instance()


def run(*args: str, code: int = 0) -> str:
    result = CliRunner().invoke(root_cli, ["approvals", "policy", *args])
    output = f"{result.output}{result.exception or ''}"
    assert result.exit_code == code, output
    return output


class TestShow:
    def test_it_lists_what_a_token_and_the_assistant_may_do_and_what_nobody_chose(self) -> None:
        output = run("show")
        assert "apps.deploy" in output and "secrets.reveal" in output
        assert "token" in output and "assistant" in output
        assert "alone" in output and "propose" in output and "never (fixed)" in output
        assert "*" not in output

    def test_only_one_class_or_only_what_was_chosen(self) -> None:
        only = run("show", "--caller", "assistant")
        assert " assistant " in only and " token " not in only
        assert "Nobody chose a cell" in run("show", "--changed")

    def test_the_json_carries_every_cell_with_what_it_offers(self) -> None:
        table = json.loads(run("show", "--json"))
        cells = table["cells"]
        assert len(cells) == 4 * len({c["permission"] for c in cells})
        floored = [
            c for c in cells if c["caller"] == "assistant" and c["permission"] == "secrets.reveal"
        ]
        assert floored and all(c["offered"] == ["never"] and c["fixed"] for c in floored)
        assert table["token_production_approval"] is False


class TestSet:
    def test_it_gives_a_cell_a_value_that_show_marks_as_chosen(self, server: NoustStore) -> None:
        run("set", "apps.deploy", "token", "production", "propose")
        policy = CallerPolicy(server)
        assert policy.cell("token", "production", "apps.deploy").value == "propose"
        assert policy.cell("token", "other", "apps.deploy").value == "alone"
        shown = run("show", "--caller", "token", "--changed")
        assert "propose*" in shown and "apps.deploy" in shown

    def test_both_environments_by_default(self, server: NoustStore) -> None:
        run("set", "apps.operate", "token", "both", "never")
        policy = CallerPolicy(server)
        assert policy.cell("token", "production", "apps.operate").value == "never"
        assert policy.cell("token", "other", "apps.operate").value == "never"

    def test_what_the_floor_fixes_is_refused_saying_what_it_may_be(self) -> None:
        output = run("set", "secrets.reveal", "assistant", "production", "alone", code=1)
        assert "never" in output

    def test_a_permission_that_only_reads_has_no_cell(self) -> None:
        output = run("set", "apps.read", "token", "both", "never", code=1)
        assert "does not change anything" in output or "changes something" in output

    def test_a_value_that_does_not_exist_is_refused_by_the_command(self) -> None:
        result = CliRunner().invoke(
            root_cli, ["approvals", "policy", "set", "apps.deploy", "token", "both", "sometimes"]
        )
        assert result.exit_code == 2

    def test_the_change_is_on_the_audit_trail(self) -> None:
        run("set", "backups.run", "assistant", "other", "alone")
        events = [
            json.loads(line)
            for line in audit.get_log().path.read_text().splitlines()
            if line.strip()
        ]
        found = [e for e in events if e["action"] == "approval.policy"]
        assert found
        (written,) = found[-1]["details"]["cells"]
        assert (written["caller"], written["permission"], written["value"]) == (
            "assistant",
            "backups.run",
            "alone",
        )


class TestReset:
    def test_it_puts_a_cell_back_to_what_the_release_ships(self, server: NoustStore) -> None:
        run("set", "apps.deploy", "token", "both", "never")
        assert "back to what the release ships" in run("reset", "apps.deploy", "token", "both")
        assert CallerPolicy(server).cell("token", "production", "apps.deploy").chosen is None

    def test_a_cell_nobody_chose_has_nothing_to_put_back(self) -> None:
        assert "nothing to put back" in run("reset", "apps.deploy", "token", "both")
