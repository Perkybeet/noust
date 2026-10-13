# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust mcp``: turning the MCP endpoint on, choosing its tools, and a token that holds only them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands.mcp import PRESETS, permissions_for
from noust.core.accounts import AccountManager, passwords
from noust.core.config import Config
from noust.core.store import NoustStore
from noust.web.auth import STATE_DIR_ENV, SecurityConfig, TokenManager
from noust.web.mcp.tools import FORBIDDEN_PERMISSIONS

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A store, a state directory, a configuration and two accounts of the test's own."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    Config.reset_instance()
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    accounts = AccountManager(store)
    accounts.create("ops", "admin", password=PASSWORD)
    accounts.create("vera", "viewer", password=PASSWORD)
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


def run(*args: str, code: int = 0) -> str:
    """
    Args:
        *args: The arguments after ``noust``.
        code: The exit code expected.

    Returns:
        What the command printed, and the error a refusal raised.
    """
    result = CliRunner().invoke(root_cli, list(args))
    output = f"{result.output}{result.exception or ''}"
    assert result.exit_code == code, output
    return output


def test_it_is_off_until_it_is_turned_on() -> None:
    assert json.loads(run("--json", "mcp", "status"))["enabled"] is False

    run("mcp", "enable")
    assert json.loads(run("--json", "mcp", "status"))["enabled"] is True

    run("mcp", "disable")
    assert json.loads(run("--json", "mcp", "status"))["enabled"] is False


def test_the_groups_are_chosen_and_nothing_else_is_accepted() -> None:
    run("mcp", "groups", "inspect")

    assert json.loads(run("--json", "mcp", "status"))["groups"] == ["inspect"]
    assert "is not one of" in run("mcp", "groups", "shell", code=2).lower()


def test_the_tools_are_listed_with_what_they_need() -> None:
    tools = json.loads(run("--json", "mcp", "tools"))["tools"]

    by_name = {tool["name"]: tool for tool in tools}
    assert by_name["stop_application"]["confirm"] is True
    assert by_name["list_applications"]["read_only"] is True
    assert by_name["deploy_application"]["permission"] == "apps.deploy"


def test_a_preset_asks_for_what_its_tools_need_and_never_what_a_client_must_not_have() -> None:
    inspect = set(permissions_for("inspect"))
    control = set(permissions_for("control"))

    assert "apps.read" in inspect and "apps.deploy" not in inspect
    assert {"apps.deploy", "apps.operate", "backups.run"} <= control
    for preset in PRESETS:
        assert not set(permissions_for(preset)) & FORBIDDEN_PERMISSIONS


def test_a_token_is_issued_with_exactly_the_presets_permissions() -> None:
    output = run("mcp", "token", "ci-bot", "--preset", "deploy", "--owner", "ops")

    token = re.search(r"Token: (\S+)", output)
    assert token is not None
    record = next(
        r for r in TokenManager(SecurityConfig()).list_api_tokens() if r["name"] == "ci-bot"
    )
    assert set(record["permissions"]) == set(permissions_for("deploy"))
    assert record["expires_at"] is not None and not record["allow_elevated"]
    assert "only time" in output


def test_a_token_cannot_hold_more_than_its_owner() -> None:
    output = run("mcp", "token", "greedy", "--preset", "control", "--owner", "vera", code=1)

    assert "cannot hold more than its owner" in output


def test_a_token_may_be_bound_to_a_team() -> None:
    from noust.core.teams import TeamStore

    TeamStore().create_team("Acme", slug="acme")
    TeamStore().add_member("acme", "ops", "admin")

    output = run(
        "mcp", "token", "acme-mcp", "--preset", "inspect", "--owner", "ops", "--team", "acme"
    )

    assert "acme-mcp" in output
    records = {r["name"]: r for r in TokenManager(SecurityConfig()).list_api_tokens()}
    assert records["acme-mcp"]["bound_kind"] == "team"
