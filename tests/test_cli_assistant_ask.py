# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust assistant ask``: the same loop as the console, from the terminal (item 171).

Run as root, it names the account the assistant acts for and runs one turn against the console's own
application, with that account's assistant token. It explains and investigates; what it proposes is listed with
the call that would carry it out and is never carried out.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.accounts import AccountManager, passwords
from noust.core.config import Config
from noust.core.fs import RealFileSystem, get_fs, set_fs
from noust.core.store import App, NoustStore
from noust.web.api import apps as apps_api
from noust.web.api.apps import AppActionResponse
from noust.web.auth import STATE_DIR_ENV
from tests.support.assistant_lab import FakeServices
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets

PASSWORD = "correct horse battery staple"
KEY = "sk-provider-9d8c7b6a5f4e3d2c1b0a"
SHOP = "shop.example.com"


@pytest.fixture(autouse=True)
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[NoustStore]:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    (tmp_path / "etc").mkdir()
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    set_fs(RealFileSystem())
    Config.reset_instance()
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db")
    AccountManager(store).create("ops", "operator", password=PASSWORD)
    AccountManager(store).create("reader", "viewer", password=PASSWORD)
    store.create_app(App(domain=SHOP, app_path=str(tmp_path / SHOP), status="running"))
    monkeypatch.setattr(apps_api, "ServiceManager", FakeServices)
    monkeypatch.setattr(
        apps_api,
        "_service_action",
        lambda domain, verb, past: AppActionResponse(success=True, message="ok", domain=domain),
    )
    yield store
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


def turn_on(model: ScriptedModel) -> None:
    model.script(Reply.call("get_status", {"service": "probe"}))
    run(
        "assistant",
        "enable",
        "--base-url",
        model.base_url,
        "--model",
        "tiny",
        "--allow-private-host",
        "--key-stdin",
        input_text=f"{KEY}\n",
    )
    model.requests.clear()


def a_failed_deployment(store: NoustStore, tmp_path: Path) -> int:
    deployment = store.record_deployment_start(SHOP, "cli", git_commit="a3f9c21")
    store.finish_deployment(deployment, "failed", "build failed")
    directory = tmp_path / "deploy-logs"
    get_fs().make_dir(directory, mode=0o700, parents=True)
    path = directory / f"{deployment}.log"
    get_fs().write_text(
        path, "building\nError: variable X is missing\nnpm error code 1\n", mode=0o600
    )
    store.annotate_deployment(deployment, log_path=str(path))
    return deployment


class TestAsking:
    def test_it_explains_a_page_and_quotes_the_error(
        self, model: ScriptedModel, setup: NoustStore, tmp_path: Path
    ) -> None:
        turn_on(model)
        deployment = a_failed_deployment(setup, tmp_path)
        model.script(Reply.say("The variable is missing.\n[[S2:2]]\nSet it."))
        output = run(
            "assistant", "ask", "--as", "ops", "--page", f"deployment:{deployment}", "Explain this"
        )

        assert "The variable is missing." in output
        assert "Set it." in output
        assert "Read the build log of deployment" in output
        assert "(An AI: tiny;" in output

    def test_json_is_the_turn_as_the_console_draws_it(
        self, model: ScriptedModel, setup: NoustStore, tmp_path: Path
    ) -> None:
        turn_on(model)
        deployment = a_failed_deployment(setup, tmp_path)
        model.script(Reply.say("Missing.\n[[S2:2]]"))
        document = json.loads(
            run(
                "--json",
                "assistant",
                "ask",
                "--as",
                "ops",
                "--page",
                f"deployment:{deployment}",
                "Explain this",
            )
        )
        assert document["state"] == "done"
        literal = next(p for p in document["parts"] if p["type"] == "literal")
        assert literal["lines"] == [{"n": 2, "text": "Error: variable X is missing"}]

    def test_what_it_proposes_is_listed_with_its_call_and_is_not_done(
        self, model: ScriptedModel
    ) -> None:
        turn_on(model)
        model.script(Reply.call("restart_application", {"domain": SHOP}), Reply.say("proposed"))
        output = run("assistant", "ask", "--as", "ops", f"restart {SHOP}")

        assert "Proposed, NOT done (ask): Restart shop.example.com" in output
        assert f"call: POST /api/apps/{SHOP}/restart" in output
        assert "Carry it out in the console" in output

    def test_it_is_asked_as_an_account_and_held_to_what_the_account_holds(
        self, model: ScriptedModel
    ) -> None:
        turn_on(model)
        output = run("assistant", "ask", "--as", "reader", "hello", code=1)
        assert "does not allow using the assistant" in output
        output = run("assistant", "ask", "--as", "nobody", "hello", code=1)
        assert "nobody" in output

    def test_off_it_says_so_and_asks_nothing(self, model: ScriptedModel) -> None:
        output = run("assistant", "ask", "--as", "ops", "hello", code=1)
        assert "The assistant is off" in output
        assert model.requests == []

    def test_a_page_it_cannot_read_is_refused(self, model: ScriptedModel) -> None:
        turn_on(model)
        run("assistant", "ask", "--as", "ops", "--page", "shell:rm", "hello", code=1)
        assert model.requests == []

    def test_what_it_acted_with_is_taken_back_when_the_answer_ends(
        self, model: ScriptedModel, setup: NoustStore
    ) -> None:
        turn_on(model)
        model.script(Reply.say("hello"))
        run("assistant", "ask", "--as", "ops", "hello")
        from noust.web.server import get_token_manager

        held = [
            t
            for t in get_token_manager().sessions.list_api_tokens()
            if str(t.get("name", "")).startswith("assistant:")
        ]
        assert held and all(t.get("revoked_at") for t in held)
