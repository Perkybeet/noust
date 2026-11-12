# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The versions of a stack's Compose document, as the history of a site's file keeps them.

What is held: a version is a text and never a duplicate of the one before; it says who saved it, when and
why; only the latest :data:`KEEP` are kept and numbers are never reused; and a stack that goes away takes
its history with it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.docker_stack_versions import KEEP, StackVersionStore, sha256_of
from noust.core.store import NoustStore

DOCUMENT = "services:\n  web:\n    image: nginx:1.27\n"


@pytest.fixture
def versions(tmp_path: Path) -> Iterator[StackVersionStore]:
    NoustStore.reset_instance()
    yield StackVersionStore(NoustStore(tmp_path / "noust.db"))
    NoustStore.reset_instance()


def test_the_first_text_is_version_one_with_who_and_why(versions: StackVersionStore) -> None:
    added = versions.add(
        "shop.example.com", DOCUMENT, source="edit", actor="ana", reason="Limit the memory"
    )

    assert added is not None
    assert (added.version, added.source, added.actor, added.reason) == (
        1,
        "edit",
        "ana",
        "Limit the memory",
    )
    assert added.sha256 == sha256_of(DOCUMENT)
    assert added.size == len(DOCUMENT.encode())


def test_the_same_text_again_adds_nothing(versions: StackVersionStore) -> None:
    versions.add("shop.example.com", DOCUMENT, source="found", actor=None, reason="")

    again = versions.add("shop.example.com", DOCUMENT, source="edit", actor="ana", reason="x")

    assert again is None
    assert [v.version for v in versions.versions("shop.example.com")] == [1]


def test_versions_are_numbered_in_order_and_listed_newest_first(
    versions: StackVersionStore,
) -> None:
    for number in range(1, 4):
        versions.add(
            "shop.example.com", DOCUMENT + f"# {number}\n", source="edit", actor="ana", reason=""
        )

    listed = versions.versions("shop.example.com")

    assert [v.version for v in listed] == [3, 2, 1]
    assert all(v.text == "" for v in listed)  # a listing carries no text


def test_one_stack_does_not_see_the_versions_of_another(versions: StackVersionStore) -> None:
    versions.add("shop.example.com", DOCUMENT, source="edit", actor="ana", reason="")

    assert versions.versions("blog.example.com") == []
    assert versions.latest("blog.example.com") is None


def test_a_version_is_read_with_its_text(versions: StackVersionStore) -> None:
    versions.add("shop.example.com", DOCUMENT, source="edit", actor="ana", reason="")

    found = versions.get("shop.example.com", 1)

    assert found is not None
    assert found.text == DOCUMENT
    assert versions.get("shop.example.com", 2) is None


def test_only_the_latest_are_kept_and_numbers_are_not_reused(versions: StackVersionStore) -> None:
    for number in range(1, KEEP + 6):
        versions.add(
            "shop.example.com", DOCUMENT + f"# {number}\n", source="edit", actor=None, reason=""
        )

    listed = versions.versions("shop.example.com")

    assert len(listed) == KEEP
    assert listed[0].version == KEEP + 5
    assert listed[-1].version == 6
    assert versions.get("shop.example.com", 1) is None


def test_a_stack_that_goes_takes_its_history(versions: StackVersionStore) -> None:
    versions.add("shop.example.com", DOCUMENT, source="edit", actor=None, reason="")
    versions.add("blog.example.com", DOCUMENT, source="edit", actor=None, reason="")

    versions.forget("shop.example.com")

    assert versions.versions("shop.example.com") == []
    assert len(versions.versions("blog.example.com")) == 1


def test_a_source_the_history_does_not_know_is_refused(versions: StackVersionStore) -> None:
    with pytest.raises(ValueError, match="source"):
        versions.add("shop.example.com", DOCUMENT, source="magic", actor=None, reason="")


def test_the_reason_is_kept_to_a_line_so_a_history_reads_as_one(
    versions: StackVersionStore,
) -> None:
    added = versions.add(
        "shop.example.com",
        DOCUMENT,
        source="edit",
        actor=None,
        reason="first line\nsecond line " + "x" * 400,
    )

    assert added is not None
    assert "\n" not in added.reason
    assert len(added.reason) <= 200
