# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a text somebody else wrote cannot do to the assistant, one rule at a time (item 171).

The battery that attacks it every time the code changes (instructions hidden in a journal, a build log, a
commit message, a branch name and a README, a model that obeys them, and what stopped each attempt) is data
in ``tests/fixtures/assistant_battery/injection`` and runs in ``test_assistant_battery_injection.py``. What
stays here are the rules it rests on, each on its own:

- the same call asked by the person, with nothing from outside read, is done where the policy lets it;
- once a conversation has read text from outside, what it is told to do afterwards is proposed, in the
  next turn too;
- a host the person wrote is theirs, and one they did not is not.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.accounts import passwords
from tests.support.assistant_lab import SHOP, FakeServices, Lab, build_lab
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
    with ScriptedModel(models=["tiny"], api_key="sk-provider-9d8c7b6a5f4e3d2c1b0a") as scripted:
        yield scripted


@pytest.fixture
def lab(sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel) -> Iterator[Lab]:
    yield from build_lab(sandbox, monkeypatch, model)


def test_the_same_call_asked_by_the_person_is_done_where_the_policy_lets_it(lab: Lab) -> None:
    # Control: the rule is about what was read, not about the tool. Asked by the person, with no text from
    # outside read, the policy's "alone" is what it says.
    lab.configure()
    lab.policy(("assistant", "production", "apps.operate", "alone"))
    lab.model.script(Reply.call("restart_application", {"domain": SHOP}), Reply.say("done"))
    turn = lab.ask(lab.conversation(), f"Please restart {SHOP}")
    assert lab.restarts == [f"restart {SHOP}"]
    assert [p["klass"] for p in turn["proposals"]] == ["alone"]


def test_a_conversation_that_read_outside_text_stays_marked_in_the_next_turn(lab: Lab) -> None:
    lab.configure()
    lab.policy(("assistant", "production", "apps.operate", "alone"))
    FakeServices.journal = "ready\n"
    conversation = lab.conversation()
    lab.model.script(Reply.call("application_logs", {"domain": SHOP}), Reply.say("looks fine"))
    lab.ask(conversation, "look at the journal")
    lab.model.script(Reply.call("restart_application", {"domain": SHOP}), Reply.say("proposed"))
    turn = lab.ask(conversation, f"now restart {SHOP}")
    assert lab.restarts == []
    assert [p["klass"] for p in turn["proposals"]] == ["ask"]
    assert turn["proposals"][0]["why"]["code"] == "assistant.proposal.why.read_outside"


def test_a_host_the_person_wrote_is_theirs_and_one_they_did_not_is_not(lab: Lab) -> None:
    lab.configure()
    conversation = lab.conversation()
    lab.model.script(
        Reply.call(
            "call_operation",
            {
                "method": "POST",
                "path": "/api/apps/{domain}/domains",
                "parameters": {"domain": SHOP},
                "body": {"domain": "www.example.com", "kind": "alias"},
            },
        ),
        Reply.say("proposed"),
    )
    turn = lab.ask(conversation, "Add the domain www.example.com to the shop")
    [proposal] = turn["proposals"]
    assert not [f for f in proposal["facts"] if f.get("foreign")]
