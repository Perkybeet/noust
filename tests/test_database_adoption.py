# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Adding one database an engine holds to Noust (4.1, item 103).

A database Noust finds on a server and does not manage is not a kind of database: it is a
database with the state "not added", and the action is "Add to Noust". 4.0 could add all of an
engine's at once; the screen adds one. What is pinned: one database is recorded exactly the way
adopting them all records it (one implementation), what is already Noust's is refused with a
code, and what is not there is not made up.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import DatabaseExistsError, DatabaseNotFoundError
from noust.core.store import NoustStore
from noust.managers.database.adoption import adopt_database, noust_state
from noust.managers.database.base import DatabaseInfo
from noust.managers.database.service import DatabaseView
from tests.support.database_choice import noble  # noqa: F401 - the autouse fixture
from tests.support.database_servers import World


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "state" / "noust.db")
    yield instance
    instance.close()
    NoustStore.reset_instance()


@pytest.fixture
def world(tmp_path: Path, store: NoustStore) -> World:
    return World(tmp_path, store, mysql=None)


class TestAddingOne:
    def test_the_database_becomes_noust_s_and_only_that_one(self, world: World) -> None:
        pg = world.classes["postgresql"]
        pg.dbs["second"] = {"owner": "app", "size": None}
        service = world.service()

        added = adopt_database(service, "postgresql", "appdb")

        assert added == "postgresql/appdb"
        tracked = {v.name: v.tracked for v in service.list_databases("postgresql")}
        assert tracked == {"appdb": True, "second": False}

    def test_it_is_recorded_the_way_adopting_all_records_it(self, world: World) -> None:
        service = world.service()
        adopt_database(service, "postgresql", "appdb")
        one = service.store.get_database("appdb", "postgresql")
        service.store.delete_database("appdb", "postgresql")

        assert service.adopt("postgresql") == ["postgresql/appdb"]
        every = service.store.get_database("appdb", "postgresql")

        assert one is not None and every is not None
        assert (one.host, one.port, one.encoding, one.engine) == (
            every.host,
            every.port,
            every.encoding,
            every.engine,
        )

    def test_it_is_audited(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        service = world.service()
        events: list[tuple[str, str, dict]] = []
        monkeypatch.setattr(
            service, "audit", lambda event, target, *a, **kw: events.append((event, target, kw))
        )

        adopt_database(service, "postgresql", "appdb")

        assert events == [("db.adopt", "postgresql", {"databases": "postgresql/appdb"})]

    def test_what_is_already_noust_s_is_refused_with_a_code(self, world: World) -> None:
        service = world.service()
        adopt_database(service, "postgresql", "appdb")

        with pytest.raises(DatabaseExistsError) as caught:
            adopt_database(service, "postgresql", "appdb")

        assert caught.value.coded is not None
        assert caught.value.coded.code == "databases.already_added"
        assert dict(caught.value.coded.params) == {"name": "appdb", "engine": "postgresql"}

    def test_what_the_engine_does_not_hold_is_not_made_up(self, world: World) -> None:
        pg = world.classes["postgresql"]

        def absent(self, name):  # type: ignore[no-untyped-def]
            if name not in type(self).dbs:
                # What every real engine says of a database it does not hold.
                raise DatabaseNotFoundError(f"Database '{name}' not found")
            return DatabaseInfo(name=name, engine=type(self).ENGINE_NAME)

        pg.get_database_info = absent  # type: ignore[method-assign]

        with pytest.raises(DatabaseNotFoundError):
            adopt_database(world.service(), "postgresql", "nothing")

        assert world.service().store.get_database("nothing", "postgresql") is None


class TestTheStateIsNotWhereItRuns:
    @pytest.mark.parametrize(
        ("view", "state"),
        [
            (DatabaseView(name="a", engine="postgresql"), "not_added"),
            (DatabaseView(name="a", engine="postgresql", tracked=True), "added"),
            (DatabaseView(name="a", engine="postgresql", tracked=True, missing=True), "missing"),
            (
                DatabaseView(name="a", engine="postgresql", tracked=True, unverified=True),
                "unverified",
            ),
        ],
    )
    def test_the_four_states(self, view: DatabaseView, state: str) -> None:
        assert noust_state(view) == state
