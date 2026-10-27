# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Why the terminal on the server itself cannot be opened, and the command that changes it.

The console blamed the ENS profile and proposed ``noust config set terminal.server.force_ens true`` on
a server with no ENS, where what keeps it off is the control ``server_terminal`` set to ``refused``.
The server says what blocks it, with a code the console translates and the exact command, and a test
runs each command it names.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import audit_policy
from noust.cli.app import cli
from noust.core.audit import Actor
from noust.core.config import Config
from noust.core.store import NoustStore, get_store
from noust.terminal.settings import (
    SERVER_ENABLE_COMMAND,
    SERVER_FORCE_COMMAND,
    load_settings,
)


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


def run(command: str, input: str = "y\n") -> Any:
    argv = shlex.split(command)
    assert argv[0] == "noust", command
    return CliRunner().invoke(cli, argv[1:], input=input)


class TestWhatBlocksIt:
    def test_the_switch_is_off(self, real_config: Config) -> None:
        block = load_settings(real_config).server_block()

        assert block is not None
        assert block.code == "server_shell_off"
        assert block.command == SERVER_ENABLE_COMMAND
        assert block.control is None and block.forced_by == ()
        assert "terminal.server.enabled" in block.what

    def test_nothing_blocks_a_server_that_is_on_and_unrestricted(self, real_config: Config) -> None:
        real_config.set("terminal.server.enabled", True)

        settings = load_settings(real_config)

        assert settings.server_block() is None and settings.server_refusal() is None

    def test_the_control_alone_blocks_it_on_a_server_without_a_framework(
        self, real_config: Config
    ) -> None:
        """The case in the report: no ENS anywhere, and the console blamed the profile."""
        real_config.set("terminal.server.enabled", True)
        real_config.set("security.server_terminal", "refused")

        block = load_settings(real_config).server_block()

        assert block is not None
        assert block.code == "server_shell_control"
        assert block.control == "server_terminal" and block.forced_by == ()
        assert block.command == "noust compliance set server_terminal allowed"
        assert "server_terminal" in block.what
        assert "ENS" not in block.what + block.how and "profile" not in block.what + block.how
        assert block.command in block.how

    def test_the_command_for_the_control_works(self, real_config: Config) -> None:
        real_config.set("terminal.server.enabled", True)
        real_config.set("security.server_terminal", "refused")
        real_config.write()
        block = load_settings(real_config).server_block()
        assert block is not None

        result = run(block.command)

        assert result.exit_code == 0, result.output
        assert load_settings(real_config).server_allowed is True

    def test_a_framework_that_asks_for_it_is_named(self, real_config: Config) -> None:
        real_config.set("terminal.server.enabled", True)
        real_config.set("security.frameworks", ["ens-media"])

        block = load_settings(real_config).server_block()

        assert block is not None
        assert block.code == "server_shell_framework"
        assert block.control == "server_terminal" and block.forced_by == ("ens-media",)
        # ENS asks for a reason with every change, so what is printed carries the placeholder.
        assert block.command == SERVER_FORCE_COMMAND + ' --reason "<why>"'
        assert "ens-media" in block.what
        assert "noust compliance disable ens-media" in block.how
        assert block.command in block.how

    def test_the_override_for_a_framework_works_and_is_put_on_record(
        self, real_config: Config
    ) -> None:
        real_config.set("terminal.server.enabled", True)
        real_config.set("security.frameworks", ["ens-media"])
        real_config.write()
        block = load_settings(real_config).server_block()
        assert block is not None

        result = run(block.command, input="")

        assert result.exit_code == 0, result.output
        assert load_settings(real_config).server_allowed is True

    def test_a_server_that_asks_for_a_reason_gets_commands_that_carry_one(
        self, real_config: Config
    ) -> None:
        real_config.set("security.change_reasons", True)

        block = load_settings(real_config).server_block()

        assert block is not None
        assert block.command == SERVER_ENABLE_COMMAND + ' --reason "<why>"'
        assert block.command in block.how

    def test_the_switch_comes_first_when_both_block_it(self, real_config: Config) -> None:
        real_config.set("security.server_terminal", "refused")

        block = load_settings(real_config).server_block()

        assert block is not None and block.code == "server_shell_off"

    def test_the_command_for_the_switch_works(self, real_config: Config) -> None:
        real_config.write()
        block = load_settings(real_config).server_block()
        assert block is not None

        result = run(block.command, input="")

        assert result.exit_code == 0, result.output
        assert load_settings(real_config).server_block() is None

    def test_the_old_pair_still_reads_the_same(self, real_config: Config) -> None:
        refusal = load_settings(real_config).server_refusal()

        assert refusal is not None
        what, how = refusal
        assert "off" in what and SERVER_ENABLE_COMMAND in how


class TestWhatTheApiSays:
    def test_the_status_carries_the_code_the_control_and_the_command(
        self, real_config: Config, tmp_path: Path
    ) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from noust.web.api import terminal as terminal_api
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers

        real_config.set("terminal.server.enabled", True)
        real_config.set("security.server_terminal", "refused")
        api = FastAPI()
        install_error_handlers(api)
        api.include_router(terminal_api.router, prefix="/api/terminal")
        api.dependency_overrides[get_current_session] = lambda: {
            "type": "session",
            "session_id": "s",
            "scope": "admin",
            "name": "alice",
        }

        body = TestClient(api).get("/api/terminal/status").json()["server"]

        assert body["allowed"] is False
        assert body["code"] == "server_shell_control"
        assert body["control"] == "server_terminal" and body["forced_by"] == []
        assert body["command"] == "noust compliance set server_terminal allowed"
        assert body["reason"] and body["how"]

    def test_a_server_that_may_open_it_says_nothing_of_a_block(
        self, real_config: Config, tmp_path: Path
    ) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from noust.web.api import terminal as terminal_api
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers

        real_config.set("terminal.server.enabled", True)
        api = FastAPI()
        install_error_handlers(api)
        api.include_router(terminal_api.router, prefix="/api/terminal")
        api.dependency_overrides[get_current_session] = lambda: {
            "type": "session",
            "session_id": "s",
            "scope": "admin",
            "name": "alice",
        }

        body = TestClient(api).get("/api/terminal/status").json()["server"]

        assert body["allowed"] is True
        assert body["code"] is None and body["command"] is None and body["forced_by"] == []
