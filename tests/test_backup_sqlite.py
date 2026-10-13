# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A SQLite database in use is copied whole by an application backup (checklist H-26)."""

from __future__ import annotations

import io
import sqlite3
import tarfile
from pathlib import Path

import pytest

from noust.core.runner import FakeRunner
from noust.managers.backup_manager import BackupManager
from noust.managers.sqlite_snapshot import COMPANION_SUFFIXES, companions_of, is_sqlite, snapshot
from noust.managers.volume_backups import database_in_archive


def keep_everything(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """A filter that leaves nothing out."""
    return info


@pytest.fixture
def manager() -> BackupManager:
    """A backup manager with nothing to run."""
    return BackupManager(runner=FakeRunner())


def busy_database(path: Path) -> sqlite3.Connection:
    """
    Make a WAL-mode database whose last transaction is only in the -wal file.

    Returns:
        The open connection: closing it would checkpoint the data away, and an
        application holds its database open the same way.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    connection.execute("INSERT INTO notes (body) VALUES ('committed, only in the wal')")
    return connection


def archive_of(
    manager: BackupManager, root: Path, tmp_path: Path
) -> tuple[tarfile.TarFile, list[str]]:
    """Archive a tree and open the result."""
    buffer = io.BytesIO()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        snapshotted = manager._add_tree(archive, root, "app", keep_everything, scratch)
    buffer.seek(0)
    return tarfile.open(fileobj=buffer, mode="r:gz"), snapshotted


class TestTheHelper:
    def test_a_database_is_told_from_anything_else(self, tmp_path: Path) -> None:
        database = tmp_path / "a.db"
        sqlite3.connect(database).execute("CREATE TABLE t (x)").connection.close()
        text = tmp_path / "notes.txt"
        text.write_text("hello")

        assert is_sqlite(database) and not is_sqlite(text) and not is_sqlite(tmp_path)
        assert not is_sqlite(tmp_path / "missing")

    def test_a_snapshot_holds_what_is_only_in_the_wal(self, tmp_path: Path) -> None:
        live = tmp_path / "live.db"
        connection = busy_database(live)
        try:
            copy = tmp_path / "copy.db"
            snapshot(live, copy)
        finally:
            connection.close()

        rows = sqlite3.connect(copy).execute("SELECT body FROM notes").fetchall()
        assert rows == [("committed, only in the wal",)]

    def test_companions_are_named_for_every_database(self) -> None:
        assert companions_of({"a.db"}) == {f"a.db{suffix}" for suffix in COMPANION_SUFFIXES}


class TestTheApplicationsArchive:
    def test_a_live_database_is_in_the_archive_with_its_committed_data(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        root = tmp_path / "app"
        connection = busy_database(root / "shared" / "data" / "kuma.db")
        (root / "index.js").write_text("console.log(1)\n")
        try:
            archive, snapshotted = archive_of(manager, root, tmp_path)
        finally:
            connection.close()

        assert snapshotted == ["shared/data/kuma.db"]
        member = archive.extractfile("app/shared/data/kuma.db")
        assert member is not None
        restored = tmp_path / "restored.db"
        restored.write_bytes(member.read())
        assert sqlite3.connect(restored).execute("SELECT body FROM notes").fetchall() == [
            ("committed, only in the wal",)
        ]
        assert archive.extractfile("app/index.js").read() == b"console.log(1)\n"  # type: ignore[union-attr]

    def test_the_files_sqlite_keeps_beside_it_are_not_archived_twice(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        root = tmp_path / "app"
        connection = busy_database(root / "data.db")
        try:
            assert (root / "data.db-wal").exists()
            archive, _ = archive_of(manager, root, tmp_path)
        finally:
            connection.close()

        names = archive.getnames()
        assert "app/data.db" in names
        assert not any(name.endswith(("-wal", "-shm", "-journal")) for name in names)

    def test_a_file_that_only_looks_like_a_database_is_copied_as_it_is(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        root = tmp_path / "app"
        root.mkdir()
        (root / "broken.db").write_bytes(b"SQLite format 3\x00" + b"\x01" * 100)
        archive, snapshotted = archive_of(manager, root, tmp_path)

        assert snapshotted == []
        assert archive.extractfile("app/broken.db").read().startswith(b"SQLite format 3")  # type: ignore[union-attr]

    def test_the_tree_is_otherwise_what_tarfile_would_have_archived(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        root = tmp_path / "app"
        (root / "a" / "b").mkdir(parents=True)
        (root / "a" / "b" / "c.txt").write_text("c")
        (root / "empty").mkdir()
        (root / "link").symlink_to("a/b/c.txt")
        (root / "dirlink").symlink_to("a")
        expected = io.BytesIO()
        with tarfile.open(fileobj=expected, mode="w:gz") as reference:
            reference.add(root, arcname="app", recursive=True)
        expected.seek(0)

        ours, _ = archive_of(manager, root, tmp_path)

        assert sorted(ours.getnames()) == sorted(
            tarfile.open(fileobj=expected, mode="r:gz").getnames()
        )
        assert ours.getmember("app/link").issym() and ours.getmember("app/dirlink").issym()

    def test_an_excluded_directory_is_not_walked(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        root = tmp_path / "app"
        (root / "node_modules" / "left-pad").mkdir(parents=True)
        (root / "node_modules" / "left-pad" / "index.js").write_text("x")
        (root / "src").mkdir()
        (root / "src" / "main.js").write_text("y")

        def drop_modules(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            return None if "node_modules" in info.name else info

        buffer = io.BytesIO()
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            manager._add_tree(archive, root, "app", drop_modules, scratch)
        buffer.seek(0)

        names = tarfile.open(fileobj=buffer, mode="r:gz").getnames()
        assert "app/src/main.js" in names
        assert not any("node_modules" in name for name in names)


class TestALiveDatabaseVolume:
    """A volume that holds an engine's data directory is said to be a risk when copied live."""

    def archive(self, tmp_path: Path, *names: str) -> Path:
        path = tmp_path / "volume.tar.gz"
        with tarfile.open(path, "w:gz") as handle:
            for name in names:
                info = tarfile.TarInfo(name)
                info.size = 1
                handle.addfile(info, io.BytesIO(b"x"))
        return path

    @pytest.mark.parametrize(
        ("marker", "engine"),
        [
            ("PG_VERSION", "PostgreSQL"),
            ("ibdata1", "MySQL or MariaDB"),
            ("WiredTiger.wt", "MongoDB"),
        ],
    )
    def test_the_marker_of_an_engine_names_it(
        self, tmp_path: Path, marker: str, engine: str
    ) -> None:
        path = self.archive(tmp_path, "./base/1/123", f"./{marker}", "./postgresql.conf")

        assert database_in_archive(path) == engine

    def test_an_ordinary_volume_is_not_a_database(self, tmp_path: Path) -> None:
        path = self.archive(tmp_path, "./uploads/a.png", "./config.json")

        assert database_in_archive(path) is None

    def test_something_that_is_not_an_archive_says_nothing(self, tmp_path: Path) -> None:
        junk = tmp_path / "junk.tar.gz"
        junk.write_bytes(b"nope")

        assert database_in_archive(junk) is None
