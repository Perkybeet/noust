# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every security check has its title in the console, in both languages (item 151).

``/security`` showed "Application services do not start with the server" in English among the checks
that passed, because ``noust.units_not_enabled`` was the one of 32 checks the console had no title for.
The server says each check's title in English, and the console says it in its own language from the
check's id: this holds the console's table to the server's catalog, so a check added there cannot ship
without its words here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from noust.managers.server.security_catalog import CATALOG

ROOT = Path(__file__).resolve().parents[1] / "panel" / "src"


def _table() -> dict[str, str]:
    """The check id to the console's catalog key, as ``features/server/security/data.ts`` writes it."""
    source = (ROOT / "features" / "server" / "security" / "data.ts").read_text(encoding="utf-8")
    return dict(
        re.findall(
            r'^\s+"([a-z0-9_.]+)": "(server\.checks\.titles\.[A-Za-z0-9]+)",$', source, flags=re.M
        )
    )


def test_every_check_of_the_server_has_a_title_in_the_console() -> None:
    assert sorted(set(CATALOG) - set(_table())) == []


@pytest.mark.parametrize("language", ["en", "es"])
def test_every_title_the_console_points_at_is_written_in_both_languages(language: str) -> None:
    source = (ROOT / "i18n" / language / "server.ts").read_text(encoding="utf-8")
    written = set(re.findall(r"^\s+([A-Za-z0-9]+): \"", source, flags=re.M))

    assert (
        sorted(
            key.rsplit(".", 1)[1]
            for key in _table().values()
            if key.rsplit(".", 1)[1] not in written
        )
        == []
    )


def test_the_english_title_is_the_one_the_server_says() -> None:
    source = (ROOT / "i18n" / "en" / "server.ts").read_text(encoding="utf-8")
    table = _table()

    # The console says the title it chose, which may be reworded for the page; the check this item was about
    # is held to the server's own words so that the two do not drift apart unseen.
    key = table["noust.units_not_enabled"].rsplit(".", 1)[1]
    assert f'{key}: "{CATALOG["noust.units_not_enabled"].title}"' in source
