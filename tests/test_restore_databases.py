# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The databases no application owns come back on a new server (checklist H-28, H-30).

``noust server migrate prepare`` dumps them and sends the dumps to a destination, and
``noust restore-server`` used to bring the applications back and leave these behind. What is
pinned here is how they are brought back without ever replacing anything: a database that is
already on the server is left as it is, the newest dump wins wherever it is (here or on a
destination), an engine that is not installed is said with its command, a database in an
instance is left for the instance, and one failing does not stop the next.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import BackupError, DatabaseBackupError
from noust.core.logger import Logger
from noust.core.store import Database, NoustStore
from noust.managers.restore_databases import OrphanDatabases

NOW = datetime(2026, 10, 17, 12, 0, 0)


@pytest.fixture
def store(tmp_path: Path) -> NoustStore:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    yield instance  # type: ignore[misc]
    instance.close()
    NoustStore.reset_instance()


def dump(name: str, hours_ago: float) -> Any:
    return SimpleNamespace(
        name=name,
        info=SimpleNamespace(created=NOW - timedelta(hours=hours_ago), path=Path(name)),
    )


def remote(name: str, hours_ago: float) -> dict[str, Any]:
    return {"name": name, "modified": (NOW - timedelta(hours=hours_ago)).isoformat()}


@dataclass
class Engines:
    """What each engine of the new server has: installed or not, and the databases on it."""

    installed: set[str] = field(default_factory=lambda: {"postgresql", "mysql"})
    databases: dict[str, set[str]] = field(default_factory=dict)

    def manager(self, engine: str) -> Any:
        def exists(name: str) -> bool:
            return name in self.databases.get(engine, set())

        return SimpleNamespace(
            engine_type=engine,
            ENGINE_NAME=engine,
            is_installed=lambda: engine in self.installed,
            database_exists=exists,
        )


@dataclass
class Backups:
    """The dumps on this server and on each destination, and what was restored from them."""

    local: dict[tuple[str, str], list[Any]] = field(default_factory=dict)
    held: dict[tuple[str, str, str], list[dict[str, Any]]] = field(default_factory=dict)
    policy_destinations: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    unreachable: set[str] = field(default_factory=set)
    restored: list[tuple[str, str, str, str]] = field(default_factory=list)
    fail: set[str] = field(default_factory=set)

    def list_dumps(self, engine: str, database: str) -> list[Any]:
        return list(self.local.get((engine, database), []))

    def destinations_of(self, engine: str, database: str) -> list[str]:
        return list(self.policy_destinations.get((engine, database), []))

    def remote_dumps(self, engine: str, database: str, destination: str) -> list[dict[str, Any]]:
        if destination in self.unreachable:
            raise BackupError(f"{destination} did not answer", details="connection refused")
        return list(self.held.get((engine, database, destination), []))

    def restore(self, engine: str, database: str, file_name: str, **kwargs: Any) -> None:
        if database in self.fail:
            raise DatabaseBackupError("Failed to restore database", details="role does not exist")
        assert kwargs["drop_existing"] is False and kwargs["safety_backup"] is False
        self.restored.append(("local", engine, database, file_name))

    def restore_remote(
        self, engine: str, database: str, destination: str, file_name: str, **kwargs: Any
    ) -> None:
        if database in self.fail:
            raise DatabaseBackupError("Failed to restore database", details="role does not exist")
        assert kwargs["drop_existing"] is False and kwargs["safety_backup"] is False
        self.restored.append((destination, engine, database, file_name))


def flow(engines: Engines, backups: Backups) -> OrphanDatabases:
    return OrphanDatabases(
        logger=Logger(),
        service=engines,  # type: ignore[arg-type]
        backups=backups,  # type: ignore[arg-type]
    )


def make(store: NoustStore, engine: str, name: str, app_id: int | None = None) -> None:
    store.create_database(Database(name=name, engine=engine, app_id=app_id))


class TestWhichDatabases:
    def test_only_the_ones_no_application_owns_are_brought_back(self, store: NoustStore) -> None:
        from noust.core.store import App

        app = store.create_app(App(domain="a.example.com", app_type="nodejs", app_path="/srv/a"))
        make(store, "postgresql", "owned", app.id)
        make(store, "postgresql", "loose")
        backups = Backups()
        backups.local[("postgresql", "loose")] = [dump("pg-loose.dump", 3)]

        outcomes = flow(Engines(), backups).restore(store)

        assert [(o.engine, o.name) for o in outcomes] == [("postgresql", "loose")]


