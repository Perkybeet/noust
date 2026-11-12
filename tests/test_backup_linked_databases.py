# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The databases an application's backup dumps (item 192).

An application's backup dumped the databases the store's ``databases`` table says it owns. A
database Noust found in the application's own environment and recorded as a link
(``record_detected_link``: the ``.env`` names it, nothing is rewritten) is a row of
``database_links`` and has no row in ``databases``, so an application with its database in a
container instance was backed up with files only and the log said "No database was backed up",
as it also did for every application that has no database at all. What is pinned:

- the databases an application uses are what the database service says (links and owned rows,
  each once): the backup asks it, and so does the application's Database tab;
- a database linked to an application is dumped wherever it lives, a container's included;
- an application with no database is backed up without a word about it, and an application
  whose databases were all dumped says nothing either.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.store import App, Database
from noust.managers.backup_manager import DATABASES_DIR, PAYLOAD_DIR, BackupManager
from noust.managers.database.records import DatabaseLink
from tests.support.database_instance_support import World, build_world, close_world

DOMAIN = "shop.example.com"
KEY = "postgresql@pg16"
ARCHIVE = "PGDMP\x01\x0e\x00 a custom archive\n"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    from noust.managers.database.postgres import PostgresManager

    monkeypatch.setattr(PostgresManager, "BACKUP_DIR", tmp_path / "dumps")
    built = build_world(tmp_path, monkeypatch)
    built.managed.create("pg16", "postgresql", version="16", memory="512m")
    built.host.script(["docker", "exec"], stdout=ARCHIVE)
    yield built
    close_world()


@pytest.fixture
def app(world: World, tmp_path: Path) -> App:
    """A deployed application that is not a Compose stack."""
    path = tmp_path / "apps" / "shop-example-com"
    (path / "src").mkdir(parents=True)
    (path / "src" / "index.js").write_text("console.log('shop')\n")
    return world.store.create_app(App(domain=DOMAIN, app_path=str(path), app_type="nodejs"))


@pytest.fixture
def manager(world: World, tmp_path: Path, app: App) -> Iterator[BackupManager]:
    backups = BackupManager(verbose=False, runner=world.host)
    backups.backup_dir = tmp_path / "backups"
    previous = backups.config.get("apps_directory")
    backups.config.set("apps_directory", str(tmp_path / "apps"))
    yield backups
    backups.config.set("apps_directory", previous)


def link(world: World, app: App, engine: str, database: str) -> None:
    world.service.records.save_link(
        DatabaseLink(app_id=app.id or 0, engine=engine, db_name=database, env_var="DATABASE_URL")
    )


class TestTheDatabasesAnApplicationUses:
    """One answer, from the database service, that the backup and the Database tab share."""

    def test_a_link_with_no_row_of_the_store_is_one_of_them(self, world: World, app: App) -> None:
        link(world, app, KEY, "appdb")

        assert world.service.records.databases_of(app.id or 0) == [(KEY, "appdb")]

    def test_a_database_both_linked_and_owned_is_named_once(self, world: World, app: App) -> None:
        link(world, app, KEY, "appdb")
        world.store.create_database(Database(app_id=app.id, name="appdb", engine=KEY))
        world.store.create_database(Database(app_id=app.id, name="audit", engine=KEY))

        assert world.service.records.databases_of(app.id or 0) == [(KEY, "appdb"), (KEY, "audit")]

    def test_an_application_with_none_has_none(self, world: World, app: App) -> None:
        assert world.service.records.databases_of(app.id or 0) == []

    def test_the_database_tab_lists_what_the_backup_dumps(self, world: World, app: App) -> None:
        link(world, app, KEY, "appdb")
        world.store.create_database(Database(app_id=app.id, name="audit", engine=KEY))

        tab = [(view.engine, view.database) for view in world.service.app_databases(DOMAIN)]

        assert sorted(tab) == sorted(world.service.records.databases_of(app.id or 0))


class TestTheBackupOfAnApplication:
    def test_a_database_linked_in_a_container_instance_is_dumped(
        self, manager: BackupManager, world: World, app: App, tmp_path: Path
    ) -> None:
        link(world, app, KEY, "appdb")

        backup = manager.create(DOMAIN, include_databases=True)

        assert backup.includes_databases is True
        (entry,) = backup.database_backups
        assert (entry["engine"], entry["name"]) == (KEY, "appdb")
        assert entry["archive_path"].startswith(f"{PAYLOAD_DIR}/{DATABASES_DIR}/postgresql.pg16-")

    def test_a_database_that_is_linked_and_owned_is_dumped_once(
        self, manager: BackupManager, world: World, app: App
    ) -> None:
        link(world, app, KEY, "appdb")
        world.store.create_database(Database(app_id=app.id, name="appdb", engine=KEY))

        backup = manager.create(DOMAIN, include_databases=True)

        assert [(e["engine"], e["name"]) for e in backup.database_backups] == [(KEY, "appdb")]

    def test_an_application_with_no_database_is_backed_up_without_a_warning(
        self, manager: BackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        said: list[str] = []
        monkeypatch.setattr(manager.logger, "warning", said.append)

        backup = manager.create(DOMAIN, include_databases=True)

        assert backup.database_backups == []
        assert said == []

    def test_a_database_that_is_dumped_says_nothing_of_the_kind(
        self, manager: BackupManager, world: World, app: App, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        said: list[str] = []
        monkeypatch.setattr(manager.logger, "warning", said.append)
        link(world, app, KEY, "appdb")

        manager.create(DOMAIN, include_databases=True)

        assert said == []
