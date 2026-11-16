# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a firewall or SSH change was reaches the console as a sentence it can say in its language (item 151).

A change is recorded with a code and its values, and the notification says it in the language of the
person who reads it. The page of pending changes and the banner that counts one down read the same
record through the API, which sent only the English ``title``: a Spanish console said "Allow tcp port
5432 from 10.0.4.12/32" in the middle of a Spanish sentence. The API now sends the sentence as a code
and its values (``title_coded``), and the console says it in its own words, which are the words of the
notification: one place for the English, and a test that the Spanish of the two is the same.
"""

from __future__ import annotations

import pytest

from noust.core import access_changes
from noust.core.messages_access import ACCESS_MESSAGES
from noust.core.server_text import CATALOG, say
from noust.web.api.server.security import _change
from tests.support.console_catalog import CONSOLE_CATALOGS, sentences_of

CHANGES = sorted(
    key.removeprefix("change.")
    for key in ACCESS_MESSAGES
    if key.startswith("change.") and key != "change.recorded"
)


def _values(what: str) -> dict[str, str]:
    import string

    names = {name for _, name, _, _ in string.Formatter().parse(ACCESS_MESSAGES[f"change.{what}"]["en"]) if name}
    return {name: f"<{name}>" for name in names}


@pytest.mark.parametrize("what", CHANGES)
def test_every_change_is_a_sentence_of_the_catalog_with_the_english_of_the_notification(
    what: str,
) -> None:
    sentence = access_changes.coded(what, _values(what))

    assert sentence is not None and sentence.code == f"access_changes.{what}"
    assert sentence.text == access_changes.describe(what, _values(what), "en")
    assert f"access_changes.{what}" in CATALOG


@pytest.mark.parametrize("what", CHANGES)
def test_the_console_says_each_change_in_the_words_of_the_notification(what: str) -> None:
    said = sentences_of(CONSOLE_CATALOGS["Spanish"])

    assert said[f"access_changes.{what}"] == ACCESS_MESSAGES[f"change.{what}"]["es"]
    assert sentences_of(CONSOLE_CATALOGS["English"])[f"access_changes.{what}"] == (
        ACCESS_MESSAGES[f"change.{what}"]["en"]
    )


def test_a_record_that_has_no_code_or_one_that_cannot_be_said_has_no_sentence() -> None:
    assert access_changes.coded("", {}) is None
    assert access_changes.coded("a_change_of_a_later_version", {"x": "1"}) is None
    # The record kept the code but lost a value its sentence puts in.
    assert access_changes.coded("firewall_allow", {"port": "22/tcp"}) is None
    # The sentence of a record from before the ledger kept what it was is not one of the catalog's.
    assert access_changes.coded("recorded", {"text": "Allow tcp port 22"}) is None


def _record(**extra: object) -> dict[str, object]:
    return {
        "id": "66e8f26e",
        "kind": "firewall",
        "title": "Allow 5432/tcp from 10.0.4.12/32",
        "actor": "master",
        "applied_at": 1.0,
        "expires_at": 301.0,
        "status": "pending",
        "unit": "noust-security-66e8f26e",
        "proof": "operator",
        **extra,
    }


def test_the_api_sends_the_change_as_a_code_and_its_values_beside_its_title() -> None:
    shown = _change(
        _record(what="firewall_allow", values={"port": "5432/tcp", "source": "10.0.4.12/32"})
    ).model_dump()

    assert shown["title"] == "Allow 5432/tcp from 10.0.4.12/32"
    assert shown["title_coded"] == say(
        "access_changes.firewall_allow", port="5432/tcp", source="10.0.4.12/32"
    ).to_dict()


def test_the_api_sends_no_code_for_a_change_recorded_before_the_ledger_kept_one() -> None:
    shown = _change(_record(what="", values={})).model_dump()

    assert shown["title_coded"] is None and shown["title"].startswith("Allow")
