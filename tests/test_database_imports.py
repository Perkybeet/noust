# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Importing a dump (3.4, checklist H-18): the uploads, and the import that follows.

What is pinned here is what an operator moving from another provider depends
on when something goes wrong:

- an upload is resumable, refuses a chunk at the wrong offset with where to
  resume, never exceeds the size it declared, is capped, is owner-only on
  disk, belongs to who started it and is swept when abandoned;
- an import checks the file before touching the database, loads it through the
  one restore (safety copy first, put back on failure), creates the database
  when it is not there and drops it again when the load fails, and removes the
  upload it consumed;
- the options reach the engine's loader, and are on the audit trail.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import stat
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.config import Config
from noust.core.exceptions import ConfirmationRequired, DatabaseBackupError, ValidationError
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.imports import DatabaseImports
from noust.managers.database.service import DatabaseService
from noust.managers.database.uploads import (
    DEFAULT_MAX_BYTES,
    MAX_CHUNK_BYTES,
    UPLOAD_TTL,
    UploadOffsetError,
    UploadStore,
    UploadTooLargeError,
    max_upload_bytes,
)
from tests.database_backup_support import (
    PG_LISTING,
    RcloneRunner,
    make_engine,
    make_service,
)

SQL = b"-- PostgreSQL database dump\nSELECT 1;\n-- PostgreSQL database dump complete\n"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def uploads(tmp_path: Path) -> UploadStore:
    return UploadStore(tmp_path / "uploads")


# ================================================================ the uploads


