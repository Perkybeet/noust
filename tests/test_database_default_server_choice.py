# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the default database server of an engine changes (4.1, item 103).

Choosing a container as the default of an engine means a new database of that engine goes
there when nobody says otherwise: the database a new application asks for
(:func:`~noust.managers.database.choice.resolve_choice`) and the one ``noust db create`` makes
(:func:`~noust.managers.database.choice.new_database_engine`). What must not change is what 4.0
did: with no choice the system's engine is used, a version or an instance that is named is
honoured, and an engine other than the one chosen is left alone.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.db_server_defaults import DbServerDefaultStore
from noust.core.store import NoustStore
from noust.managers.database.choice import new_database_engine, resolve_choice
from tests.support.database_choice import noble  # noqa: F401 - the autouse fixture
from tests.support.database_servers import World, make_instance


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def world(tmp_path: Path, store: NoustStore) -> World:
    return World(tmp_path, store, mysql="mysql")


def choose(world: World, flavour: str, key: str) -> None:
    DbServerDefaultStore(world.store).set(flavour, key, set_by="t")


class TestWhenNobodyChose:
    def test_a_database_is_made_on_the_system_engine_as_before(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        choice = resolve_choice(world.service(), "mysql")

        assert (choice.engine, choice.instance) == ("mysql", None)
        assert new_database_engine(world.service(), "mysql") == "mysql"


class TestWhenAContainerIsTheDefault:
    def test_a_new_applications_database_goes_there(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        choose(world, "mysql", "mysql@legacy")

        choice = resolve_choice(world.service(), "mysql")

        assert (choice.engine, choice.instance, choice.action) == (
            "mysql@legacy",
            "legacy",
            "use",
        )

    def test_so_does_a_database_made_by_name(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        choose(world, "mysql", "mysql@legacy")

        assert new_database_engine(world.service(), "mysql") == "mysql@legacy"

    def test_naming_the_server_is_still_naming_it(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        make_instance(world.store, "other", "mysql", "8.4", port=3308)
        choose(world, "mysql", "mysql@legacy")

        assert new_database_engine(world.service(), "mysql@other") == "mysql@other"
        named = resolve_choice(world.service(), "mysql", instance="other")
        assert named.instance == "other"

    def test_asking_for_a_version_is_asking_for_that_version_not_for_the_default(
        self, world: World
    ) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        choose(world, "mysql", "mysql@legacy")

        choice = resolve_choice(world.service(), "mysql", version="8.0.36")

        assert (choice.engine, choice.instance) == ("mysql", None)

    def test_the_system_engine_chosen_back_is_the_system_engine(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        choose(world, "mysql", "mysql@legacy")
        choose(world, "mysql", "mysql")

        assert resolve_choice(world.service(), "mysql").instance is None
        assert new_database_engine(world.service(), "mysql") == "mysql"

    def test_a_system_without_the_engine_uses_the_container_chosen(
        self, tmp_path: Path, store: NoustStore
    ) -> None:
        bare = World(tmp_path, store, postgres=None, mysql=None)
        make_instance(store, "pg17", "postgresql", "17", port=5441)
        choose(bare, "postgresql", "postgresql@pg17")

        assert new_database_engine(bare.service(), "postgresql") == "postgresql@pg17"
        assert resolve_choice(bare.service(), "postgresql").instance == "pg17"

    def test_another_engine_is_left_alone(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        choose(world, "mysql", "mysql@legacy")

        assert new_database_engine(world.service(), "postgresql") == "postgresql"
        assert resolve_choice(world.service(), "postgresql").instance is None

    def test_the_other_flavour_of_the_family_is_left_alone(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        choose(world, "mysql", "mysql@legacy")
        make_instance(world.store, "maria", "mariadb", "10.11", port=3309)
        choose(world, "mariadb", "mysql@maria")

        # Typed as the engine of a MySQL system, it is the MySQL default; typed as MariaDB, its own.
        assert new_database_engine(world.service(), "mysql") == "mysql@legacy"
        assert new_database_engine(world.service(), "mariadb") == "mysql@maria"

    def test_a_choice_whose_server_is_not_finished_or_is_gone_is_the_systems(
        self, world: World
    ) -> None:
        make_instance(world.store, "busy", "mysql", "8.0", state="creating", port=3307)
        choose(world, "mysql", "mysql@busy")
        assert new_database_engine(world.service(), "mysql") == "mysql"

        choose(world, "mysql", "mysql@gone")
        assert new_database_engine(world.service(), "mysql") == "mysql"
        assert resolve_choice(world.service(), "mysql").instance is None

    def test_a_name_that_is_not_an_engine_is_left_for_the_caller_to_refuse(
        self, world: World
    ) -> None:
        assert new_database_engine(world.service(), "oracle") == "oracle"
