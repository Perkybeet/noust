# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The list of database servers: what the new Databases screen draws from (4.1, item 103).

The owner got lost in 4.0 between engines, instances, containers and databases. The model is two
things: the databases an application uses, and the **database servers** they run on, whether the
system's engine or a container Noust runs. This list is the one place that joins them (the
engines' route and the instances' route stay for what is done to each): engine, version, where it
runs, its default, when its support ends and how many databases it holds. What is defended:

- **one list for every place a server can run**: the system's, a container Noust made (stopped
  ones too, which Docker no longer shows) and a container an application's stack runs;
- **exactly one default per engine**: the operator's choice when there is one that still exists,
  else the system's, else the only one, else the oldest. The choice does not change what a
  request that names an engine already did unless it is a container Noust made;
- **the date support ends is on every server**, and the count of databases is the caller's
  (a team member counts what it can see);
- **a server that cannot be read says so** instead of vanishing: Docker not answering is a
  problem, not an empty list.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.db_server_defaults import DbServerDefaultStore
from noust.core.exceptions import DatabaseNotFoundError, ValidationError
from noust.core.store import NoustStore
from tests.support.database_choice import noble  # noqa: F401 - the autouse fixture
from tests.support.database_servers import World, make_instance, stack_container


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


def keys(listing) -> list[str]:  # type: ignore[no-untyped-def]
    return [server["key"] for server in listing.servers]


def by_key(listing, key: str) -> dict:  # type: ignore[no-untyped-def]
    return next(server for server in listing.servers if server["key"] == key)


