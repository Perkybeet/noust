# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How many answers one person may have being written at once (item 171).

The turns that run at once are the server's: a handful, because each is a thread and a way of spending money. Without
a share for each person, one account that asks in several conversations at the same time holds all of them and the
others are told the assistant is busy. So the registry holds each account to two, and says so in a sentence of its
own, which is not the one that says the whole server is busy.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.accounts import passwords
from noust.core.exceptions import AssistantBusyError
from noust.web.assistant import turns
from noust.web.assistant.turns import MAX_PER_ACCOUNT, TurnRegistry
from tests.support.assistant_lab import KEY, Lab, build_lab
from tests.support.scripted_model import ScriptedModel


class Waiting:
    """A turn that runs until it is let go: only what the registry reads of one."""

    def __init__(self, turn_id: str, account: int, conversation: str) -> None:
        self.turn = SimpleNamespace(id=turn_id)
        self.ctx = SimpleNamespace(
            account=SimpleNamespace(id=account), conversation_id=conversation
        )
        self.cancel = threading.Event()
        self.release = threading.Event()

    def run(self) -> None:
        self.release.wait(10)


@pytest.fixture
def registry() -> Iterator[TurnRegistry]:
    made = TurnRegistry(limit=4)
    held: list[Waiting] = []
    made.held = held  # type: ignore[attr-defined]
    yield made
    for runner in held:
        runner.release.set()


def begin(registry: TurnRegistry, turn_id: str, account: int, conversation: str) -> Waiting:
    runner = Waiting(turn_id, account, conversation)
    registry.held.append(runner)  # type: ignore[attr-defined]
    registry.start(runner)  # type: ignore[arg-type]
    return runner


class TestAShareForEachPerson:
    def test_it_is_a_small_number(self) -> None:
        assert MAX_PER_ACCOUNT == 2

    def test_a_third_answer_of_the_same_account_waits_and_says_why(
        self, registry: TurnRegistry
    ) -> None:
        begin(registry, "t1", 7, "c1")
        begin(registry, "t2", 7, "c2")

        with pytest.raises(AssistantBusyError) as refused:
            begin(registry, "t3", 7, "c3")

        assert refused.value.coded.code == "assistant.turn.account_busy"
        assert refused.value.limit == MAX_PER_ACCOUNT
        assert refused.value.coded.hint

    def test_another_account_is_still_answered_while_one_holds_its_share(
        self, registry: TurnRegistry
    ) -> None:
        begin(registry, "t1", 7, "c1")
        begin(registry, "t2", 7, "c2")

        begin(registry, "t3", 8, "c3")
        begin(registry, "t4", 8, "c4")

        assert registry.alive() == {"t1", "t2", "t3", "t4"}

    def test_the_whole_server_is_still_held_to_its_own_limit_in_its_own_words(
        self, registry: TurnRegistry
    ) -> None:
        for number, account in enumerate((1, 1, 2, 2)):
            begin(registry, f"t{number}", account, f"c{number}")

        with pytest.raises(AssistantBusyError) as refused:
            begin(registry, "t9", 3, "c9")

        assert refused.value.coded.code == "assistant.turn.busy"

    def test_a_share_comes_back_when_an_answer_ends(self, registry: TurnRegistry) -> None:
        first = begin(registry, "t1", 7, "c1")
        begin(registry, "t2", 7, "c2")
        first.release.set()
        assert registry.wait("t1", 5)

        begin(registry, "t3", 7, "c3")


@pytest.fixture
def cheap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


class TestWhatThePersonIsTold:
    pytestmark = pytest.mark.allow_sockets

    @pytest.fixture(autouse=True)
    def real_config(self, real_config: Any) -> Any:
        return real_config

    @pytest.fixture
    def model(self) -> Iterator[ScriptedModel]:
        with ScriptedModel(models=["tiny"], api_key=KEY) as scripted:
            yield scripted

    @pytest.fixture
    def lab(
        self, cheap: None, sandbox: Path, monkeypatch: pytest.MonkeyPatch, model: ScriptedModel
    ) -> Iterator[Lab]:
        yield from build_lab(sandbox, monkeypatch, model)

    def test_it_is_a_429_with_the_sentence_of_the_share_and_the_turn_is_not_left_running(
        self, lab: Lab, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lab.configure()
        monkeypatch.setattr(turns, "MAX_PER_ACCOUNT", 0)

        refused = lab.begin(lab.conversation(), "hello")

        assert refused.status_code == 429, refused.text
        assert refused.json()["coded"]["code"] == "assistant.turn.account_busy"
