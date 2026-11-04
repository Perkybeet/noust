# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every sentence the server says with a code is said in English and in Spanish by the console.

The catalog of ``noust.core.server_text`` is the English the server sends; the console's two
catalogs (``panel/src/i18n/{en,es}/serverText.ts``) are what a person reads. A code without a text
in Spanish is a sentence read in English in a Spanish console, and a text that puts in other values
than the server sends is a blank or a stray brace on a page. The console's own test holds the same
line from the other side (``panel/src/i18n/serverText.test.ts``); this one runs with the server's
tests, where the sentence is added, and reads the two catalogs without running any JavaScript.
"""

from __future__ import annotations

import re
import string

import pytest

from noust.core.server_text import CATALOG, HINT_SUFFIX
from tests.support.console_catalog import CONSOLE_CATALOGS, sentences_of

SENTENCES = sorted(code for code in CATALOG if not code.endswith(HINT_SUFFIX))


def _values(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


def _server_values(code: str, part: str) -> set[str]:
    key = code if part == "text" else code + HINT_SUFFIX
    template = CATALOG.get(key, "")
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


@pytest.mark.parametrize("language", CONSOLE_CATALOGS)
def test_every_sentence_has_its_text_in_the_console(language: str) -> None:
    catalog = sentences_of(CONSOLE_CATALOGS[language])

    assert [code for code in SENTENCES if code not in catalog] == []


@pytest.mark.parametrize("language", CONSOLE_CATALOGS)
def test_every_fix_of_a_sentence_is_in_the_console_and_no_fix_is_made_up(language: str) -> None:
    fixes = sentences_of(CONSOLE_CATALOGS[language], "hint")
    wanted = {code for code in SENTENCES if code + HINT_SUFFIX in CATALOG}

    assert sorted(wanted - set(fixes)) == []
    assert sorted(set(fixes) - wanted) == []


@pytest.mark.parametrize("language", CONSOLE_CATALOGS)
def test_the_console_says_each_sentence_and_fix_with_the_values_the_server_sends(
    language: str,
) -> None:
    wrong: list[str] = []
    for part in ("text", "hint"):
        said = sentences_of(CONSOLE_CATALOGS[language], part)
        for code, text in said.items():
            if code in CATALOG and _values(text) != _server_values(code, part):
                wrong.append(
                    f"{code}.{part}: {sorted(_values(text))} for {sorted(_server_values(code, part))}"
                )

    assert wrong == []


def test_the_console_has_no_sentence_the_server_does_not_say() -> None:
    for language, path in CONSOLE_CATALOGS.items():
        extra = sorted(set(sentences_of(path)) - set(SENTENCES))
        assert extra == [], f"{language} says what the server does not: {extra}"