class TestTheNewestDump:
    def test_a_local_dump_is_restored_when_it_is_the_newest(self, store: NoustStore) -> None:
        make(store, "postgresql", "loose")
        backups = Backups()
        backups.local[("postgresql", "loose")] = [dump("new.dump", 2), dump("old.dump", 50)]
        backups.policy_destinations[("postgresql", "loose")] = ["nas"]
        backups.held[("postgresql", "loose", "nas")] = [remote("older.dump", 20)]

        [outcome] = flow(Engines(), backups).restore(store)

        assert (outcome.status, outcome.source, outcome.dump) == ("restored", "local", "new.dump")
        assert backups.restored == [("local", "postgresql", "loose", "new.dump")]

    def test_a_dump_on_a_destination_wins_when_it_is_newer(self, store: NoustStore) -> None:
        make(store, "postgresql", "loose")
        backups = Backups()
        backups.local[("postgresql", "loose")] = [dump("old.dump", 70)]
        backups.policy_destinations[("postgresql", "loose")] = ["nas"]
        backups.held[("postgresql", "loose", "nas")] = [remote("newer.dump", 4)]

        [outcome] = flow(Engines(), backups).restore(store)

        assert (outcome.status, outcome.source, outcome.dump) == ("restored", "nas", "newer.dump")
        assert backups.restored == [("nas", "postgresql", "loose", "newer.dump")]

    def test_the_destination_a_move_was_prepared_on_is_looked_at_too(
        self, store: NoustStore
    ) -> None:
        make(store, "postgresql", "loose")
        backups = Backups()
        backups.held[("postgresql", "loose", "cloud")] = [remote("moved.dump", 1)]

        [outcome] = flow(Engines(), backups).restore(store, from_destination="cloud")

        assert (outcome.status, outcome.source) == ("restored", "cloud")

    def test_a_destination_that_does_not_answer_is_said_and_the_others_still_count(
        self, store: NoustStore
    ) -> None:
        make(store, "postgresql", "loose")
        backups = Backups()
        backups.policy_destinations[("postgresql", "loose")] = ["nas", "cloud"]
        backups.unreachable.add("nas")
        backups.held[("postgresql", "loose", "cloud")] = [remote("c.dump", 3)]

        [outcome] = flow(Engines(), backups).restore(store)

        assert outcome.status == "restored" and outcome.source == "cloud"
        assert any("nas" in note for note in outcome.notes)

    def test_a_database_nothing_dumped_is_reported_as_having_no_copy(
        self, store: NoustStore
    ) -> None:
        make(store, "postgresql", "loose")

        [outcome] = flow(Engines(), Backups()).restore(store)

        assert outcome.status == "no_dump"
        assert "noust db backup" in (outcome.error or "")


class TestWhatIsLeftAlone:
    def test_a_database_the_server_already_has_is_never_replaced(self, store: NoustStore) -> None:
        make(store, "postgresql", "loose")
        engines = Engines(databases={"postgresql": {"loose"}})
        backups = Backups()
        backups.local[("postgresql", "loose")] = [dump("x.dump", 1)]

        [outcome] = flow(engines, backups).restore(store)

        assert outcome.status == "present"
        assert backups.restored == []

    def test_an_engine_that_is_not_installed_is_named_with_its_command(
        self, store: NoustStore
    ) -> None:
        make(store, "mysql", "wiki")

        [outcome] = flow(Engines(installed={"postgresql"}), Backups()).restore(store)

        assert outcome.status == "skipped"
        assert "noust db install mysql" in (outcome.error or "")

    def test_a_database_in_an_instance_waits_for_the_instance(self, store: NoustStore) -> None:
        make(store, "postgresql@pg16.db", "inst")

        [outcome] = flow(Engines(), Backups()).restore(store)

        assert outcome.status == "skipped"
        assert "noust db instance create" in (outcome.error or "")

    def test_a_redis_snapshot_is_never_loaded_into_a_running_instance(
        self, store: NoustStore
    ) -> None:
        make(store, "redis", "0")

        [outcome] = flow(Engines(installed={"redis"}), Backups()).restore(store)

        assert outcome.status == "skipped"
        assert "replaces every key" in (outcome.error or "")


class TestFailures:
    def test_one_failing_does_not_stop_the_next_and_keeps_the_engines_words(
        self, store: NoustStore
    ) -> None:
        make(store, "postgresql", "bad")
        make(store, "postgresql", "good")
        backups = Backups()
        for name in ("bad", "good"):
            backups.local[("postgresql", name)] = [dump(f"{name}.dump", 1)]
        backups.fail.add("bad")

        outcomes = {o.name: o for o in flow(Engines(), backups).restore(store)}

        assert outcomes["bad"].status == "failed" and "role does not exist" in (
            outcomes["bad"].error or ""
        )
        assert outcomes["good"].status == "restored"
