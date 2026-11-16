# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What each permission lets a role do is said in Spanish by the console (item 151).

``GET /api/roles`` sends each permission's description in English (``DESCRIPTIONS``), and the roles page
drew it as it came, under a heading that was the raw area name, so Marcos read thirty English sentences
in a Spanish console just where he decides what a person may do. The console now has the sentence of every
permission and the word of every area in both languages; this holds those catalogs to the permissions the
server has, so that a new permission does not ship without them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from noust.core.permission_catalog import ALL_PERMISSIONS, DESCRIPTIONS

ROOT = Path(__file__).resolve().parents[1] / "panel" / "src" / "i18n"
LANGUAGES = ["en", "es"]


def _block(language: str, name: str) -> dict[str, str]:
    """The entries of a flat object of ``roles.ts`` (``permissionText`` or ``area``) as written."""
    source = (ROOT / language / "roles.ts").read_text(encoding="utf-8")
    start = source.index(f"  {name}: {{")
    end = source.index("\n  },", start)
    pairs = re.findall(r'^\s{4}([a-z_]+): ("(?:[^"\\]|\\.)*"),$', source[start:end], flags=re.M)
    import json

    return {key: json.loads(value) for key, value in pairs}


def _areas() -> set[str]:
    return {name.split(".")[0] for name in ALL_PERMISSIONS if name != "self"}


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_permission_has_its_sentence_and_no_other_does(language: str) -> None:
    said = _block(language, "permissionText")

    wanted = {name.replace(".", "_") for name in ALL_PERMISSIONS}
    assert sorted(wanted - set(said)) == []
    assert sorted(set(said) - wanted) == []


def test_the_english_is_the_description_the_server_sends() -> None:
    said = _block("en", "permissionText")

    for name, description in DESCRIPTIONS.items():
        assert said[name.replace(".", "_")] == description


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_area_has_its_word(language: str) -> None:
    assert sorted(_areas() - set(_block(language, "area"))) == []