class TestOneListForEveryPlaceAServerRuns:
    def test_the_engines_of_the_system_and_the_containers_noust_made_are_one_list(
        self, world: World
    ) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        listing = world.servers().all()

        assert keys(listing) == ["postgresql", "mysql", "mysql@legacy"]
        system = by_key(listing, "mysql")
        assert (system["runs_in"], system["flavour"], system["name"]) == ("system", "mysql", None)
        noust = by_key(listing, "mysql@legacy")
        assert (noust["runs_in"], noust["flavour"], noust["name"]) == (
            "noust_container",
            "mysql",
            "legacy",
        )

    def test_an_engine_that_is_not_installed_is_not_a_server(self, tmp_path: Path, store) -> None:  # type: ignore[no-untyped-def]
        world = World(tmp_path, store, postgres=None, mysql=None)

        assert keys(world.servers().all()) == []

    def test_a_container_noust_made_that_is_stopped_is_still_listed(self, world: World) -> None:
        # Stopping its unit removes its container, so Docker no longer shows it.
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        listing = world.servers().all()

        stopped = by_key(listing, "mysql@legacy")
        assert (stopped["running"], stopped["state"]) == (False, "stopped")
        assert "start" in stopped["actions"] and "stop" not in stopped["actions"]

    def test_a_container_an_applications_stack_runs_is_listed_as_the_applications(
        self, world: World
    ) -> None:
        world.containers.append(stack_container("shop", "db", "postgres:16-alpine"))

        listing = world.servers().all()

        theirs = next(s for s in listing.servers if s["runs_in"] == "app_container")
        assert theirs["image"] == "postgres:16-alpine"
        assert theirs["name"] is None
        assert theirs["default"] is False
        assert not {"set_default", "remove", "upgrade", "start", "stop"} & set(theirs["actions"])

    def test_an_instance_being_made_says_so_and_offers_nothing(self, world: World) -> None:
        make_instance(world.store, "new", "mysql", "8.4", state="creating", port=3308)

        server = by_key(world.servers().all(), "mysql@new")

        assert server["state"] == "creating" and server["actions"] == []

    def test_the_list_can_be_asked_for_one_engine(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        assert keys(world.servers().all(engine="mysql")) == ["mysql", "mysql@legacy"]
        assert keys(world.servers().all(engine="pg")) == ["postgresql"]

    def test_docker_not_answering_is_a_problem_and_the_system_engines_stay(
        self, world: World
    ) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        world.docker_down = True

        listing = world.servers().all()

        assert keys(listing) == ["postgresql", "mysql"]
        [problem] = listing.problems
        assert problem.kind == "docker" and "Docker does not answer" in problem.message


class TestWhatEachOneSays:
    def test_the_version_and_when_its_support_ends(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        listing = world.servers().all()

        old = by_key(listing, "mysql@legacy")
        assert (old["version"], old["support"], old["end_of_life"]) == (
            "8.0",
            "ended",
            "2026-04-30",
        )
        assert old["display_name"] == "MySQL"
        system = by_key(listing, "mysql")
        assert system["version"] == "8.0.36" and system["support"] == "ended"

    def test_a_container_says_its_image_its_digest_its_port_and_its_memory(
        self, world: World
    ) -> None:
        row = make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        old = by_key(world.servers().all(), "mysql@legacy")

        assert (old["image"], old["digest"]) == ("mysql", row.digest)
        assert (old["port"], old["memory_limit_mb"]) == (3307, 1024)
        assert old["custom_image"] is False

    def test_a_flavour_that_is_mariadb_on_the_system_is_named_so(
        self, tmp_path: Path, store
    ) -> None:  # type: ignore[no-untyped-def]
        world = World(tmp_path, store, mysql="mariadb")

        system = by_key(world.servers().all(), "mysql")

        assert system["flavour"] == "mariadb" and system["display_name"] == "MariaDB"


class TestTheDefault:
    def test_the_system_engine_is_the_default_when_nobody_chose(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        listing = world.servers().all()

        assert by_key(listing, "mysql")["default"] is True
        assert by_key(listing, "mysql")["default_source"] == "system"
        assert by_key(listing, "mysql@legacy")["default"] is False
        assert by_key(listing, "mysql@legacy")["default_source"] is None

    def test_with_no_system_engine_the_only_container_is_it(self, tmp_path: Path, store) -> None:  # type: ignore[no-untyped-def]
        world = World(tmp_path, store, postgres=None, mysql=None)
        make_instance(store, "only", "mysql", "8.4", port=3307)

        only = by_key(world.servers().all(), "mysql@only")

        assert (only["default"], only["default_source"]) == (True, "only")

    def test_with_several_containers_and_no_system_engine_it_is_the_oldest(
        self, tmp_path: Path, store
    ) -> None:  # type: ignore[no-untyped-def]
        world = World(tmp_path, store, postgres=None, mysql=None)
        make_instance(store, "first", "mysql", "8.0", port=3307)
        make_instance(store, "second", "mysql", "8.4", port=3308)

        listing = world.servers().all()

        assert by_key(listing, "mysql@first")["default"] is True
        assert by_key(listing, "mysql@first")["default_source"] == "oldest"
        assert by_key(listing, "mysql@second")["default"] is False

    def test_what_the_operator_chose_wins(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        chosen = world.servers().set_default("mysql@legacy")

        assert (chosen["default"], chosen["default_source"]) == (True, "chosen")
        listing = world.servers().all()
        assert by_key(listing, "mysql")["default"] is False
        assert [s["key"] for s in listing.servers if s["default"] and s["flavour"] == "mysql"] == [
            "mysql@legacy"
        ]

    def test_each_engine_has_exactly_one(self, world: World) -> None:
        make_instance(world.store, "pg16", "postgresql", "16", port=5441)
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        listing = world.servers().all()

        for flavour in ("postgresql", "mysql"):
            assert sum(1 for s in listing.servers if s["flavour"] == flavour and s["default"]) == 1

    def test_the_system_engine_can_be_chosen_back(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        world.servers().set_default("mysql@legacy")

        chosen = world.servers().set_default("mysql")

        assert (chosen["key"], chosen["default_source"]) == ("mysql", "chosen")

    def test_taking_the_choice_back_returns_to_the_automatic_default(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        world.servers().set_default("mysql@legacy")

        taken = world.servers().clear_default("mysql@legacy")

        assert (taken["default"], taken["default_source"]) == (False, None)
        assert by_key(world.servers().all(), "mysql")["default_source"] == "system"

    def test_taking_back_what_was_not_chosen_changes_nothing(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        taken = world.servers().clear_default("mysql@legacy")

        assert taken["default"] is False
        assert DbServerDefaultStore(world.store).all() == {}

    def test_a_choice_that_names_a_server_that_is_gone_is_ignored(self, world: World) -> None:
        DbServerDefaultStore(world.store).set("mysql", "mysql@gone", set_by=None)

        listing = world.servers().all()

        assert by_key(listing, "mysql")["default"] is True
        assert by_key(listing, "mysql")["default_source"] == "system"

    def test_choosing_is_audited(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)
        events: list[tuple[str, str, dict]] = []
        servers = world.servers()
        monkeypatch.setattr(
            servers.service,
            "audit",
            lambda event, target, *a, **kw: events.append((event, target, kw)),
        )

        servers.set_default("mysql@legacy")
        servers.clear_default("mysql@legacy")

        assert [(e[0], e[1]) for e in events] == [
            ("db.server.default", "mysql@legacy"),
            ("db.server.default", "mysql@legacy"),
        ]
        assert events[0][2]["flavour"] == "mysql" and events[0][2]["chosen"] is True
        assert events[1][2]["chosen"] is False

    def test_an_unknown_server_is_a_not_found_with_a_code(self, world: World) -> None:
        with pytest.raises(DatabaseNotFoundError) as caught:
            world.servers().set_default("mysql@nothing")

        assert caught.value.coded is not None
        assert caught.value.coded.code == "database_servers.unknown"
        assert caught.value.coded.params["command"] == "noust db servers"

    def test_an_applications_container_cannot_be_the_default(self, world: World) -> None:
        world.containers.append(stack_container("shop", "db", "postgres:16-alpine"))
        theirs = next(s for s in world.servers().all().servers if s["runs_in"] == "app_container")

        with pytest.raises(ValidationError) as caught:
            world.servers().set_default(theirs["key"])

        assert caught.value.coded is not None
        assert caught.value.coded.code == "database_servers.default_app"
        assert caught.value.field == "server"

    def test_one_that_is_still_being_made_cannot_be_either(self, world: World) -> None:
        make_instance(world.store, "new", "mysql", "8.4", state="creating", port=3308)

        with pytest.raises(ValidationError) as caught:
            world.servers().set_default("mysql@new")

        assert caught.value.coded is not None
        assert caught.value.coded.code == "database_servers.default_not_ready"
        assert caught.value.coded.params["state"] == "creating"

    def test_a_key_that_is_not_one_is_refused_before_anything_is_looked_up(
        self, world: World
    ) -> None:
        with pytest.raises(ValidationError) as caught:
            world.servers().set_default("mysql@a;b")

        assert caught.value.field == "server"


class TestHowManyDatabasesEachHolds:
    def test_the_count_is_the_databases_the_engine_lists(self, world: World) -> None:
        listing = world.servers().all()

        assert by_key(listing, "postgresql")["databases"] == 1
        assert by_key(listing, "mysql")["databases"] == 0

    def test_the_caller_counts_only_what_it_may_see(self, world: World) -> None:
        listing = world.servers().all(visible=lambda view: view.name != "appdb")

        assert by_key(listing, "postgresql")["databases"] == 0
        assert by_key(listing, "mysql")["databases"] == 0

    def test_a_server_that_could_not_be_read_has_no_count_and_a_problem(self, world: World) -> None:
        from noust.core.exceptions import DatabaseQueryError

        def refuse(self):  # type: ignore[no-untyped-def]
            raise DatabaseQueryError("access denied", details="store the account")

        world.classes["postgresql"].list_databases = refuse  # type: ignore[method-assign]

        listing = world.servers().all()

        assert by_key(listing, "postgresql")["databases"] is None
        assert by_key(listing, "mysql")["databases"] == 0
        assert [problem.engine for problem in listing.problems] == ["postgresql"]

    def test_a_server_that_is_stopped_or_unreadable_has_no_count(self, world: World) -> None:
        make_instance(world.store, "legacy", "mysql", "8.0", port=3307)

        assert by_key(world.servers().all(), "mysql@legacy")["databases"] is None
