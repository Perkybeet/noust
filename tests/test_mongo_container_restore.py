# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A container's MongoDB archive is restored where it was asked to go, and nowhere else.

``mongodump --archive`` keeps the namespaces it was dumped from, and
mongorestore writes them back there unless told otherwise. Before this was
defended, restoring ``shop``'s archive "as a new database", or testing it in a
temporary database, ran ``mongorestore --archive --drop`` and replaced
``shop`` itself, with no safety copy, since the target was new. And the
verification listed the archive with ``tar -tzf``, so every container dump
failed it.

What is defended:

- the archive's own prelude says which database it holds, and the restore is
  aimed with ``--nsInclude`` (and ``--nsFrom``/``--nsTo`` when the names
  differ), never ``--drop``;
- an archive that cannot be aimed (not an archive, several databases, a
  pattern in a name) is refused before anything is touched;
- the verification reads the prelude, and a gzipped archive cut short fails.
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

import pytest

from noust.core.exceptions import DatabaseBackupError
from noust.core.runner import FakeRunner
from noust.managers.database import mongo_archive
from noust.managers.database.backup_verify import check_dump, restore_test
from noust.managers.database.instances import DatabaseInstance
from noust.managers.database.mongo_archive import ArchiveError, read_prelude, read_whole
from noust.managers.database.mongodb import MongoDBManager


def bson(**fields: str | int) -> bytes:
    """Encode a flat BSON document of strings and int32s."""
    body = b""
    for name, value in fields.items():
        key = name.encode() + b"\x00"
        if isinstance(value, int):
            body += b"\x10" + key + struct.pack("<i", value)
        else:
            raw = value.encode() + b"\x00"
            body += b"\x02" + key + struct.pack("<i", len(raw)) + raw
    return struct.pack("<i", len(body) + 5) + body + b"\x00"


def archive(*namespaces: tuple[str, str]) -> bytes:
    """A mongodump archive's prelude, followed by a body the reader never needs."""
    prelude = struct.pack("<I", 0x8199E26D)
    prelude += bson(concurrent_collections=4, version="0.1", server_version="7.0.0")
    for db, collection in namespaces:
        prelude += bson(db=db, collection=collection, metadata="{}", size=0)
    prelude += b"\xff\xff\xff\xff"
    return prelude + bson(db=namespaces[0][0] if namespaces else "", collection="x") + b"body"


@pytest.fixture
def manager(runner: FakeRunner, tmp_path: Path) -> MongoDBManager:
    """A MongoDB in a Compose service, whose databases exist when listed."""
    instance = DatabaseInstance(
        key="mongodb@shop.mongo",
        engine="mongodb",
        flavour="mongo",
        container="shop-mongo-1",
        container_id="m",
        image="mongo:7",
        project="shop",
        service="mongo",
        state="running",
        env_names=frozenset({"MONGO_INITDB_ROOT_USERNAME", "MONGO_INITDB_ROOT_PASSWORD"}),
        settings={"MONGO_INITDB_ROOT_USERNAME": "root"},
    )
    bound = MongoDBManager().bind(instance)
    bound.BACKUP_DIR = tmp_path / "backups"
    existing = {"shop"}
    bound.database_exists = lambda name: name in existing  # type: ignore[method-assign]
    bound.drop_database = lambda name, force=False: existing.discard(name)  # type: ignore[method-assign,assignment]
    return bound


def restores(runner: FakeRunner) -> list[tuple[str, ...]]:
    """Every mongorestore call, from its program on."""
    found = []
    for call in runner.calls:
        if "mongorestore" in call:
            found.append(tuple(call[call.index("mongorestore") :]))
    return found


def dump_of(tmp_path: Path, *databases: str, compress: bool = True) -> Path:
    content = archive(*[(db, "orders") for db in databases])
    path = tmp_path / ("mongodb.shop.mongo.shop.archive" + (".gz" if compress else ""))
    path.write_bytes(gzip.compress(content) if compress else content)
    return path


