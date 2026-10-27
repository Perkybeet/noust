# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Why the terminal on the server itself cannot be opened, and the command that changes it.

The console blamed the ENS profile and proposed ``noust config set terminal.server.force_ens true`` on
a server with no ENS, where what keeps it off is the control ``server_terminal`` set to ``refused``.
The server says what blocks it as a sentence with a code (``noust.core.server_text``), whose values
are the control, the frameworks and the exact command, and a test runs each command it names.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import audit_policy
from noust.cli.app import cli
from noust.core.audit import Actor
from noust.core.config import Config
from noust.core.store import NoustStore, get_store
from noust.terminal.sessions import (
    SessionRegistry,
    TerminalDisabledError,
    TerminalLimitError,
)
from noust.terminal.settings import (
    SERVER_ENABLE_COMMAND,
    SERVER_FORCE_COMMAND,
    TERMINALS_ENABLE_COMMAND,
    TerminalSettings,
    load_settings,
)
from noust.terminal.targets import KIND_APP, KIND_SERVER


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
        assert block.code == "terminal.server_off"
        assert block.params == {"command": SERVER_ENABLE_COMMAND}
        assert "terminal.server.enabled" in block.text
        assert SERVER_ENABLE_COMMAND in (block.hint or "")

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
        assert block.code == "terminal.server_control"
        assert block.params == {
            "control": "server_terminal",
            "command": "noust compliance set server_terminal allowed",
        }
        assert "server_terminal" in block.text
        said = block.text + (block.hint or "")
        assert "ENS" not in said and "profile" not in said
        assert block.params["command"] in (block.hint or "")

    def test_the_command_for_the_control_works(self, real_config: Config) -> None:
        real_config.set("terminal.server.enabled", True)
        real_config.set("security.server_terminal", "refused")
        real_config.write()
        block = load_settings(real_config).server_block()
        assert block is not None

        result = run(str(block.params["command"]))

        assert result.exit_code == 0, result.output
        assert load_settings(real_config).server_allowed is True

    def test_a_framework_that_asks_for_it_is_named(self, real_config: Config) -> None:
        real_config.set("terminal.server.enabled", True)
        real_config.set("security.frameworks", ["ens-media"])

        block = load_settings(real_config).server_block()

        assert block is not None
        assert block.code == "terminal.server_framework"
        # ENS asks for a reason with every change, so what is printed carries the placeholder.
        assert block.params["command"] == SERVER_FORCE_COMMAND + ' --reason "<why>"'
        assert block.params["control"] == "server_terminal"
        assert block.params["frameworks"] == "ens-media"
        assert "ens-media" in block.text
        assert "noust compliance disable ens-media" in str(block.params["turn_off_command"])
        assert block.params["command"] in (block.hint or "")
        assert block.params["turn_off_command"] in (block.hint or "")

    def test_several_frameworks_are_all_named(self, real_config: Config) -> None:
        real_config.set("terminal.server.enabled", True)
        real_config.set("security.frameworks", ["ens-media", "iso-27001"])

        block = load_settings(real_config).server_block()

        assert block is not None and block.code == "terminal.server_framework"
        assert block.params["frameworks"] == "ens-media, iso-27001"

    def test_the_override_for_a_framework_works_and_is_put_on_record(
        self, real_config: Config
    ) -> None:
        real_config.set("terminal.server.enabled", True)
        real_config.set("security.frameworks", ["ens-media"])
        real_config.write()
        block = load_settings(real_config).server_block()
        assert block is not None

        result = run(str(block.params["command"]), input="")

        assert result.exit_code == 0, result.output
        assert load_settings(real_config).server_allowed is True

    def test_the_command_that_turns_the_framework_off_works(self, real_config: Config) -> None:
        real_config.set("terminal.server.enabled", True)
        real_config.write()
        assert run("noust compliance enable ens-media --yes").exit_code == 0
        block = load_settings(real_config).server_block()
        assert block is not None and block.code == "terminal.server_framework"

        result = run(str(block.params["turn_off_command"]))

        assert result.exit_code == 0, result.output
        assert load_settings(real_config).server_block() is None

    def test_a_server_that_asks_for_a_reason_gets_commands_that_carry_one(
        self, real_config: Config
    ) -> None:
        real_config.set("security.change_reasons", True)

        block = load_settings(real_config).server_block()

        assert block is not None
        assert block.params["command"] == SERVER_ENABLE_COMMAND + ' --reason "<why>"'
        assert block.params["command"] in (block.hint or "")

    def test_the_switch_comes_first_when_both_block_it(self, real_config: Config) -> None:
        real_config.set("security.server_terminal", "refused")

        block = load_settings(real_config).server_block()

        assert block is not None and block.code == "terminal.server_off"

    def test_the_command_for_the_switch_works(self, real_config: Config) -> None:
        real_config.write()
        block = load_settings(real_config).server_block()
        assert block is not None

        result = run(str(block.params["command"]), input="")

        assert result.exit_code == 0, result.output
        assert load_settings(real_config).server_block() is None

    def test_the_old_pair_still_reads_the_same(self, real_config: Config) -> None:
        refusal = load_settings(real_config).server_refusal()

        assert refusal is not None
        what, how = refusal
        assert "off" in what and SERVER_ENABLE_COMMAND in how


