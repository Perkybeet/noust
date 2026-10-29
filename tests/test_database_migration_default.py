# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The default database server follows a move to a newer major version (4.1, item 103).

An operator who chose a server as the default of its engine chose where new databases go. When the
databases of that server are moved to a newer version (``noust db instance migrate``), the old
server is left stopped and the new one is where they are, so the choice goes with them, and goes
back if the move is rolled back. What nobody chose is not stored and follows what exists.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.db_server_defaults import DbServerDefaultStore
from noust.core.exceptions import DeploymentError
from tests.support.database_migration_support import Lab, build_lab
from tests.support.database_move_support import HOST


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    yield from build_lab(tmp_path, monkeypatch)


def chosen(lab: Lab, flavour: str = "postgresql") -> str | None:
    row = DbServerDefaultStore(lab.world.store).get(flavour)
    return row.server_key if row else None


class TestTheChoiceGoesWithTheDatabases:
    def test_a_move_from_the_chosen_server_chooses_the_new_one(self, lab: Lab) -> None:
        DbServerDefaultStore(lab.world.store).set("postgresql", HOST, set_by="t")

        lab.migrations.run(HOST, "17", name="pg17", memory="1g")

        assert chosen(lab) == "postgresql@pg17"

    def test_a_move_from_an_instance_that_was_chosen_does_the_same(self, lab: Lab) -> None:
        lab.managed.create("pg16", "postgresql", version="16", memory="1g")
        DbServerDefaultStore(lab.world.store).set("postgresql", "postgresql@pg16", set_by="t")

        lab.migrations.run("pg16", "17", name="pg17")

        assert chosen(lab) == "postgresql@pg17"

    def test_nothing_chosen_stays_nothing_chosen(self, lab: Lab) -> None:
        lab.migrations.run(HOST, "17", name="pg17", memory="1g")

        assert chosen(lab) is None

    def test_a_choice_of_another_server_is_left_alone(self, lab: Lab) -> None:
        lab.managed.create("other", "postgresql", version="15", memory="1g")
        DbServerDefaultStore(lab.world.store).set("postgresql", "postgresql@other", set_by="t")

        lab.migrations.run(HOST, "17", name="pg17", memory="1g")

        assert chosen(lab) == "postgresql@other"

    def test_a_move_that_fails_leaves_the_choice_where_it_was(self, lab: Lab) -> None:
        DbServerDefaultStore(lab.world.store).set("postgresql", HOST, set_by="t")
        lab.gate["results"] = [(False, "502 from /health")]

        with pytest.raises(DeploymentError):
            lab.migrations.run(HOST, "17", name="pg17", memory="1g")

        assert chosen(lab) == HOST


class TestAndComesBackWithThem:
    def test_rolling_the_move_back_chooses_the_server_the_databases_returned_to(
        self, lab: Lab
    ) -> None:
        DbServerDefaultStore(lab.world.store).set("postgresql", HOST, set_by="t")
        outcome = lab.migrations.run(HOST, "17", name="pg17", memory="1g")

        lab.migrations.rollback(outcome.migration)

        assert chosen(lab) == HOST

    def test_rolling_back_what_nobody_chose_chooses_nothing(self, lab: Lab) -> None:
        outcome = lab.migrations.run(HOST, "17", name="pg17", memory="1g")

        lab.migrations.rollback(outcome.migration)

        assert chosen(lab) is None
