# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust compliance set token_max_days`` at the terminal.

It answered "No control changes value" on a server with no framework and applied no limit, because
the number lives in a key every file holds at 90 and so could not tell a choice from the default.
The choice is recorded now (tests/test_compliance_token_limit.py proves the rule); here the person
at the terminal is told what happened, in both situations.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import audit_policy
from noust.cli.app import cli
from noust.core.audit import Actor
from noust.core.compliance import resolve
from noust.core.config import Config
from noust.core.store import NoustStore, get_store


@pytest.fixture(autouse=True)
def operator(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = Actor(kind="cli", id="0", name="root", via="sudo", source=None)
    monkeypatch.setattr(audit_policy.audit, "cli_actor", lambda: actor)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = get_store()
    yield instance
    NoustStore.reset_instance()


def run(*args: str) -> Any:
    return CliRunner().invoke(cli, ["compliance", *args])


def test_set_with_no_framework_says_it_limits_tokens_now(real_config: Config) -> None:
    result = run("set", "token_max_days", "90", "--yes")

    assert result.exit_code == 0, result.output
    assert "No control changes value" not in result.output
    assert "no limit" in result.output and "90 days" in result.output
    assert resolve.value("token_max_days", real_config) == 90


def test_set_while_a_framework_asks_the_same_says_what_it_recorded(real_config: Config) -> None:
    assert run("enable", "ens-media", "--yes").exit_code == 0

    result = run("set", "token_max_days", "90", "--yes", "--reason", "x")

    assert result.exit_code == 0, result.output
    assert "No control changes value now" in result.output
    assert "token_max_days" in result.output and "no framework" in result.output
    run("disable", "ens-media", "--yes", "--reason", "x")
    assert resolve.value("token_max_days", real_config) == 90


def test_the_json_plan_names_the_move(real_config: Config) -> None:
    result = run("set", "token_max_days", "90", "--json")

    body = json.loads(result.output)
    assert body["applied"] is False
    assert body["plan"]["settings"] == {"token_max_days": 90}
    assert body["plan"]["effects"][0]["after"] == 90
    assert resolve.value("token_max_days", real_config) is None
