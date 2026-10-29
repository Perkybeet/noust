# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a person is named when a central acted for them.

A node's audit trail, its alerts and its history name whoever acted. When it was an operator of
a central, the label read "fleet-fra-1 on behalf of marta": the credential first and the person
after, in English in a console that speaks Spanish. The person goes first and the central
beside them, in a sentence the console translates (``actors.through_central``).
"""

from __future__ import annotations

import pytest

from noust.core.audit.actor import Actor, actor_person, actor_sentence, actor_words
from noust.core.fleet_tokens import central_of_token
from noust.core.server_text import CATALOG, say


class TestTheSentence:
    def test_a_person_acting_through_a_central_is_named_first(self) -> None:
        sentence = actor_sentence("fleet-fra-1 on behalf of marta")

        assert sentence == say("actors.through_central", person="marta", central="fra-1")
        assert sentence is not None
        assert sentence.text == "marta (from the central fra-1)"

    @pytest.mark.parametrize(
        "label",
        [
            "fleet-fra-1.2 on behalf of marta",  # a token issued again
            "fleet:fleet-fra-1 on behalf of marta",  # the channel the audit trail spells
        ],
    )
    def test_the_central_is_the_one_the_token_names(self, label: str) -> None:
        sentence = actor_sentence(label)

        assert sentence is not None
        assert sentence.params == {"person": "marta", "central": "fra-1"}

    def test_a_token_that_names_no_central_is_named_whole(self) -> None:
        sentence = actor_sentence("ci-bot on behalf of marta")

        assert sentence is not None
        assert sentence.params == {"person": "marta", "central": "ci-bot"}

    @pytest.mark.parametrize("label", ["marta", "token:ci", "master", "cli:root", "", None])
    def test_nobody_else_has_a_sentence(self, label: str | None) -> None:
        assert actor_sentence(label) is None

    def test_an_actor_says_the_same_as_its_label(self) -> None:
        actor = Actor.from_label("fleet-fra-1 on behalf of marta")

        assert actor.sentence == actor_sentence("fleet-fra-1 on behalf of marta")
        assert Actor(kind="user", name="marta").sentence is None

    def test_the_sentence_is_in_the_catalog_the_console_is_held_to(self) -> None:
        assert CATALOG["actors.through_central"] == "{person} (from the central {central})"


class TestWhoIsNamedFirst:
    def test_in_english_for_a_terminal(self) -> None:
        assert actor_words("fleet-fra-1 on behalf of marta") == "marta (from the central fra-1)"
        assert actor_words("token:ci") == "token:ci"
        assert actor_words(None) == ""

    def test_the_person_behind_a_central_and_everybody_else_as_they_are(self) -> None:
        assert actor_person("fleet-fra-1 on behalf of marta") == "marta"
        assert actor_person("marta") == "marta"
        assert actor_person("token:ci") == "token:ci"
        assert actor_person(None) == ""


class TestWhatATokenSaysOfItsCentral:
    @pytest.mark.parametrize(
        ("token", "central"),
        [("fleet-nas", "nas"), ("fleet-nas.2", "nas"), ("fleet-fra-1", "fra-1")],
    )
    def test_the_central(self, token: str, central: str) -> None:
        assert central_of_token(token) == central

    @pytest.mark.parametrize("token", ["ci", "fleet-", "fleet-Nas", "", None])
    def test_not_a_fleet_token(self, token: str | None) -> None:
        assert central_of_token(token) is None
