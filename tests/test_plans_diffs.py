# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a plan shows of a file it would change, and what it never shows.

The canaries are values that must not come out of a plan whichever way the file is shaped: a
plan is read with the permission to read the application, which is less than the permission to
read its secrets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.plans.diffs import MAX_DIFF_LINES, FileView, view_of_change
from noust.core.redact import Scrubber

CANARY = "canary-7f3a9c1e5b2d4a68"
OTHER_CANARY = "canary-0b8d6e2f4a1c9375"


def view(
    path: str, after: str | bytes | None, before: str | bytes | None = None, **kwargs
) -> FileView:
    return view_of_change(Path(path), before=before, after=after, scrub=lambda text: text, **kwargs)


def kinds(found: FileView) -> list[str]:
    return [line.kind for line in found.lines]


def everything(found: FileView) -> str:
    return "\n".join(line.text for line in found.lines)


def test_a_changed_text_file_shows_the_lines_that_change_and_two_of_context() -> None:
    before = (
        "services:\n  app:\n    image: app:1\n    restart: always\n    init: true\n    tty: false\n"
    )
    after = before.replace("app:1", "app:2")

    found = view("/srv/app/compose.yaml", after, before)

    assert found.shown == "diff" and found.reason is None and not found.truncated
    assert [(line.kind, line.text) for line in found.lines] == [
        ("same", "services:"),
        ("same", "  app:"),
        ("del", "    image: app:1"),
        ("add", "    image: app:2"),
        ("same", "    restart: always"),
        ("same", "    init: true"),
    ]


def test_a_new_file_is_all_additions_and_a_deleted_one_all_removals() -> None:
    created = view("/etc/noust/new.conf", "a\nb\n")
    deleted = view("/etc/noust/old.conf", None, "a\nb\n")

    assert kinds(created) == ["add", "add"]
    assert kinds(deleted) == ["del", "del"]


def test_a_file_with_no_change_has_no_lines() -> None:
    assert view("/x/y", "same\n", "same\n").lines == ()


def test_a_dotenv_shows_names_and_never_a_value() -> None:
    before = f"KEEP=1\nDB_PASSWORD={CANARY}\nGONE=old\n"
    after = f"KEEP=1\nDB_PASSWORD={OTHER_CANARY}\nNEW_NAME=value-{CANARY}\n"

    found = view("/var/www/apps/shop/.env", after, before, mode=0o600)

    assert found.shown == "names"
    assert [(line.kind, line.text) for line in found.lines] == [
        ("del", "DB_PASSWORD"),
        ("add", "DB_PASSWORD"),
        ("add", "NEW_NAME"),
        ("del", "GONE"),
    ]
    assert CANARY not in everything(found) and OTHER_CANARY not in everything(found)


def test_a_dotenv_that_did_not_change_says_nothing() -> None:
    assert view("/x/.env", "A=1\n", "A=1\n", mode=0o600).lines == ()


def test_a_private_file_that_is_not_a_list_of_variables_shows_nothing_of_its_content() -> None:
    found = view("/etc/noust/token.json", f'{{"token": "{CANARY}"}}', None, mode=0o600)

    assert found.shown == "none" and found.reason == "secret" and found.lines == ()


def test_a_binary_file_is_not_shown() -> None:
    found = view("/srv/app/logo.png", b"\x89PNG\r\n\x1a\n", None)

    assert (found.shown, found.reason, found.lines) == ("none", "binary", ())


def test_text_that_cannot_be_decoded_is_a_binary_file_too() -> None:
    assert view("/srv/x", "ok\n", b"\xff\xfe\x00").reason == "binary"


def test_a_value_named_like_a_secret_is_masked_in_any_file() -> None:
    after = f"services:\n  app:\n    environment:\n      DB_PASSWORD: {CANARY}\n      LOG_LEVEL: debug\n"

    found = view("/srv/app/compose.yaml", after)

    assert CANARY not in everything(found)
    assert "      DB_PASSWORD: ***" in everything(found)
    assert "      LOG_LEVEL: debug" in everything(found)


def test_a_reference_to_a_variable_is_not_a_secret_to_hide() -> None:
    found = view("/srv/app/compose.yaml", "      DB_PASSWORD: ${DB_PASSWORD}\n")

    assert "${DB_PASSWORD}" in everything(found)


def test_a_credential_in_an_address_is_masked_by_its_shape() -> None:
    from noust.core.redact import Scrubber

    found = view_of_change(
        Path("/srv/app/config.ini"),
        before=None,
        after=f"origin = https://deploy:{CANARY}@git.example.com/shop.git\n",
        scrub=Scrubber().scrub,
    )

    assert CANARY not in everything(found)
    assert "https://deploy:" in everything(found)


def test_a_value_the_application_is_known_to_hold_is_masked_wherever_it_stands() -> None:
    found = view_of_change(
        Path("/srv/app/notes.txt"),
        before=None,
        after=f"the key is {CANARY} and that is all\n",
        scrub=Scrubber([CANARY]).scrub,
    )

    assert CANARY not in everything(found)


def test_a_long_diff_is_cut_and_says_so() -> None:
    after = "".join(f"line {number}\n" for number in range(MAX_DIFF_LINES * 3))

    found = view("/srv/app/big.txt", after)

    assert found.truncated and len(found.lines) == MAX_DIFF_LINES


def test_a_huge_file_is_not_diffed_at_all() -> None:
    found = view("/srv/app/dump.sql", "x" * 600_000)

    assert (found.shown, found.reason, found.lines) == ("none", "large", ())


@pytest.mark.parametrize("name", [".env", ".env.production", "shop.env", ".env.local"])
def test_every_way_of_naming_a_dotenv_is_one(name: str) -> None:
    found = view(f"/srv/{name}", f"A={CANARY}\n")

    assert found.shown == "names" and CANARY not in everything(found)
