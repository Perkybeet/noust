# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust assistant``: setting the built-in assistant up from a shell, with a key that never touches
the command line (item 171).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.accounts import AccountManager, passwords
from noust.core.config import Config
from noust.core.fs import RealFileSystem, set_fs
from noust.core.store import NoustStore
from noust.web.assistant.identity import AssistantIdentity
from noust.web.auth import STATE_DIR_ENV, SecurityConfig, TokenManager
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets

PASSWORD = "correct horse battery staple"
KEY = "sk-provider-9d8c7b6a5f4e3d2c1b0a"
GOOD = Reply.call("get_status", {"service": "probe"})


@pytest.fixture(autouse=True)
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    (tmp_path / "etc").mkdir()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    monkeypatch.delenv("NOUST_ASSISTANT_API_KEY", raising=False)
    set_fs(RealFileSystem())
    Config.reset_instance()
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    AccountManager(store).create("ops", "operator", password=PASSWORD)
    yield
    Config.reset_instance()
    NoustStore.reset_instance()
    set_fs(None)


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny"], api_key=KEY) as scripted:
        yield scripted


def run(*args: str, code: int = 0, input_text: str | None = None) -> str:
    result = CliRunner().invoke(root_cli, list(args), input=input_text)
    output = f"{result.output}{result.exception or ''}"
    assert result.exit_code == code, output
    return output


def configure_args(model: ScriptedModel, *more: str) -> list[str]:
    return [
        "assistant",
        "configure",
        "--base-url",
        model.base_url,
        "--model",
        "tiny",
        "--allow-private-host",
        "--key-stdin",
        *more,
    ]


def status() -> dict[str, object]:
    return json.loads(run("--json", "assistant", "status"))


class TestOffByDefault:
    def test_nothing_is_chosen_and_the_status_says_how_to_begin(self) -> None:
        document = status()
        assert document["enabled"] is False
        assert document["configured"] is False
        assert document["has_key"] is False
        assert "ollama" in document["presets"]  # type: ignore[operator]
        assert "api_key" not in document
        assert "enable --preset" in run("assistant", "status")


class TestConfiguring:
    def test_the_key_comes_in_on_standard_input_and_is_probed_sealed_and_never_shown(
        self, model: ScriptedModel
    ) -> None:
        model.script(GOOD)
        output = run(*configure_args(model), input_text=f"{KEY}\n")
        assert KEY not in output
        assert "answers" in output
        assert "called the test call to a tool correctly" in output or "correctly" in output
        document = status()
        assert document["has_key"] is True
        assert document["location"] == "machine"
        assert document["calls_tools"] is True
        assert KEY not in json.dumps(document)
        assert model.chat_requests()[0].headers["authorization"] == f"Bearer {KEY}"

    def test_the_key_may_come_from_the_environment_too(
        self, model: ScriptedModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model.script(GOOD)
        monkeypatch.setenv("NOUST_ASSISTANT_API_KEY", KEY)
        args = [a for a in configure_args(model) if a != "--key-stdin"]
        run(*args)
        assert status()["has_key"] is True

    def test_there_is_no_way_to_give_it_as_an_argument(self, model: ScriptedModel) -> None:
        for option in ("--key", "--api-key", "--token"):
            run("assistant", "configure", option, KEY, code=2)

    def test_an_empty_standard_input_is_said_not_taken_as_a_key(self, model: ScriptedModel) -> None:
        output = run(*configure_args(model), code=2, input_text="")
        assert "standard input" in output

    def test_an_address_of_ones_own_needs_the_consent_and_without_it_nothing_is_saved(
        self, model: ScriptedModel
    ) -> None:
        args = [a for a in configure_args(model) if a != "--allow-private-host"]
        output = run(*args, code=1, input_text=f"{KEY}\n")
        assert "security.allow_private_hosts" in output
        assert status()["configured"] is False
        assert model.requests == []

    def test_a_limit_and_a_price_are_set_and_one_that_is_no_limit_is_refused(
        self, model: ScriptedModel
    ) -> None:
        run(
            "assistant",
            "configure",
            "--limit",
            "steps_per_turn=9",
            "--price",
            "tiny=0.5,1.5",
            "--currency",
            "EUR",
        )
        document = status()
        assert document["limits"]["steps_per_turn"] == 9  # type: ignore[index]
        assert document["prices"] == {"tiny": {"input_per_million": 0.5, "output_per_million": 1.5}}
        assert document["currency"] == "EUR"
        run("assistant", "configure", "--limit", "steps_per_turn=0", code=1)
        run("assistant", "configure", "--limit", "steps_per_turn", code=2)
        run("assistant", "configure", "--price", "tiny=0.5", code=2)


class TestTurningItOnAndOff:
    def test_it_is_probed_and_turned_on_and_off(self, model: ScriptedModel) -> None:
        model.script(GOOD)
        output = run(
            *[a.replace("configure", "enable") for a in configure_args(model)],
            input_text=f"{KEY}\n",
        )
        assert "The assistant is on" in output
        assert status()["enabled"] is True
        run("assistant", "disable")
        assert status()["enabled"] is False

    def test_it_is_not_turned_on_by_a_provider_that_does_not_answer(self) -> None:
        with ScriptedModel() as gone:
            url = gone.base_url
        output = run(
            "assistant",
            "enable",
            "--base-url",
            url,
            "--model",
            "tiny",
            "--allow-private-host",
            code=1,
        )
        assert "probe" in output.lower()
        assert status()["enabled"] is False

    def test_turning_it_off_revokes_every_token_it_held(
        self, model: ScriptedModel, tmp_path: Path
    ) -> None:
        manager = TokenManager(SecurityConfig(state_dir=tmp_path / "state"))
        run("assistant", "disable")  # nothing held yet: not an error
        account = manager.accounts.find("ops")
        issued = AssistantIdentity(manager).issue(account, "a" * 32)
        assert manager.verify_api_token(issued.token, "10.0.0.1") is not None
        output = run("assistant", "disable")
        assert "1 token(s)" in output
        assert manager.verify_api_token(issued.token, "10.0.0.1") is None

    def test_a_hub_turns_it_on_like_any_other(self, model: ScriptedModel) -> None:
        # A central that deploys nothing has servers to look at: the assistant is the console's.
        Config().set("central.role", "hub")
        Config().write()
        model.script(GOOD)
        run(
            *[a.replace("configure", "enable") for a in configure_args(model)],
            input_text=f"{KEY}\n",
        )
        assert status()["enabled"] is True


class TestProbeAndUsage:
    def test_the_probe_says_what_it_found_and_in_json(self, model: ScriptedModel) -> None:
        run(*configure_args(model), "--no-probe", input_text=f"{KEY}\n")
        model.script(Reply.say("I would rather talk."))
        document = json.loads(run("--json", "assistant", "probe"))
        assert document["reachable"] is True
        assert document["tools"] is False
        assert any(s["code"] == "assistant.probe.no_tools" for s in document["sentences"])

    def test_there_is_nothing_to_probe_before_a_provider_is_chosen(self) -> None:
        output = run("assistant", "probe", code=1)
        assert "Choose a provider" in output

    def test_the_usage_is_read_only_and_says_what_the_month_cost(self) -> None:
        output = run("assistant", "usage")
        assert "0 step(s)" in output
        assert "Limits:" in output
        document = json.loads(run("--json", "assistant", "usage"))
        assert document["month"]["steps"] == 0
        assert document["limits"]["actions_per_hour"] == 10
