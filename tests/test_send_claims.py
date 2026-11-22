# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One pass at a time sends an owed copy to a destination (item 201).

Two passes that meet (the timer's and one a person started, or two timers after a network cut) used to
read the same owed row and both upload the copy, and told the operator twice. The claim is a ``flock``
on a file per copy and destination: a second claimant is refused at once, the kernel lets go of it when
its holder ends however it ends (so a process that died holds nothing), and under ``--dry-run`` nothing
is created.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from noust.core.fs import DryRunFileSystem, set_fs
from noust.core.send_claims import claim, claim_directory


@pytest.fixture(autouse=True)
def _real_filesystem() -> None:
    set_fs(None)


def test_a_copy_that_one_pass_holds_is_refused_to_another(tmp_path: Path) -> None:
    with claim(tmp_path, "app", "shop_1", "nas") as first:
        assert first is True
        with claim(tmp_path, "app", "shop_1", "nas") as second:
            assert second is False


def test_the_claim_is_per_copy_and_destination(tmp_path: Path) -> None:
    with claim(tmp_path, "app", "shop_1", "nas") as first:
        with claim(tmp_path, "app", "shop_1", "cloud") as other_destination:
            with claim(tmp_path, "app", "shop_2", "nas") as other_copy:
                with claim(tmp_path, "volume", "shop_1", "nas") as other_kind:
                    assert (first, other_destination, other_copy, other_kind) == (True,) * 4


def test_a_copy_can_be_claimed_again_once_the_pass_is_done(tmp_path: Path) -> None:
    with claim(tmp_path, "app", "shop_1", "nas") as first:
        assert first is True
    with claim(tmp_path, "app", "shop_1", "nas") as again:
        assert again is True


def test_a_refused_claim_does_not_release_the_holders(tmp_path: Path) -> None:
    with claim(tmp_path, "app", "shop_1", "nas"):
        with claim(tmp_path, "app", "shop_1", "nas") as refused:
            assert refused is False
        with claim(tmp_path, "app", "shop_1", "nas") as still_refused:
            assert still_refused is False


def test_a_file_left_by_a_process_that_died_holds_nothing(tmp_path: Path) -> None:
    # The kernel frees a flock when its process ends, so a record left in the file is no claim: this
    # is the expiry of the claim of a pass that was killed.
    with claim(tmp_path, "app", "shop_1", "nas"):
        (held,) = tmp_path.glob("send-*.lock")
        leftover = held.read_bytes()
    held.write_bytes(leftover or b'{"pid": 1}')

    with claim(tmp_path, "app", "shop_1", "nas") as taken:
        assert taken is True


def test_nothing_is_left_in_the_directory_after_a_pass(tmp_path: Path) -> None:
    for item in ("a", "b", "c"):
        with claim(tmp_path, "app", item, "nas"):
            pass

    assert list(tmp_path.iterdir()) == []


def test_a_name_that_is_not_a_file_name_is_still_one_claim(tmp_path: Path) -> None:
    # A database dump is "engine:file", and what a destination is called is the operator's.
    with claim(tmp_path, "database", "postgres:../../etc/passwd", "nas/../x") as taken:
        assert taken is True
        (found,) = tmp_path.glob("send-*.lock")
        assert found.parent == tmp_path


def test_a_link_planted_where_the_claim_goes_is_not_followed(tmp_path: Path) -> None:
    with claim(tmp_path, "app", "shop_1", "nas"):
        (held,) = tmp_path.glob("send-*.lock")
        name = held.name
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    (tmp_path / name).symlink_to(target)

    with pytest.raises(OSError):
        with claim(tmp_path, "app", "shop_1", "nas"):
            pass

    assert target.read_text() == "keep"


def test_a_rehearsal_creates_no_file(tmp_path: Path) -> None:
    directory = tmp_path / "locks"
    set_fs(DryRunFileSystem())
    try:
        with claim(directory, "app", "shop_1", "nas") as taken:
            assert taken is True
    finally:
        set_fs(None)

    assert not directory.exists()


def test_the_directory_is_beside_the_store(tmp_path: Path) -> None:
    from noust.core.store import NoustStore

    store = NoustStore(tmp_path / "state" / "noust.db")
    try:
        assert claim_directory(store) == tmp_path / "state" / "locks"
    finally:
        store.close()
    assert os.path.isdir(tmp_path)
