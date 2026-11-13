# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who is calling, in three classes the server deduces from the credential (item 184).

A person (a session of the console or of the command line, the master token, a central acting for its
operator), a token (CI, a script, a client of MCP) and the built-in assistant (a token of its own,
marked when it is issued). What the client says of itself decides nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core import callers
from noust.core.callers import ASSISTANT, PERSON, TOKEN


def _token(**extra: Any) -> dict[str, Any]:
    return {
        "type": "api_token",
        "sid": "token:ci",
        "token_name": "ci",
        "token_id": 7,
        "owner_account_id": 3,
        "permissions": frozenset({"apps.deploy"}),
        **extra,
    }


class TestTheClasses:
    def test_a_session_of_an_account_is_a_person(self) -> None:
        payload = {"type": "session", "account_id": 3, "username": "marta", "role": "admin"}
        assert callers.caller_of(payload) == PERSON

    def test_the_master_token_is_a_person(self) -> None:
        assert callers.caller_of({"type": "session", "sid": "master"}) == PERSON
        assert callers.caller_of({"type": "master"}) == PERSON

    def test_a_central_acting_for_its_operator_is_a_person(self) -> None:
        # The central asked its own operator and vouches for the call: the node's own guard
        # (a fleet token needs the central's word) is what judges it.
        payload = {
            "fleet": True,
            "scope": "fleet",
            "token_name": "fleet-fra",
            "on_behalf_of": "marta",
        }
        assert callers.caller_of(payload) == PERSON

    def test_an_api_token_is_a_token(self) -> None:
        assert callers.caller_of(_token()) == TOKEN

    def test_a_token_marked_for_the_assistant_is_the_assistant(self) -> None:
        assert callers.caller_of(_token(caller="assistant")) == ASSISTANT

    def test_a_token_marked_as_a_token_is_a_token(self) -> None:
        assert callers.caller_of(_token(caller="token")) == TOKEN

    def test_nothing_is_a_person_by_default_when_the_payload_is_empty(self) -> None:
        # An anonymous request holds no credential: the guard never asks about it, and when it
        # does the answer is the most restricted class that exists, never a person.
        assert callers.caller_of(None) == TOKEN
        assert callers.caller_of({}) == TOKEN


class TestWhatTheClientCannotSay:
    @pytest.mark.parametrize("claimed", ["person", "PERSON", "admin", "", None, 1, ["assistant"]])
    def test_an_unknown_mark_on_a_token_is_still_a_token(self, claimed: Any) -> None:
        assert callers.caller_of(_token(caller=claimed)) == TOKEN

    def test_the_mark_on_a_session_does_not_make_it_the_assistant(self) -> None:
        # Only a token can be the assistant: a browser session carrying the word is a person.
        payload = {"type": "session", "account_id": 3, "username": "marta", "caller": "assistant"}
        assert callers.caller_of(payload) == PERSON

    def test_the_mark_on_the_master_token_does_not_demote_it(self) -> None:
        assert (
            callers.caller_of({"type": "session", "sid": "master", "caller": "assistant"}) == PERSON
        )

    def test_a_header_is_not_part_of_the_answer(self) -> None:
        # The function reads the authenticated payload and nothing else.
        payload = _token(headers={"x-noust-caller": "person"}, client={"name": "a person"})
        assert callers.caller_of(payload) == TOKEN


class TestNames:
    def test_the_closed_list(self) -> None:
        assert callers.CALLERS == (PERSON, TOKEN, ASSISTANT)

    def test_who_is_not_a_person(self) -> None:
        assert not callers.is_person(TOKEN)
        assert not callers.is_person(ASSISTANT)
        assert callers.is_person(PERSON)

    def test_the_classes_a_policy_cell_exists_for(self) -> None:
        assert callers.POLICED == (TOKEN, ASSISTANT)
