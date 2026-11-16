# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every sentence the source says is said with the values the catalog gives it (item 151).

``say("code", value=...)`` fails at run time when a value is missing or one is not taken, and the places
that say a sentence are most of them errors: a branch nobody's test reaches, which then raises a
``ValueError`` where it should have refused with its sentence. The syntax tree has everything needed to
see it in advance: this reads each call whose code is a literal and whose values are written out, and holds
the names it passes to the placeholders of the entry (and of its fix).
"""

from __future__ import annotations

import ast
from pathlib import Path

from noust.core import server_text
from noust.core.server_text import CATALOG, values_of

SRC = Path(server_text.__file__).resolve().parents[1]


def _calls() -> list[tuple[str, int, str, set[str]]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if "static" in path.parts or path.name.startswith("server_text"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        says = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "noust.core.server_text"
            and any(alias.name == "say" and alias.asname is None for alias in node.names)
            for node in ast.walk(tree)
        )
        if not says:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "say"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and all(keyword.arg is not None for keyword in node.keywords)
            ):
                found.append(
                    (
                        node.args[0].value,
                        node.lineno,
                        path.relative_to(SRC.parent).as_posix(),
                        {str(keyword.arg) for keyword in node.keywords},
                    )
                )
    return found


def test_every_literal_sentence_is_said_with_exactly_the_values_the_catalog_gives_it() -> None:
    wrong = []
    for code, line, where, given in _calls():
        if code not in CATALOG:
            continue  # a code the catalog lacks is the other test's to say
        wanted = values_of(code)
        if given != wanted:
            wrong.append(
                f"{where}:{line} {code}: gives {sorted(given)}, the catalog takes {sorted(wanted)}"
            )

    assert wrong == []
