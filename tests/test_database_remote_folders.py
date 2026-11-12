# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The folder a database engine's dumps live in on a backup destination (item 191).

An engine that runs in a container is named by a key (``mysql@shop-example-com.mysql``,
``postgresql@pg16``), and the key is what a dump, a policy and a link are filed under. The remote
folder was built from that key as it is, and the destination's validator of folders refuses ``@``
and ``.``: the dump and the restore test worked, and the upload failed every night with "Invalid
folder on the destination". What is pinned:

- one function names the folder of an engine, for the dumps a policy sends and for the listing
  of what a destination holds, so the two cannot disagree;
- the key is spelled the way the dump's own file name spells it (``@`` becomes ``.``), and what a
  system engine was uploaded under before does not move;
- every shape a key can take is sent, listed and read back through the real destination manager;
- a hostile key is refused before it becomes part of a path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core.exceptions import BackupError, ValidationError
from noust.core.store import NoustStore
from noust.managers.backup_destination_files import DestinationFileManager, file_sha256
from noust.managers.database.backups import DatabaseBackups, remote_folder
from tests.support.database_backup_support import PG_ARCHIVE, RcloneRunner
from tests.support.database_backups import FakeScheduler, add_destination
from tests.support.database_instance_support import World, build_world, close_world

COMPOSE_KEY = "mysql@shop-example-com.mysql"
CONTAINER_KEY = "postgresql@pg16"


class TestTheFolderOfAnEngine:
    """The one place an engine's folder is spelled."""

    @pytest.mark.parametrize(
        ("engine", "folder"),
        [
            ("postgresql", "databases/postgresql/shop"),
            ("mysql", "databases/mysql/shop"),
            ("mongodb", "databases/mongodb/shop"),
            (COMPOSE_KEY, "databases/mysql.shop-example-com.mysql/shop"),
            (CONTAINER_KEY, "databases/postgresql.pg16/shop"),
            ("postgresql@jobs-example-net.db", "databases/postgresql.jobs-example-net.db/shop"),
            ("postgresql@Reports_2.db-1", "databases/postgresql.Reports_2.db-1/shop"),
        ],
    )
    def test_a_system_engine_keeps_its_folder_and_an_instance_gets_a_name_a_folder_can_have(
        self, engine: str, folder: str
    ) -> None:
        assert remote_folder(engine, "shop") == folder

    def test_a_redis_instance_has_one_folder_whatever_its_slot(self) -> None:
        assert remote_folder("redis", "3") == "databases/redis/instance"
        assert remote_folder("redis@cache-example-com.redis", "3") == (
            "databases/redis.cache-example-com.redis/instance"
        )

    @pytest.mark.parametrize(
        "key",
        [
            "mysql@",
            "mysql@../etc",
            "mysql@a/b",
            "postgresql@a b",
            "postgresql@.hidden",
            "postgresql@a\n",
            "mysql@a@b",
            "@pg16",
            "postgresql@" + "a" * 300,
            "postgresql@" + "a" * 250,
            "../etc",
            "post gres",
            "PostgreSQL",
            "",
            "a",
        ],
    )
    def test_a_key_that_is_not_an_instance_is_refused_before_it_is_a_path(self, key: str) -> None:
        with pytest.raises(ValidationError):
            remote_folder(key, "shop")


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def rclone(tmp_path: Path) -> RcloneRunner:
    # Handed to each destination manager, never installed as the process's runner: the world of
    # the instance tests installs the Docker host's, and the two must not replace each other.
    fake = RcloneRunner(tmp_path / "remote")
    add_destination(fake)
    return fake


class TestEveryKeyIsSentListedAndReadBack:
    """The real destination manager takes the folder of each shape of key."""

    @pytest.mark.parametrize("engine", ["postgresql", "mysql", COMPOSE_KEY, CONTAINER_KEY])
    def test_a_file_goes_to_the_folder_of_its_engine_and_is_listed_there(
        self, rclone: RcloneRunner, engine: str, tmp_path: Path
    ) -> None:
        destinations = DestinationFileManager(runner=rclone)
        dump = tmp_path / "shop.dump"
        dump.write_bytes(PG_ARCHIVE)
        folder = remote_folder(engine, "shop")

        summary = destinations.push_file(
            dump, "nas", folder, sidecar={"version": 1, "sha256": file_sha256(dump)}
        )

        assert summary["folder"] == folder
        assert [entry.name for entry in destinations.list_files("nas", folder)] == ["shop.dump"]
        engines = destinations.list_folders("nas", "databases")
        assert folder.split("/")[1] in engines

    def test_a_folder_still_cannot_be_a_dot_or_climb(self, rclone: RcloneRunner) -> None:
        destinations = DestinationFileManager(runner=rclone)

        for folder in (
            "databases/./shop",
            "databases/../shop",
            "databases/.hidden/shop",
            "databases/a/../../shop",
            "databases/mysql.x/ ../shop",
        ):
            with pytest.raises(BackupError):
                destinations.list_files("nas", folder)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    yield build_world(tmp_path, monkeypatch)
    close_world()


class TestAnInstancesDumpsReachTheDestination:
    """A policy of a database in a container sends its dump, as a policy of the host's engine does."""

    def make_backups(
        self, world: World, rclone: RcloneRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> DatabaseBackups:
        from noust.managers.database.postgres import PostgresManager

        monkeypatch.setattr(PostgresManager, "BACKUP_DIR", tmp_path / "dumps")
        world.managed.create("pg16", "postgresql", version="16", memory="512m")
        world.host.calls.clear()
        world.host.script(["docker", "exec"], stdout="PGDMP\x01\x0e\x00 a custom archive\n")
        return DatabaseBackups(
            world.fresh().service,
            scheduler=FakeScheduler(),  # type: ignore[arg-type]
            destinations=DestinationFileManager(runner=rclone),
        )

    def test_the_dump_of_a_managed_instance_is_sent_under_the_folder_of_its_key(
        self,
        world: World,
        rclone: RcloneRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        backups = self.make_backups(world, rclone, monkeypatch, tmp_path)
        taken = backups.dump(CONTAINER_KEY, "appdb", verify=False)
        record = taken.record
        assert record is not None
        record.verify_status = "ok"
        backups.records.save_dump(record)

        summary = backups.push(CONTAINER_KEY, taken.name, "nas")

        assert summary["folder"] == "databases/postgresql.pg16/appdb"
        remote = rclone.root / "nas" / "noust-backups" / "databases" / "postgresql.pg16" / "appdb"
        assert (remote / taken.name).is_file()
        (view,) = backups.list_dumps(CONTAINER_KEY, "appdb")
        (copy,) = view.to_dict()["destinations"]
        assert copy["folder"] == "databases/postgresql.pg16/appdb"
        assert backups.remote_databases(CONTAINER_KEY, "nas") == ["appdb"]
        (listed,) = backups.remote_dumps(CONTAINER_KEY, "appdb", "nas")
        assert listed["name"] == taken.name and listed["own"] is True

    def test_the_databases_of_the_host_engine_are_not_listed_under_an_instance(
        self,
        world: World,
        rclone: RcloneRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        backups = self.make_backups(world, rclone, monkeypatch, tmp_path)
        (rclone.root / "nas" / "noust-backups" / "databases" / "postgresql" / "legacy").mkdir(
            parents=True
        )

        assert backups.remote_databases(CONTAINER_KEY, "nas") == []
        assert backups.remote_databases("postgresql", "nas") == ["legacy"]
