# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The loop under its limits, and what turning the assistant off does to a turn that runs (item 171).

A limit is asked before every step and cuts between two steps: the step that crossed it is whole and written
down, and the next one is not made. The warning comes at 80 %. The hour's limit on changes turns a change the
policy would let the assistant make into a proposal. Turning the assistant off ends the turn that runs, with
the sentence that says so, and nothing it tries afterwards works.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.web.assistant.turns import registry
from tests.support.assistant_lab import KEY, SHOP, Lab, build_lab
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


def loop_of(count: int, **options: Any) -> list[Reply]:
    return [Reply.call("list_applications", {}, **options) for _ in range(count)]


class TestTheLimits:
    def test_the_21st_step_is_not_made(self, lab: Lab) -> None:
        lab.configure()
        lab.model.script(*loop_of(30, usage=(100, 10)))
        turn = lab.ask(lab.conversation(), "keep looking")

        assert turn["state"] == "stopped"
        assert turn["stop"]["code"] == "assistant.limit.steps"
        assert turn["stop"]["params"] == {"limit": 20}
        assert turn["stop"]["hint"]
        assert len(lab.requests()) == 20
        assert turn["usage"]["steps"] == 20
        # The 20 steps that were made are whole: each read happened.
        assert len([s for s in turn["steps"] if s["state"] == "done"]) == 20
        events = [e for e in lab.audit_events() if e.get("action") == "assistant.limit"]
        assert len(events) == 1 and events[0]["result"] != "ok"

    def test_the_warning_comes_at_80_percent(self, lab: Lab) -> None:
        lab.configure(limits={"steps_per_turn": 5})
        lab.model.script(*loop_of(4, usage=(100, 10)), Reply.say("done"))
        turn = lab.ask(lab.conversation(), "look")

        assert turn["state"] == "done"
        assert [w["code"] for w in turn["warnings"]] == ["assistant.limit.steps_near"]
        assert any(e["type"] == "warning" for e in lab.events())

    def test_the_tokens_of_a_conversation_stop_it_between_steps(self, lab: Lab) -> None:
        lab.configure(limits={"input_tokens_per_conversation": 20000})
        lab.model.script(*loop_of(10, usage=(15000, 10)))
        turn = lab.ask(lab.conversation(), "keep looking")

        assert turn["state"] == "stopped"
        assert turn["stop"]["code"] == "assistant.limit.input_tokens"
        assert len(lab.requests()) == 2

    def test_the_spending_of_a_conversation_stops_it_and_a_model_with_no_price_is_said_to_have_none(
        self, lab: Lab
    ) -> None:
        lab.configure(
            prices={"tiny": {"input_per_million": 1_000_000.0, "output_per_million": 0.0}},
            limits={"spend_per_conversation": 1.0},
        )
        lab.model.script(*loop_of(10, usage=(2000, 10)))
        turn = lab.ask(lab.conversation(), "keep looking")
        assert turn["stop"]["code"] == "assistant.limit.conversation_spend"
        assert len(lab.requests()) == 1

    def test_without_a_price_the_person_is_told_there_is_no_spending_limit(self, lab: Lab) -> None:
        lab.configure()
        lab.model.script(Reply.say("hello"))
        turn = lab.ask(lab.conversation(), "hello")
        assert [n["code"] for n in turn["usage"]["notes"]] == ["assistant.limit.no_price"]

    def test_the_hours_limit_on_changes_turns_a_change_into_a_proposal(self, lab: Lab) -> None:
        lab.configure(limits={"actions_per_hour": 1})
        lab.policy(("assistant", "production", "apps.operate", "alone"))
        lab.model.script(
            Reply.call("restart_application", {"domain": SHOP}),
            Reply.call("restart_application", {"domain": SHOP}),
            Reply.say("one made, one proposed"),
        )
        turn = lab.ask(lab.conversation(), f"restart {SHOP} twice")

        assert lab.restarts == [f"restart {SHOP}"]
        assert [(p["klass"], p["state"]) for p in turn["proposals"]] == [
            ("alone", "done"),
            ("ask", "waiting"),
        ]
        assert turn["proposals"][1]["why"]["code"] == "assistant.proposal.why.actions_limit"
        assert any(e.get("action") == "assistant.limit" for e in lab.audit_events())


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
