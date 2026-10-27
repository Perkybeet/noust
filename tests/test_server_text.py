# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sentences the server says for the console to translate (``noust.core.server_text``).

A sentence a person reads in the console is Noust's own words, and the console speaks two
languages: it cannot translate a string it is only handed. These tests hold the one mechanism
that gives such a sentence a code and parameters beside its English text, so that what the
server sends, what the CLI prints and what the console translates are the same sentence.
"""

from __future__ import annotations

import ast
import string
from pathlib import Path

import pytest

from noust.core import server_text
from noust.core.exceptions import NoustError, ValidationError
from noust.core.server_text import CATALOG, HINT_SUFFIX, ServerText, say

SRC = Path(server_text.__file__).resolve().parents[1]


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_a_sentence_is_its_catalog_entry_with_the_values_put_in() -> None:
    text = say("tunnels.cloudflared_missing", url="https://pkg.cloudflare.com/")

    assert text.code == "tunnels.cloudflared_missing"
    assert text.text == "cloudflared is not installed"
    assert text.params == {"url": "https://pkg.cloudflare.com/"}


def test_the_fix_of_a_sentence_is_the_entry_that_ends_in_hint() -> None:
    text = say("tunnels.cloudflared_missing", url="https://pkg.cloudflare.com/")

    assert text.hint is not None and "https://pkg.cloudflare.com/" in text.hint
    assert say("tunnels.store_sealed").hint is not None
    # A sentence with no fix has none, rather than an empty one.
    assert say("doctor.platform.supported", label="Debian 12, x86_64").hint is None


def test_the_json_form_is_the_code_and_the_parameters_and_nothing_else() -> None:
    text = say("doctor.program.version", command="curl", version="curl 8.5.0")

    assert text.to_dict() == {
        "code": "doctor.program.version",
        "params": {"command": "curl", "version": "curl 8.5.0"},
    }


def test_an_unknown_code_is_a_bug_found_at_once_not_a_blank_in_the_console() -> None:
    with pytest.raises(KeyError, match=r"no\.such\.code"):
        say("no.such.code")


def test_every_placeholder_needs_its_value_and_every_value_its_placeholder() -> None:
    with pytest.raises(ValueError, match="url"):
        say("tunnels.cloudflared_missing")
    with pytest.raises(ValueError, match="surplus"):
        say("tunnels.store_sealed", surplus="x")


def test_a_value_is_text_a_number_or_a_flag_because_that_is_what_the_console_can_put_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(CATALOG, "test.count", "{count} of them, on: {enabled}")

    with pytest.raises(TypeError, match="url"):
        say("tunnels.cloudflared_missing", url=["not", "a", "scalar"])  # type: ignore[arg-type]
    assert say("test.count", count=3, enabled=True).params == {"count": 3, "enabled": True}
    assert say("test.count", count=3, enabled=True).text == "3 of them, on: True"


def test_a_sentence_cannot_be_given_a_code_that_is_not_in_the_catalog_by_building_it_by_hand() -> (
    None
):
    with pytest.raises(KeyError):
        ServerText("made.up", {})


def test_a_sentence_travels_on_an_error_and_the_error_keeps_its_own_words() -> None:
    text = say("tunnels.store_sealed")

    error = ValidationError.from_text(text, field="name")

    assert (error.message, error.details) == (text.text, text.hint)
    assert error.field == "name"
    assert error.coded == text
    assert isinstance(error, NoustError)
    # An error nobody coded says so with None, never a made-up code.
    assert ValidationError("plain").coded is None


# -- the catalog itself ---------------------------------------------------------------


def test_every_hint_belongs_to_a_sentence() -> None:
    for code in CATALOG:
        if code.endswith(HINT_SUFFIX):
            sentence = code[: -len(HINT_SUFFIX)]
            assert sentence in CATALOG, f"{code} is the fix of {sentence}, which does not exist"


def test_a_sentence_and_its_fix_are_said_with_the_values_of_both() -> None:
    assert server_text.values_of("tunnels.cloudflared_missing") == {"url"}
    assert server_text.values_of("tunnels.store_sealed") == set()


def test_a_code_is_dotted_lowercase_words_because_it_is_a_catalog_key_in_the_console() -> None:
    for code in CATALOG:
        assert all(
            part and part.replace("_", "").isalnum() and part == part.lower()
            for part in code.split(".")
        ), code


def test_no_sentence_is_empty_and_none_has_a_stray_brace() -> None:
    for code, template in CATALOG.items():
        assert template.strip(), code
        # Parsing is what ``say`` will do with it; a lone brace would fail there at run time.
        list(string.Formatter().parse(template))


def _trees() -> list[tuple[Path, ast.AST]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(SRC.rglob("*.py"))
        if "static" not in path.parts and path.name != "server_text.py"
    ]


def _codes_asked_in_source() -> dict[str, list[str]]:
    """Every literal code passed to ``say(...)`` in the package, with where."""
    found: dict[str, list[str]] = {}
    for path, tree in _trees():
        # Other modules have a ``say`` of their own (a job's progress line); only the one
        # imported from here is a sentence.
        imports_it = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "noust.core.server_text"
            and any(alias.name == "say" for alias in node.names)
            for node in ast.walk(tree)
        )
        if not imports_it:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "say"
                and node.args
            ):
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.setdefault(first.value, []).append(
                        f"{path.relative_to(SRC.parent)}:{node.lineno}"
                    )
    return found


def test_every_code_the_source_asks_for_is_in_the_catalog() -> None:
    missing = {
        code: where for code, where in _codes_asked_in_source().items() if code not in CATALOG
    }

    assert missing == {}


def test_every_sentence_in_the_catalog_is_said_somewhere() -> None:
    # A code reaches ``say`` as a literal or, where one function says several (a resource's
    # four states), through a table of literals: either way the string is in the source.
    literals = {
        node.value
        for _, tree in _trees()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    unused = sorted(
        code for code in CATALOG if not code.endswith(HINT_SUFFIX) and code not in literals
    )

    assert unused == []