class TestTheRestoreIsAimed:
    def test_a_restore_as_a_new_database_is_renamed_and_never_drops(
        self, manager: MongoDBManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        dump = dump_of(tmp_path, "shop")

        manager.restore("shop_copy", dump, safety_backup=False, isolated=True)

        (call,) = restores(runner)
        assert "--drop" not in call
        assert call[call.index("--nsInclude") + 1] == "shop.*"
        assert call[call.index("--nsFrom") + 1] == "shop.*"
        assert call[call.index("--nsTo") + 1] == "shop_copy.*"

    def test_a_restore_over_the_same_database_includes_only_it(
        self, manager: MongoDBManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        dump = dump_of(tmp_path, "shop", "admin")

        manager.restore("shop", dump, safety_backup=False)

        (call,) = restores(runner)
        assert call[call.index("--nsInclude") + 1] == "shop.*"
        assert "--nsFrom" not in call and "--drop" not in call

    def test_the_restore_test_never_reaches_the_original(
        self, manager: MongoDBManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        dump = dump_of(tmp_path, "shop")

        restore_test(manager, dump)

        (call,) = restores(runner)
        assert call[call.index("--nsFrom") + 1] == "shop.*"
        assert call[call.index("--nsTo") + 1].startswith("noust_verify_")

    def test_an_archive_of_several_databases_without_the_target_is_refused_first(
        self, manager: MongoDBManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        dump = dump_of(tmp_path, "shop", "blog")

        with pytest.raises(DatabaseBackupError) as raised:
            manager.restore("other", dump)

        assert "shop, blog" in (raised.value.details or "")
        assert runner.calls == []

    def test_a_host_tarball_is_refused_before_anything_runs(
        self, manager: MongoDBManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        tarball = tmp_path / "mongodb-shop.tar.gz"
        tarball.write_bytes(gzip.compress(b"shop/orders.bson" + b"\x00" * 600))

        with pytest.raises(DatabaseBackupError) as raised:
            manager.restore("shop", tarball)

        assert "host" in (raised.value.details or "")
        assert runner.calls == []

    def test_a_name_mongorestore_would_read_as_a_pattern_is_refused(
        self, manager: MongoDBManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        dump = dump_of(tmp_path, "sh*")

        with pytest.raises(DatabaseBackupError):
            manager.restore("shop_copy", dump, safety_backup=False)

        assert restores(runner) == []


class TestTheVerification:
    def test_a_container_archive_is_read_by_its_prelude(
        self, manager: MongoDBManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        outcome = check_dump(manager, dump_of(tmp_path, "shop"))

        assert (outcome.ok, outcome.method) == (True, "archive prelude")
        assert "shop" in outcome.detail
        assert not any("tar" in call for call in runner.calls)

    def test_a_plain_archive_is_read_too(
        self, manager: MongoDBManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        outcome = check_dump(manager, dump_of(tmp_path, "shop", compress=False))

        assert outcome.ok is True

    def test_a_gzipped_archive_cut_short_fails(
        self, manager: MongoDBManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        whole = dump_of(tmp_path, "shop")
        cut = tmp_path / "cut.archive.gz"
        cut.write_bytes(whole.read_bytes()[:-12])

        outcome = check_dump(manager, cut)

        assert outcome.ok is False

    def test_a_host_tarball_is_still_listed_by_tar(
        self, manager: MongoDBManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        runner.script(("tar", "-tzf"), stdout="shop/\nshop/orders.bson\n")
        tarball = tmp_path / "mongodb-shop.tar.gz"
        tarball.write_bytes(gzip.compress(b"shop/orders.bson" + b"\x00" * 600))

        outcome = check_dump(manager, tarball)

        assert (outcome.ok, outcome.method) == (True, "tar -tzf")


class TestThePreludeReader:
    def test_it_names_each_database_once(self, tmp_path: Path) -> None:
        path = tmp_path / "a.archive"
        path.write_bytes(archive(("shop", "a"), ("shop", "b"), ("blog", "c")))

        assert read_prelude(path).databases == ("shop", "blog")

    def test_a_prelude_cut_short_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "a.archive"
        path.write_bytes(archive(("shop", "a"))[:30])

        with pytest.raises(ArchiveError):
            read_prelude(path)

    def test_an_impossible_document_length_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "a.archive"
        path.write_bytes(struct.pack("<I", 0x8199E26D) + struct.pack("<i", 2**30))

        with pytest.raises(ArchiveError):
            read_prelude(path)

    def test_a_database_or_collection_name_mongodb_would_refuse_is_an_error(
        self, tmp_path: Path
    ) -> None:
        """Names past MongoDB's own limits are a forged prelude, not one to aim with."""
        path = tmp_path / "a.archive"
        path.write_bytes(archive(("d" * 63, "c" * 255)))
        assert read_prelude(path).namespaces == (("d" * 63, "c" * 255),)

        path.write_bytes(archive(("d" * 64, "orders")))
        with pytest.raises(ArchiveError, match="database of more than 63 bytes"):
            read_prelude(path)

        path.write_bytes(archive(("shop", "c" * 256)))
        with pytest.raises(ArchiveError, match="collection of more than 255 bytes"):
            read_prelude(path)

    def test_a_prelude_of_too_many_namespaces_is_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mongo_archive, "_MAX_NAMESPACES", 3)
        path = tmp_path / "a.archive"
        path.write_bytes(archive(*[("shop", f"c{n}") for n in range(4)]))

        with pytest.raises(ArchiveError, match="more than 3 collections"):
            read_prelude(path)

    def test_a_prelude_past_its_byte_budget_is_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mongo_archive, "_MAX_PRELUDE", 300)
        path = tmp_path / "a.archive"
        path.write_bytes(archive(*[("shop", "c" * 60 + str(n)) for n in range(5)]))

        with pytest.raises(ArchiveError, match="runs past"):
            read_prelude(path)


class TestReadingAGzippedArchiveIsBounded:
    """A gzip bomb is untrusted input: its expansion and its time are capped."""

    def test_an_archive_that_expands_past_its_cap_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(mongo_archive, "_EXPANSION_FLOOR", 1024 * 1024)
        path = tmp_path / "bomb.archive.gz"
        path.write_bytes(gzip.compress(archive(("shop", "a")) + b"\x00" * (8 * 1024 * 1024)))

        with pytest.raises(ArchiveError, match="expands past"):
            read_whole(path)

    def test_a_read_past_its_deadline_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = iter(range(0, 10_000, 100))
        monkeypatch.setattr(mongo_archive.time, "monotonic", lambda: next(clock))
        path = tmp_path / "slow.archive.gz"
        path.write_bytes(gzip.compress(archive(("shop", "a")) + b"\x00" * (3 * 1024 * 1024)))

        with pytest.raises(ArchiveError, match="longer than 150 seconds"):
            read_whole(path, deadline=150)

    def test_an_honest_archive_is_read_to_its_end(self, tmp_path: Path) -> None:
        content = archive(("shop", "a")) + b"\x00" * (3 * 1024 * 1024)
        path = tmp_path / "ok.archive.gz"
        path.write_bytes(gzip.compress(content))

        assert read_whole(path) == len(content)

    def test_a_plain_archive_is_not_read_at_all(self, tmp_path: Path) -> None:
        path = tmp_path / "plain.archive"
        path.write_bytes(archive(("shop", "a")))

        assert read_whole(path) == path.stat().st_size


def test_a_container_restore_gets_a_deadline_that_fits_its_archive(
    manager: MongoDBManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A big archive used to be killed at the fixed two-hour transfer deadline."""
    from noust.managers.database import mongodb as mongodb_module

    asked: list[Path] = []

    def fitted(path: Path) -> int:
        asked.append(path)
        return 54321

    monkeypatch.setattr(mongodb_module, "restore_timeout", fitted, raising=False)
    deadlines: list[int] = []
    real_exec = manager._exec

    def exec_(argv, **kwargs):  # type: ignore[no-untyped-def]
        if "mongorestore" in argv:
            deadlines.append(kwargs.get("timeout"))
        return real_exec(argv, **kwargs)

    monkeypatch.setattr(manager, "_exec", exec_)

    manager.restore("shop", dump_of(tmp_path, "shop"), safety_backup=False)

    assert deadlines == [54321]
    (staged,) = asked
    assert staged.name.endswith("-restore-shop.archive")
