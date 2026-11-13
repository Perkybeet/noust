# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How long a turn may take, and what turning the assistant off does to a turn that runs (item 171).

The limits themselves (the steps, the tokens, the spending of a conversation and of the month, the hour's
changes and the warning at 80 %) are scenarios of the battery, in ``tests/fixtures/assistant_battery/limits``.
What stays here is what is not a number the operator changes: a turn is cut after fifteen minutes whatever its
steps, and turning the assistant off ends the turn that runs, with the sentence that says so, and nothing it
tries afterwards works.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.web.assistant.turns import registry
from tests.support.assistant_lab import KEY, Lab, build_lab
from tests.support.scripted_model import Reply, ScriptedModel

pytestmark = pytest.mark.allow_sockets


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def real_config(real_config: Any) -> Any:
    return real_config


@pytest.fixture
def model() -> Iterator[ScriptedModel]:
    with ScriptedModel(models=["tiny"], api_key=KEY) as scripted:
        yield scripted


@pytest.fixture
def lab(sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel) -> Iterator[Lab]:
    yield from build_lab(sandbox, monkeypatch, model)


class TestHowLongATurnMayTake:
    def test_a_turn_that_runs_too_long_is_stopped_between_steps(
        self, lab: Lab, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.assistant import loop

        lab.configure()
        monkeypatch.setattr(loop, "TURN_SECONDS", 0)
        lab.model.script(Reply.say("never asked"))
        turn = lab.ask(lab.conversation(), "hello")
        assert turn["state"] == "stopped"
        assert turn["stop"]["code"] == "assistant.turn.too_long"
        assert lab.requests() == []


class TestTurningItOffWhileItRuns:
    def test_the_turn_that_runs_ends_with_the_sentence_that_says_why(self, lab: Lab) -> None:
        lab.configure()
        lab.model.script(Reply.call("list_applications", {}, delay=2.0), Reply.say("late"))
        conversation = lab.conversation()
        started = lab.begin(conversation, "look").json()
        lab.marta.elevate()
        response = lab.marta.post("/api/assistant/off")
        assert response.status_code == 200
        assert registry().wait(started["turn"], 30)

        turn = lab.turn(conversation, started["turn"])
        assert turn["state"] == "stopped"
        assert turn["stop"]["code"] == "assistant.off"
        # Nothing it tried afterwards worked: no call reached the API.
        assert lab.bridge.calls == []

    def test_a_question_asked_after_it_is_refused(self, lab: Lab) -> None:
        lab.configure()
        conversation = lab.conversation()
        lab.marta.elevate()
        lab.marta.post("/api/assistant/off")
        response = lab.begin(conversation, "hello")
        assert response.status_code == 403
        assert response.json()["coded"]["code"] == "assistant.unavailable.off"

    def test_a_token_taken_back_in_the_middle_is_the_assistant_off_and_not_an_error(
        self, lab: Lab
    ) -> None:
        lab.configure()
        lab.model.script(Reply.call("list_applications", {}))
        conversation = lab.conversation()
        started = lab.begin(conversation, "look").json()
        lab.marta.elevate()
        lab.marta.post("/api/assistant/off")
        assert registry().wait(started["turn"], 30)
        assert lab.turn(conversation, started["turn"])["stop"]["code"] == "assistant.off"
