# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An instance Noust runs goes through the same backups, policies, restore and import as the host's engines.

Nothing about an instance is special-cased in those paths: the key is what an
engine name was, so what is pinned is that it *is* carried everywhere. A dump of
a database on ``postgresql@pg16`` is named, recorded, listed and restored under
that key and never mixes with the host's ``postgresql`` dumps; a policy for it
(one database, or every database, the ones made later included) installs its
timer under the key; a dump is loaded through ``docker exec`` with the file on
stdin and no password in any argument; and the import path resolves the same
manager.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.scope import ALL_DATABASES
from tests.database_instance_support import DockerHost, World, build_world, close_world
from tests.test_database_backups import FakeScheduler

KEY = "postgresql@pg16"
ARCHIVE = "PGDMP\x01\x0e\x00 a custom archive\n"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    yield build_world(tmp_path, monkeypatch)
    close_world()


@pytest.fixture
def backups(world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DatabaseBackups:
    monkeypatch.setattr(PostgresManager, "BACKUP_DIR", tmp_path / "dumps")
    world.managed.create("pg16", "postgresql", version="16", memory="512m")
    world.host.calls.clear()
    world.host.script(["docker", "exec"], stdout=ARCHIVE)
    return DatabaseBackups(
        world.fresh().service,
        scheduler=FakeScheduler(),  # type: ignore[arg-type]
        destinations=DestinationFileManager(runner=world.host),
    )


def argv_of(host: DockerHost, program: str) -> list[tuple[str, ...]]:
    return [call for call in host.calls if program in call and call[:2] == ("docker", "exec")]


class TestDumps:
    def test_a_dump_is_named_recorded_and_listed_under_the_instances_key(
        self, backups: DatabaseBackups
    ) -> None:
        view = backups.dump(KEY, "appdb", verify=False)

        assert view.info.path.name.startswith("postgresql.pg16.appdb-")
        assert view.record is not None and view.record.engine == KEY
        assert [item.name for item in backups.list_dumps(KEY)] == [view.name]
        assert backups.list_dumps("postgresql") == []

    def test_the_dump_runs_in_the_container_without_a_password_in_any_argument(
        self, backups: DatabaseBackups, world: World
    ) -> None:
        backups.dump(KEY, "appdb", verify=False)

        (dump,) = argv_of(world.host, "pg_dump")
        assert "noust-db-pg16" in dump
        assert world.password("pg16") not in " ".join(dump)
        for call in world.host.calls:
            assert world.password("pg16") not in " ".join(call)

    def test_a_dump_that_is_a_policy_is_found_by_the_policy_of_its_database(
        self, backups: DatabaseBackups
    ) -> None:
        backups.dump(KEY, "appdb", verify=False)

        assert backups.service.dump_path(KEY, backups.list_dumps(KEY)[0].name).exists()


class TestPolicies:
    def test_a_policy_for_one_database_installs_its_timer_under_the_key(
        self, backups: DatabaseBackups
    ) -> None:
        view = backups.set_policy(KEY, "appdb", schedule="daily")

        assert view.engine == KEY and view.database == "appdb"
        assert (KEY, "appdb") in backups.scheduler.installed  # type: ignore[attr-defined]

    def test_a_policy_for_every_database_covers_the_instance_and_not_the_host(
        self, backups: DatabaseBackups
    ) -> None:
        backups.set_policy(KEY, ALL_DATABASES, schedule="daily")

        covered = backups.unprotected(KEY)

        assert covered == []

    def test_without_a_policy_the_instances_databases_are_unprotected(
        self, backups: DatabaseBackups
    ) -> None:
        assert backups.unprotected(KEY) == [{"engine": KEY, "database": "appdb"}]

    def test_removing_a_policy_removes_its_timer(self, backups: DatabaseBackups) -> None:
        backups.set_policy(KEY, "appdb", schedule="daily")

        assert backups.remove_policy(KEY, "appdb") is True
        assert (KEY, "appdb") not in backups.scheduler.installed  # type: ignore[attr-defined]


class TestRestore:
    def test_a_dump_is_restored_into_the_instance_with_the_file_on_stdin(
        self, backups: DatabaseBackups, world: World
    ) -> None:
        view = backups.dump(KEY, "appdb", verify=False)
        world.host.calls.clear()
        world.host.stdin_paths.clear()

        outcome = backups.restore(KEY, "appdb", view.name, drop_existing=True, safety_backup=False)

        assert outcome.database == "appdb"
        restores = argv_of(world.host, "pg_restore")
        assert restores, "the restore ran a client"
        assert all(str(view.info.path) not in " ".join(call) for call in restores)
        assert world.host.stdin_paths == [view.info.path]


class TestImport:
    def test_a_dump_from_elsewhere_is_loaded_into_a_new_database_of_the_instance(
        self, backups: DatabaseBackups, world: World, tmp_path: Path
    ) -> None:
        from noust.managers.database.imports import DatabaseImports
        from noust.managers.database.uploads import UploadStore

        source = tmp_path / "from-another-provider.sql"
        source.write_text(
            "-- PostgreSQL database dump\nSELECT 1;\n-- PostgreSQL database dump complete\n"
        )
        world.host.calls.clear()

        outcome = DatabaseImports(
            backups.service, backups=backups, uploads=UploadStore(tmp_path / "uploads")
        ).run(KEY, "migrated", source)

        assert outcome.engine == KEY
        assert outcome.created is True
        assert "migrated" in world.host.databases
        loads = [
            call for call in world.host.calls if call[:2] == ("docker", "exec") and "psql" in call
        ]
        assert loads and all("noust-db-pg16" in call for call in loads)
        assert not any(world.password("pg16") in " ".join(call) for call in world.host.calls)

    def test_it_refuses_an_instance_that_is_stopped_with_how_to_start_it(
        self, backups: DatabaseBackups, world: World, tmp_path: Path
    ) -> None:
        from noust.core.exceptions import DatabaseEngineError
        from noust.managers.database.imports import DatabaseImports
        from noust.managers.database.uploads import UploadStore

        world.managed.stop("pg16")
        source = tmp_path / "dump.sql"
        source.write_text(
            "-- PostgreSQL database dump\nSELECT 1;\n-- PostgreSQL database dump complete\n"
        )
        service = world.fresh().service

        with pytest.raises(DatabaseEngineError) as caught:
            DatabaseImports(service, uploads=UploadStore(tmp_path / "uploads")).run(
                KEY, "migrated", source
            )

        assert "not running" in caught.value.message
        assert "noust db start" in caught.value.details
