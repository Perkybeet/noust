# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the console is given of a proposal and of a turn has a shape, whatever was kept (item 171).

A proposal keeps what a call answered, what the plan route said and what the turn used as they came; what is
given to the console is one shape of each, so that its types name every part: the result is its words, a
sentence with a code or that it was cut; a plan is the one answer every plan has, whole, or none at all; the
notes of a turn's usage are sentences with their English beside them.
"""

from __future__ import annotations

from typing import Any

from noust.web.assistant import present

PLAN: dict[str, Any] = {
    "operation": "app.restart",
    "subject": "shop.example.com",
    "effect": {"code": "plans.restart", "params": {}, "text": "Restarts it.", "hint": None},
    "commands": [],
    "files": [],
    "steps": [],
    "findings": [],
    "unknown": [],
    "needs": {"elevation": False, "approval": False, "confirmations": []},
    "blocked": None,
    "asked_remote": False,
    "omitted": 0,
}


class TestWhatCameOfACall:
    def test_words_are_its_text(self) -> None:
        assert present.result_of("Restarted shop") == "Restarted shop"

    def test_a_sentence_with_a_code_is_one_with_its_english_beside_it(self) -> None:
        said = present.result_of({"code": "assistant.off", "params": {}})

        assert isinstance(said, dict)
        assert said["code"] == "assistant.off"
        assert said["text"]

    def test_what_a_card_cannot_say_is_not_given(self) -> None:
        # Nothing, nothing in words, what was too long to keep, and what a route answered.
        for held in (
            None,
            "",
            {"truncated": "{'a': 1..."},
            {"job_id": "j1", "status": "pending"},
            [1, 2],
            7,
        ):
            assert present.result_of(held) is None, held


class TestThePlan:
    def test_a_plan_is_given_whole(self) -> None:
        shown = present.plan_of(PLAN)

        assert shown is not None
        assert shown["operation"] == "app.restart"
        assert shown["needs"] == {"elevation": False, "approval": False, "confirmations": []}

    def test_an_answer_that_is_not_a_plan_is_not_shown_as_one(self) -> None:
        for held in (None, "a plan", {"domain": "shop.example.com", "status": "running"}, {}):
            assert present.plan_of(held) is None, held


class TestWhatATurnUsed:
    def test_the_notes_are_said_as_sentences(self) -> None:
        shown = present._usage(
            {
                "steps": 2,
                "tokens": 900,
                "spend": 0.0,
                "notes": [{"code": "assistant.limit.estimated", "params": {}}],
            }
        )

        assert shown["steps"] == 2
        [note] = shown["notes"]
        assert note["code"] == "assistant.limit.estimated" and note["text"]

    def test_a_turn_that_kept_no_notes_has_none_and_a_note_the_catalog_lost_is_kept_as_it_was(
        self,
    ) -> None:
        assert present._usage({"steps": 1})["notes"] == []
        [kept] = present._usage({"notes": [{"code": "no.such.sentence", "params": {}}]})["notes"]

        assert kept["code"] == "no.such.sentence"
