# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A move to a newer major version does not make an instance of a version without support (4.1).

Accepting a version whose project has stopped fixing security problems is the operator's decision
about one version, asked where the instance is made (``unsupported_ok``). A move makes its new
instance itself, so it would have to take that decision for them: it refuses instead, before it
touches anything, and says the commands that do it by hand.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.managers.database.eol import END_OF_LIFE
from tests.support.database_migration_support import Lab, build_lab
from tests.support.database_move_support import HOST


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    yield from build_lab(tmp_path, monkeypatch)


@pytest.fixture
def seventeen_has_ended(monkeypatch: pytest.MonkeyPatch) -> None:
    # PostgreSQL 17 is the newer version a move goes to; its project's date has passed.
    monkeypatch.setitem(END_OF_LIFE["postgresql"], "17", date(2020, 1, 1))


def test_a_supported_version_is_a_plan_without_the_blocker(lab: Lab) -> None:
    plan = lab.migrations.plan(HOST, "17", memory="1g")

    assert plan.blockers == []


def test_a_version_without_support_is_a_blocker_that_says_how_to_do_it_by_hand(
    lab: Lab, seventeen_has_ended: None
) -> None:
    plan = lab.migrations.plan(HOST, "17", name="pg17", memory="1g")

    [blocker] = plan.blockers
    assert "no longer supports" in blocker and "2020-01-01" in blocker
    assert "noust db instance create pg17 --engine postgresql --version 17" in blocker
    assert "--accept-unsupported" in blocker
    assert f"noust db move DATABASE --from {HOST} --to postgresql@pg17" in blocker


def test_the_move_makes_nothing(lab: Lab, seventeen_has_ended: None) -> None:
    with pytest.raises(ValidationError):
        lab.migrations.run(HOST, "17", name="pg17", memory="1g")

    assert lab.managed.records.all() == []
    assert lab.world.host.containers == {}