class TestTheRefusalsOfTheRegistry:
    """What a ticket is refused with is the same sentence, so the console can translate it."""

    @staticmethod
    def registry(**overrides: Any) -> SessionRegistry:
        settings = replace(TerminalSettings(), **overrides)
        return SessionRegistry(settings=lambda: settings, runner=lambda: None)  # type: ignore[arg-type,return-value]

    @staticmethod
    def open_for(registry: SessionRegistry, actor: str) -> None:
        # Only who holds the session matters to the limits.
        registry._sessions["x"] = SimpleNamespace(grant=SimpleNamespace(actor=actor))  # type: ignore[assignment]

    def test_the_server_shell_that_is_off_carries_its_sentence(self) -> None:
        with pytest.raises(TerminalDisabledError) as caught:
            self.registry().check_room("alice", kind=KIND_SERVER)

        assert caught.value.coded is not None
        assert caught.value.coded.code == "terminal.server_off"
        assert caught.value.message == caught.value.coded.text
        assert caught.value.details == caught.value.coded.hint

    def test_terminals_that_are_off_name_a_command_that_works_as_printed(self) -> None:
        registry = self.registry(enabled=False, reason_suffix=' --reason "<why>"')

        with pytest.raises(TerminalDisabledError) as caught:
            registry.check_room("alice", kind=KIND_APP)

        coded = caught.value.coded
        assert coded is not None and coded.code == "terminal.disabled"
        assert coded.params == {"command": TERMINALS_ENABLE_COMMAND + ' --reason "<why>"'}
        assert str(coded.params["command"]) in caught.value.details

    def test_the_command_for_terminals_works(self, real_config: Config) -> None:
        real_config.set("terminal.enabled", False)
        real_config.write()

        result = run(TERMINALS_ENABLE_COMMAND, input="")

        assert result.exit_code == 0, result.output
        assert load_settings(real_config).enabled is True

    def test_a_limit_reached_says_how_many_are_open(self) -> None:
        registry = self.registry(max_sessions=1)
        self.open_for(registry, "bob")

        with pytest.raises(TerminalLimitError) as caught:
            registry.check_room("alice", kind=KIND_APP)

        coded = caught.value.coded
        assert coded is not None and coded.code == "terminal.limit_total"
        assert coded.params == {"open": 1}
        assert caught.value.message == coded.text

    def test_a_limit_of_one_person_says_how_many_they_have(self) -> None:
        registry = self.registry(max_sessions=8, max_sessions_per_user=1)
        self.open_for(registry, "alice")

        with pytest.raises(TerminalLimitError) as caught:
            registry.check_room("alice", kind=KIND_APP)

        coded = caught.value.coded
        assert coded is not None and coded.code == "terminal.limit_user"
        assert coded.params == {"open": 1}


def api_client() -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from noust.web.api import terminal as terminal_api
    from noust.web.api.auth import get_current_session
    from noust.web.api.deps import install_error_handlers

    api = FastAPI()
    install_error_handlers(api)
    api.include_router(terminal_api.router, prefix="/api/terminal")
    api.dependency_overrides[get_current_session] = lambda: {
        "type": "session",
        "session_id": "s",
        "scope": "admin",
        "name": "alice",
    }
    return TestClient(api)


class TestWhatTheApiSays:
    def test_the_status_carries_the_sentence_with_its_code_and_values(
        self, real_config: Config
    ) -> None:
        real_config.set("terminal.server.enabled", True)
        real_config.set("security.server_terminal", "refused")

        body = api_client().get("/api/terminal/status").json()["server"]

        assert body["allowed"] is False
        assert body["coded"] == {
            "code": "terminal.server_control",
            "params": {
                "control": "server_terminal",
                "command": "noust compliance set server_terminal allowed",
            },
        }
        # What a client that knows no codes reads is the same sentence in English.
        assert "server_terminal" in body["reason"]
        assert "noust compliance set server_terminal allowed" in body["how"]

    def test_the_fields_that_were_never_released_are_gone(self, real_config: Config) -> None:
        real_config.set("terminal.server.enabled", True)
        real_config.set("security.server_terminal", "refused")

        body = api_client().get("/api/terminal/status").json()["server"]

        assert set(body) == {"allowed", "enabled", "reason", "how", "coded"}

    def test_a_server_that_may_open_it_says_nothing_of_a_block(self, real_config: Config) -> None:
        real_config.set("terminal.server.enabled", True)

        body = api_client().get("/api/terminal/status").json()["server"]

        assert body["allowed"] is True
        assert body["coded"] is None and body["reason"] is None and body["how"] is None