class TestUploads:
    def test_a_session_is_owner_only_and_named_by_a_random_id(
        self, uploads: UploadStore, tmp_path: Path
    ) -> None:
        upload = uploads.create("dump.sql.gz", 10, owner="ana")

        directory = tmp_path / "uploads" / upload.id
        assert len(upload.id) == 32
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE((directory / "data").stat().st_mode) == 0o600
        assert stat.S_IMODE((directory / "meta.json").stat().st_mode) == 0o600
        assert upload.received == 0 and upload.complete is False

    def test_chunks_arrive_in_order_and_the_upload_completes(self, uploads: UploadStore) -> None:
        upload = uploads.create("dump.sql", 10, owner="ana")

        first = uploads.append(upload.id, 0, b"hello", owner="ana")
        second = uploads.append(upload.id, 5, b"world", owner="ana")
        done = uploads.complete(upload.id, owner="ana")

        assert (first.received, second.received) == (5, 10)
        assert done.complete is True
        assert uploads.path(upload.id).read_bytes() == b"helloworld"

    def test_a_chunk_at_the_wrong_offset_says_where_to_resume(self, uploads: UploadStore) -> None:
        upload = uploads.create("dump.sql", 10, owner="ana")
        uploads.append(upload.id, 0, b"hello", owner="ana")

        with pytest.raises(UploadOffsetError) as repeated:
            uploads.append(upload.id, 0, b"hello", owner="ana")
        with pytest.raises(UploadOffsetError) as hole:
            uploads.append(upload.id, 8, b"xx", owner="ana")

        assert repeated.value.received == 5
        assert hole.value.received == 5
        # Nothing was written twice or after a hole.
        assert uploads.get(upload.id).received == 5

    def test_resuming_after_a_lost_connection_needs_only_the_status(
        self, uploads: UploadStore
    ) -> None:
        upload = uploads.create("dump.sql", 6, owner="ana")
        uploads.append(upload.id, 0, b"abc", owner="ana")

        where = uploads.get(upload.id, owner="ana").received
        uploads.append(upload.id, where, b"def", owner="ana")

        assert uploads.complete(upload.id, owner="ana").received == 6

    def test_a_chunk_past_the_declared_size_or_too_big_is_refused(
        self, uploads: UploadStore
    ) -> None:
        upload = uploads.create("dump.sql", 4, owner="ana")

        with pytest.raises(UploadTooLargeError, match="past the size"):
            uploads.append(upload.id, 0, b"12345", owner="ana")
        with pytest.raises(UploadTooLargeError, match="at most"):
            uploads.append(upload.id, 0, b"x" * (MAX_CHUNK_BYTES + 1), owner="ana")

    def test_an_incomplete_upload_cannot_complete_or_be_read(self, uploads: UploadStore) -> None:
        upload = uploads.create("dump.sql", 10, owner="ana")
        uploads.append(upload.id, 0, b"hello", owner="ana")

        with pytest.raises(UploadOffsetError, match="5 of 10"):
            uploads.complete(upload.id, owner="ana")
        with pytest.raises(ValidationError, match="not finished"):
            uploads.path(upload.id)

    def test_a_declared_digest_is_checked_and_a_damaged_upload_is_discarded(
        self, uploads: UploadStore, tmp_path: Path
    ) -> None:
        good = uploads.create("a.sql", 5, owner="ana", sha256=hashlib.sha256(b"hello").hexdigest())
        uploads.append(good.id, 0, b"hello", owner="ana")
        assert uploads.complete(good.id, owner="ana").complete is True

        bad = uploads.create("b.sql", 5, owner="ana", sha256=hashlib.sha256(b"other").hexdigest())
        uploads.append(bad.id, 0, b"hello", owner="ana")
        with pytest.raises(ValidationError, match="SHA-256"):
            uploads.complete(bad.id, owner="ana")
        assert not (tmp_path / "uploads" / bad.id).exists()

    def test_the_size_cap_is_configurable_and_defaults_to_ten_gib(
        self, uploads: UploadStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert DEFAULT_MAX_BYTES == 10 * 1024**3
        assert max_upload_bytes() == DEFAULT_MAX_BYTES
        with pytest.raises(UploadTooLargeError, match="limit"):
            uploads.create("big.sql", DEFAULT_MAX_BYTES + 1, owner="ana")

        monkeypatch.setattr(
            Config,
            "get",
            lambda self, key, default=None: 100 if key == "databases.import_max_bytes" else default,
        )
        assert max_upload_bytes() == 100
        with pytest.raises(UploadTooLargeError, match="limit is 100"):
            uploads.create("big.sql", 101, owner="ana")
        uploads.create("ok.sql", 100, owner="ana")

    def test_a_disk_that_cannot_hold_it_is_refused_before_a_byte_moves(
        self, uploads: UploadStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import shutil

        monkeypatch.setattr(
            shutil,
            "disk_usage",
            lambda path: shutil._ntuple_diskusage(100, 99, 1),  # type: ignore[attr-defined]
        )

        with pytest.raises(UploadTooLargeError, match="Not enough space"):
            uploads.create("a.sql", 10, owner="ana")

    @pytest.mark.parametrize("name", ["evil.sh", "noext", "a.sql.exe", "x/y.sql", ""])
    def test_a_name_that_is_not_a_dump_is_refused(self, uploads: UploadStore, name: str) -> None:
        with pytest.raises(ValidationError):
            uploads.create(name, 10, owner="ana")

    def test_only_the_owner_can_continue_or_read(self, uploads: UploadStore) -> None:
        upload = uploads.create("a.sql", 5, owner="ana")

        with pytest.raises(ValidationError, match="no such upload"):
            uploads.append(upload.id, 0, b"hello", owner="eve")
        with pytest.raises(ValidationError, match="no such upload"):
            uploads.get(upload.id, owner="eve")

    @pytest.mark.parametrize("upload_id", ["../etc", "x" * 32, "", "A" * 32, "0" * 31, "0" * 33])
    def test_an_identifier_this_store_did_not_issue_names_no_path(
        self, uploads: UploadStore, upload_id: str
    ) -> None:
        with pytest.raises(ValidationError):
            uploads.get(upload_id)
        with pytest.raises(ValidationError):
            uploads.discard(upload_id)

    def test_abandoned_uploads_are_swept_and_fresh_ones_are_not(
        self, uploads: UploadStore, tmp_path: Path
    ) -> None:
        old = uploads.create("a.sql", 5, owner="ana")
        fresh = uploads.create("b.sql", 5, owner="ana")
        past = time.time() - UPLOAD_TTL - 60
        for name in ("data", "meta.json"):
            os.utime(tmp_path / "uploads" / old.id / name, (past, past))

        removed = uploads.sweep()

        assert removed == [old.id]
        assert (tmp_path / "uploads" / fresh.id).is_dir()

    def test_starting_an_upload_sweeps_the_abandoned(
        self, uploads: UploadStore, tmp_path: Path
    ) -> None:
        old = uploads.create("a.sql", 5, owner="ana")
        past = time.time() - UPLOAD_TTL - 60
        for name in ("data", "meta.json"):
            os.utime(tmp_path / "uploads" / old.id / name, (past, past))

        uploads.create("b.sql", 5, owner="ana")

        assert not (tmp_path / "uploads" / old.id).exists()

    def test_discarding_removes_the_data(self, uploads: UploadStore, tmp_path: Path) -> None:
        upload = uploads.create("a.sql", 5, owner="ana")

        uploads.discard(upload.id)

        assert not (tmp_path / "uploads" / upload.id).exists()


# ============================================================== the import


@pytest.fixture
def engine(tmp_path: Path) -> type[BaseDatabaseManager]:
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    cls = make_engine(dumps, suffix=".sql", content=SQL, databases=("shop",))
    original = cls._load_backup

    def recording(self: Any, database: str, backup_path: Path, **kwargs: Any) -> None:
        cls.state.setdefault("loaded", []).append(
            {"database": database, "path": Path(backup_path).name, "kwargs": kwargs}
        )
        try:
            original(self, database, backup_path, **kwargs)
        except DatabaseBackupError:
            if cls.state.get("fail_once"):
                # Only the first load fails: the put-back of the safety copy loads.
                cls.state["fail_load"] = None
            raise

    cls._load_backup = recording  # type: ignore[method-assign]
    return cls


@pytest.fixture
def service(tmp_path: Path, engine: type[BaseDatabaseManager], runner: RcloneRunner) -> Any:
    return make_service(tmp_path, [engine])


@pytest.fixture
def imports(service: DatabaseService, tmp_path: Path) -> DatabaseImports:
    return DatabaseImports(service, uploads=UploadStore(tmp_path / "uploads"))


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


def dump_file(directory: Path, name: str = "export.sql", data: bytes = SQL) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return path


class TestImporting:
    def test_a_new_database_is_created_loaded_and_tracked(
        self,
        imports: DatabaseImports,
        engine: type[BaseDatabaseManager],
        service: DatabaseService,
        tmp_path: Path,
    ) -> None:
        source = dump_file(tmp_path / "in")

        outcome = imports.run("postgresql", "fresh", source)

        assert outcome.created is True and outcome.replaced is False
        assert outcome.safety_copy is None
        assert outcome.format.kind == "sql"
        assert "fresh" in engine.state["dbs"]
        assert engine.state["loaded"][0]["path"] == "import.sql"
        assert service.store.get_database("fresh", "postgresql") is not None
        # The file the operator gave is left exactly where it was.
        assert source.read_bytes() == SQL

    def test_over_an_existing_database_a_safety_copy_is_taken_first(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        outcome = imports.run("postgresql", "shop", dump_file(tmp_path / "in"))

        assert outcome.created is False and outcome.replaced is False
        assert outcome.safety_copy is not None
        assert [call[0] for call in engine.state["calls"]][:2] == ["backup", "load"]

    def test_replace_drops_and_recreates_after_the_safety_copy(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        outcome = imports.run("postgresql", "shop", dump_file(tmp_path / "in"), replace=True)

        assert outcome.replaced is True
        assert [call[0] for call in engine.state["calls"]] == [
            "backup",
            "drop",
            "create",
            "load",
        ]

    def test_a_failed_load_over_a_replaced_database_puts_it_back(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        engine.state["fail_load"] = "syntax error at line 3"
        engine.state["fail_once"] = True

        with pytest.raises(DatabaseBackupError) as failure:
            imports.run("postgresql", "shop", dump_file(tmp_path / "in"), replace=True)

        assert "its previous contents were put back" in str(failure.value)
        assert "syntax error at line 3" in str(failure.value)
        # backup (safety), drop, create, load (fails), then the put-back:
        # drop, create, load of the safety copy.
        assert [call[0] for call in engine.state["calls"]] == [
            "backup",
            "drop",
            "create",
            "load",
            "drop",
            "create",
            "load",
        ]
        loaded = engine.state["loaded"]
        assert loaded[0]["path"] == "import.sql"
        assert loaded[1]["path"].startswith("postgresql-shop-")
        assert "shop" in engine.state["dbs"]

    def test_a_failed_import_into_a_new_database_leaves_no_database_behind(
        self,
        imports: DatabaseImports,
        engine: type[BaseDatabaseManager],
        service: DatabaseService,
        tmp_path: Path,
    ) -> None:
        engine.state["fail_load"] = "disk full"

        with pytest.raises(DatabaseBackupError, match="disk full"):
            imports.run("postgresql", "fresh", dump_file(tmp_path / "in"))

        assert "fresh" not in engine.state["dbs"]
        assert service.store.get_database("fresh", "postgresql") is None

    def test_a_file_that_is_not_a_dump_is_refused_before_anything_is_touched(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        source = dump_file(tmp_path / "in", "x.sql", b"root:$6$salt:19000:0:99999:7:::\n")

        with pytest.raises(DatabaseBackupError) as failure:
            imports.run("postgresql", "shop", source)

        assert "root:" not in str(failure.value)
        assert engine.state["calls"] == []

    def test_a_dump_the_engine_cannot_load_is_refused_before_anything_is_touched(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        import struct

        archive = struct.pack("<I", 0x8199E26D) + b"\x05\x00\x00\x00\x00\xff\xff\xff\xff"
        source = dump_file(tmp_path / "in", "m.archive", archive)

        with pytest.raises(DatabaseBackupError, match="cannot load it"):
            imports.run("postgresql", "shop", source)

        assert engine.state["calls"] == []

    def test_a_name_that_is_not_a_dump_name_is_refused(
        self, imports: DatabaseImports, tmp_path: Path
    ) -> None:
        with pytest.raises(ValidationError, match="does not look like a database dump"):
            imports.run("postgresql", "shop", dump_file(tmp_path / "in", "x.bak"))

    def test_bzip2_is_decompressed_for_the_engine_and_the_scratch_is_removed(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        import bz2

        source = dump_file(tmp_path / "in", "export.sql.bz2", bz2.compress(SQL))

        outcome = imports.run("postgresql", "fresh", source)

        assert outcome.format.compression == "bz2"
        assert engine.state["loaded"][0]["path"] == "import.sql"
        scratch_root = tmp_path / "uploads" / ".." / ".imports"
        assert list(scratch_root.resolve().iterdir()) == []

    def test_gzip_is_handed_on_for_the_engine_to_decompress(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        source = dump_file(tmp_path / "in", "export.sql.gz", gzip.compress(SQL))

        imports.run("postgresql", "fresh", source)

        assert engine.state["loaded"][0]["path"] == "import.sql.gz"

    def test_redis_has_nothing_to_import(self, tmp_path: Path, runner: RcloneRunner) -> None:
        cache = make_engine(tmp_path / "rdump", engine="redis", suffix=".rdb", databases=("0",))
        importer = DatabaseImports(
            make_service(tmp_path / "svc", [cache]), uploads=UploadStore(tmp_path / "uploads")
        )

        with pytest.raises(ValidationError, match="Redis"):
            importer.run("redis", "0", dump_file(tmp_path / "in"))

    def test_the_import_is_on_the_audit_trail(
        self,
        imports: DatabaseImports,
        tmp_path: Path,
        audited: list[dict[str, Any]],
    ) -> None:
        imports.run("postgresql", "fresh", dump_file(tmp_path / "in"), keep_owners=True)

        event = next(e for e in audited if e["event"] == "db.import")
        assert event["target"] == "db:postgresql/fresh"
        assert event["details"]["file"] == "export.sql"
        assert event["details"]["format"] == "sql"
        assert event["details"]["created"] is True
        assert event["details"]["keep_owners"] is True


class TestTheSuperuser:
    """PostgreSQL loads a dump from outside as the owner; the superuser is asked for (H-19)."""

    def test_an_import_is_not_loaded_as_the_superuser(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        imports.run("postgresql", "shop", dump_file(tmp_path / "in"))

        assert "as_superuser" not in engine.state["loaded"][0]["kwargs"]

    def test_a_restore_from_a_path_is_not_either(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        imports.backups.restore_file(
            "postgresql", "shop", dump_file(tmp_path / "in"), False, False, None
        )

        assert "as_superuser" not in engine.state["loaded"][0]["kwargs"]

    def test_the_superuser_needs_the_operator_to_accept_what_it_costs(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        with pytest.raises(ConfirmationRequired) as raised:
            imports.run("postgresql", "shop", dump_file(tmp_path / "in"), as_superuser=True)

        assert "COPY ... FROM PROGRAM" in raised.value.warnings[0]
        assert raised.value.field == "confirm_superuser"
        # Nothing was dumped, dropped or loaded to find that out.
        assert engine.state["calls"] == []

    def test_confirmed_it_is_loaded_as_the_superuser_and_audited_twice(
        self,
        imports: DatabaseImports,
        engine: type[BaseDatabaseManager],
        tmp_path: Path,
        audited: list[dict[str, Any]],
    ) -> None:
        imports.run(
            "postgresql",
            "shop",
            dump_file(tmp_path / "in"),
            as_superuser=True,
            confirm_superuser=True,
        )

        assert engine.state["loaded"][0]["kwargs"]["as_superuser"] is True
        decision = next(e for e in audited if e["event"] == "db.restore.superuser")
        assert decision["target"] == "db:postgresql/shop"
        done = next(e for e in audited if e["event"] == "db.import")
        assert done["details"]["as_superuser"] is True

    def test_an_engine_that_has_no_such_account_refuses_the_option(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        mysql = make_engine(tmp_path / "mdump", engine="mysql", suffix=".sql", databases=("shop",))
        importer = DatabaseImports(
            make_service(tmp_path / "svc", [mysql]), uploads=UploadStore(tmp_path / "uploads")
        )

        with pytest.raises(ValidationError, match="no superuser load"):
            importer.run(
                "mysql",
                "shop",
                dump_file(tmp_path / "in"),
                as_superuser=True,
                confirm_superuser=True,
            )


class TestOptions:
    def test_owners_are_dropped_by_default_and_the_loader_is_the_database_owner(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        imports.run("postgresql", "shop", dump_file(tmp_path / "in"))

        kwargs = engine.state["loaded"][0]["kwargs"]
        assert kwargs["keep_owners"] is False
        # Who the objects belong to is who loads them (pg_loader), not a role named here.
        assert "owner_role" not in kwargs

    def test_keep_owners_asks_the_loader_to_keep_them(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        imports.run("postgresql", "shop", dump_file(tmp_path / "in"), keep_owners=True)

        kwargs = engine.state["loaded"][0]["kwargs"]
        assert kwargs["keep_owners"] is True
        assert "owner_role" not in kwargs

    def test_a_new_database_is_loaded_isolated(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        imports.run("postgresql", "fresh", dump_file(tmp_path / "in"))

        assert engine.state["loaded"][0]["kwargs"]["isolated"] is True

    def test_no_safety_copy_is_taken_when_none_is_wanted_and_nothing_is_dropped(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        outcome = imports.run("postgresql", "shop", dump_file(tmp_path / "in"), safety_backup=False)

        assert outcome.safety_copy is None
        assert [call[0] for call in engine.state["calls"]] == ["load"]


class TestTheOwnerOfADatabaseTheImportMakes:
    def test_a_new_database_is_made_with_the_account_asked(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        imports.run("postgresql", "fresh", dump_file(tmp_path / "in"), create_owner="shopapp")

        assert engine.state["dbs"]["fresh"]["owner"] == "shopapp"

    def test_one_that_is_dropped_and_recreated_gets_it_too(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        imports.run(
            "postgresql", "shop", dump_file(tmp_path / "in"), replace=True, create_owner="shopapp"
        )

        assert engine.state["dbs"]["shop"]["owner"] == "shopapp"

    def test_one_that_is_loaded_over_keeps_the_owner_it_had(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        imports.run("postgresql", "shop", dump_file(tmp_path / "in"), create_owner="shopapp")

        assert engine.state["dbs"]["shop"]["owner"] == "app"

    def test_without_asking_the_default_stays(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        imports.run("postgresql", "fresh", dump_file(tmp_path / "in"))

        assert engine.state["dbs"]["fresh"]["owner"] is None

    def test_an_upload_carries_it_too(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager]
    ) -> None:
        upload = imports.uploads.create("export.sql", len(SQL), owner="ana")
        imports.uploads.append(upload.id, 0, SQL, owner="ana")
        imports.uploads.complete(upload.id, owner="ana")

        imports.run_upload("postgresql", "fresh", upload.id, create_owner="shopapp")

        assert engine.state["dbs"]["fresh"]["owner"] == "shopapp"

    def test_an_engine_whose_databases_belong_to_nobody_refuses_the_option(
        self, tmp_path: Path, runner: RcloneRunner
    ) -> None:
        (tmp_path / "m").mkdir()
        mysql = make_engine(tmp_path / "m", engine="mysql", suffix=".sql", content=SQL)
        other = DatabaseImports(
            make_service(tmp_path / "svc", [mysql]), uploads=UploadStore(tmp_path / "up")
        )

        with pytest.raises(ValidationError) as caught:
            other.run("mysql", "fresh", dump_file(tmp_path / "in"), create_owner="shopapp")

        assert caught.value.field == "owner"
        assert "fresh" not in mysql.state["dbs"]


class TestUploadedImports:
    def test_a_finished_upload_is_imported_and_removed(
        self,
        imports: DatabaseImports,
        engine: type[BaseDatabaseManager],
        tmp_path: Path,
    ) -> None:
        store = imports.uploads
        upload = store.create("moved-from-heroku.sql", len(SQL), owner="ana")
        store.append(upload.id, 0, SQL, owner="ana")
        store.complete(upload.id, owner="ana")

        outcome = imports.run_upload("postgresql", "fresh", upload.id)

        assert outcome.created is True
        assert not (tmp_path / "uploads" / upload.id).exists()

    def test_a_failed_import_keeps_the_upload_for_the_retry(
        self,
        imports: DatabaseImports,
        engine: type[BaseDatabaseManager],
        tmp_path: Path,
    ) -> None:
        store = imports.uploads
        upload = store.create("x.sql", len(SQL), owner="ana")
        store.append(upload.id, 0, SQL, owner="ana")
        store.complete(upload.id, owner="ana")
        engine.state["fail_load"] = "boom"

        with pytest.raises(DatabaseBackupError):
            imports.run_upload("postgresql", "fresh", upload.id)

        assert (tmp_path / "uploads" / upload.id / "data").is_file()

    def test_an_unfinished_upload_is_not_imported(
        self, imports: DatabaseImports, engine: type[BaseDatabaseManager]
    ) -> None:
        upload = imports.uploads.create("x.sql", 10, owner="ana")

        with pytest.raises(ValidationError, match="not finished"):
            imports.run_upload("postgresql", "fresh", upload.id)

        assert engine.state["calls"] == []

    def test_inspect_says_what_it_is_and_whether_the_engine_can_load_it(
        self, imports: DatabaseImports, tmp_path: Path
    ) -> None:
        answer = imports.inspect(dump_file(tmp_path / "in"), engine="postgresql")

        assert answer["format"]["kind"] == "sql"
        assert answer["loadable"] is True
        assert answer["size"] == len(SQL)
