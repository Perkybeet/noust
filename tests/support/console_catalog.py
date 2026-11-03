# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The sentences the console says for the server's codes, read from its catalogs.

``panel/src/i18n/{en,es}/serverText.ts`` are an object literal of nested keys with a string under
each ``text`` and ``hint`` and nothing else, so a small reader is all a Python test needs to hold
the server's catalog to the console's, and no JavaScript runs. The console's own test
(``panel/src/i18n/serverText.test.ts``) holds the same line from the other side.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The console's catalog of the server's sentences, by language.
CONSOLE_CATALOGS = {
    "English": ROOT / "panel" / "src" / "i18n" / "en" / "serverText.ts",
    "Spanish": ROOT / "panel" / "src" / "i18n" / "es" / "serverText.ts",
}


def sentences_of(path: Path) -> dict[str, str]:
    """
    Read the sentences of a console catalog.

    Args:
        path: One of :data:`CONSOLE_CATALOGS`.

    Returns:
        ``code`` to the ``text`` of the sentence (the ``hint`` of a fix is not read).
    """
    source = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_]*|[{}:,]', source)
    found: dict[str, str] = {}
    trail: list[str] = []
    index = tokens.index("{")  # the object that serverText is
    key = ""
    while index < len(tokens):
        token = tokens[index]
        if token == "{":
            trail.append(key)
        elif token == "}":
            trail.pop()
        elif token == ":" and tokens[index + 1] != "{":
            value = tokens[index + 1]
            if key == "text" and value.startswith('"'):
                found[".".join(trail[1:])] = json.loads(value)
        elif token not in (",", ":"):
            key = token
        index += 1
    return found
