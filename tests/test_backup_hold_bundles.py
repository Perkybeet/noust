# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A bundle of Noust's own state a central has not taken is not rotated away (item 195)."""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.managers.self_backup import SelfBackupManager
from tests.support.backup_hold_support import Hold, hold  # noqa: F401
from tests.support.self_backup import (  # noqa: F401
    PASSPHRASE,
    Runner,
    Server,
    _reset_store,
    cheap_scrypt,
    origin,
    runner,
)

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture
def old(tmp_path: Path) -> Iterator[Server]:
    made = origin(tmp_path)
    yield made
    for connection in made.held:
        connection.close()


def age(path: Path, days: float) -> None:
    moment = time.time() - days * 86400
    os.utime(path, (moment, moment))


def scheduled(manager: SelfBackupManager, count: int) -> list[str]:
    bundles = [manager.create(PASSPHRASE, kind="scheduled") for _ in range(count)]
    for index, bundle in enumerate(bundles):
        age(bundle.path, count - index)
    return [bundle.path.name for bundle in bundles]


def test_a_bundle_the_central_lacks_stays_and_does_not_count_toward_the_limit(
    old: Server, runner: Runner, hold: Hold
) -> None:
    manager = old.manager(runner)
    names = scheduled(manager, 5)
    hold.spare = {names[0], names[1]}

    deleted = manager.apply_retention(retention_count=2, retention_days=None)

    assert hold.asked and {kind for kind, _ in hold.asked} == {"noust"}
    assert deleted == [names[2]]
    assert {entry.name for entry in manager.entries()} == {names[0], names[1], names[3], names[4]}


def test_what_is_rotated_away_is_said(old: Server, runner: Runner, hold: Hold) -> None:
    manager = old.manager(runner)
    names = scheduled(manager, 4)

    manager.apply_retention(retention_count=1, retention_days=None)

    assert {
        item for kind, item, owner in hold.noted if kind == "noust" and owner == "noust"
    } == set(names[:3])


def test_a_bundle_deleted_by_a_person_says_nothing(old: Server, runner: Runner, hold: Hold) -> None:
    manager = old.manager(runner)
    names = scheduled(manager, 2)

    manager.delete(names[0])

    assert hold.noted == []
