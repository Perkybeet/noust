# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database dumps sealed on the server's disk, from the engine's ``backup`` to its ``restore`` (H-08).

The engine is the shared fake that really writes dump files, the destination the shared fake
rclone, and openssl is real (a runner that hands openssl to the real program and everything else to
the fakes). What is pinned: every dump an engine makes is sealed when sealing is on, whichever
engine and whoever asked (a policy, a safety copy), without any engine saying so; what is recorded
and sent is the stored file; a check, a restore and the put-back after a failed restore read the
plain dump; a dump made before sealing, or imported, still goes through the same door; and a dump
whose key is gone says so instead of being fed to the engine's loader.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import backup_files
from noust.core.backup_files import BackupKeys, is_sealed
from noust.core.ens import envelope
from noust.core.exceptions import BackupError, DatabaseBackupError
from noust.core.runner import CommandResult, SubprocessRunner, set_runner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.backup_destination_files import DestinationFileManager, file_sha256
from noust.managers.database.backup_verify import check_dump
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.service import DatabaseService
from tests.database_backup_support import (
    PG_ARCHIVE,
    PG_LISTING,
    RcloneRunner,
    make_engine,
    make_service,
)
from tests.test_database_backups import FakeScheduler, add_destination

pytestmark = pytest.mark.allow_subprocess


class OpensslRunner(RcloneRunner):
    """The shared fake runner, with the one program whose behaviour is under test made real."""

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:
        if argv and argv[0] == "openssl":
            kwargs.pop("secrets", None)
            kwargs.pop("stdin_path", None)
            return SubprocessRunner().run(argv, **kwargs)
        return super().run(argv, **kwargs)


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BackupKeys:
    """Sealing turned on, with the keys in a secret store of the test's own."""
    store = SecretStore(root=tmp_path / "sealing-secrets")
    monkeypatch.setattr(backup_files, "SecretStore", lambda: store)
    monkeypatch.setattr(backup_files.paths, "backup_dir", lambda: tmp_path / "scratch-root")
    ring = BackupKeys(store)
    ring.make_key()
    return ring


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[OpensslRunner]:
    fake = OpensslRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def dumps_dir(tmp_path: Path) -> Path:
    path = tmp_path / "dumps"
    path.mkdir()
    return path


@pytest.fixture
def engine(dumps_dir: Path) -> type[BaseDatabaseManager]:
    return make_engine(dumps_dir)


@pytest.fixture
def service(
    tmp_path: Path, engine: type[BaseDatabaseManager], runner: OpensslRunner
) -> DatabaseService:
    return make_service(tmp_path, [engine])


@pytest.fixture
def backups(service: DatabaseService, runner: OpensslRunner) -> DatabaseBackups:
    add_destination(runner)
    return DatabaseBackups(
        service,
        scheduler=FakeScheduler(),  # type: ignore[arg-type]
        destinations=DestinationFileManager(runner=runner),
    )


@pytest.fixture
def loaded(engine: type[BaseDatabaseManager], monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """What the engine's loader was handed, as bytes."""
    seen: list[bytes] = []
    original = engine._load_backup

    def load(self: Any, database: str, backup_path: Path, **kwargs: Any) -> None:
        seen.append(Path(backup_path).read_bytes())
        return original(self, database, backup_path, **kwargs)

    monkeypatch.setattr(engine, "_load_backup", load)
    return seen


# ------------------------------------------------------------------ making a dump


def test_a_dump_an_engine_makes_is_sealed_without_the_engine_saying_so(
    backups: DatabaseBackups, keys: BackupKeys
) -> None:
    taken = backups.dump("postgresql", "shop")

    assert is_sealed(taken.info.path)
    assert PG_ARCHIVE not in taken.info.path.read_bytes()
    assert taken.info.size == taken.info.path.stat().st_size, "the size is the stored file's"


def test_what_is_recorded_and_sent_is_the_stored_file(
    backups: DatabaseBackups, keys: BackupKeys, runner: OpensslRunner
) -> None:
    taken = backups.dump("postgresql", "shop")

    summary = backups.push("postgresql", taken.name, "nas")

    sent = runner.root / "nas" / "wasm-backups" / "databases" / "postgresql" / "shop" / taken.name
    assert sent.read_bytes() == taken.info.path.read_bytes()
    assert summary["verified_by"] == "md5"
    assert file_sha256(sent) == file_sha256(taken.info.path)


def test_a_dump_made_with_sealing_off_stays_in_clear_and_still_restores(
    backups: DatabaseBackups, keys: BackupKeys, loaded: list[bytes], dumps_dir: Path
) -> None:
    keys.stop_sealing()
    clear = backups.dump("postgresql", "shop")
    keys.make_key()
    sealed = backups.dump("postgresql", "shop")

    assert not is_sealed(clear.info.path) and is_sealed(sealed.info.path)
    for dump in (clear, sealed):
        backups.restore("postgresql", "shop", dump.name, safety_backup=False)
    assert loaded == [PG_ARCHIVE, PG_ARCHIVE]


# ------------------------------------------------------------------ reading a dump


def test_a_check_reads_the_plain_dump(
    backups: DatabaseBackups, keys: BackupKeys, engine: type[BaseDatabaseManager]
) -> None:
    taken = backups.dump("postgresql", "shop")

    result = check_dump(engine(), taken.info.path)

    assert result.ok, result.detail


def test_a_restore_hands_the_loader_the_plain_dump_and_names_the_file_it_was_given(
    backups: DatabaseBackups, keys: BackupKeys, loaded: list[bytes], tmp_path: Path
) -> None:
    taken = backups.dump("postgresql", "shop")

    outcome = backups.restore("postgresql", "shop", taken.name, safety_backup=False)

    assert loaded == [PG_ARCHIVE]
    assert outcome.source == taken.info.path, "not the decrypted copy"
    assert is_sealed(taken.info.path), "the stored file is untouched"
    assert not (tmp_path / "scratch-root" / ".open").exists() or not list(
        (tmp_path / "scratch-root" / ".open").iterdir()
    )


def test_the_safety_copy_is_sealed_and_a_failed_restore_puts_the_plain_one_back(
    backups: DatabaseBackups,
    keys: BackupKeys,
    loaded: list[bytes],
    engine: type[BaseDatabaseManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taken = backups.dump("postgresql", "shop")
    failures = iter(["pg_restore: error: out of memory"])
    inner = engine._load_backup

    def fail_the_first(self: Any, database: str, backup_path: Path, **kwargs: Any) -> None:
        engine.state["fail_load"] = next(failures, None)
        return inner(self, database, backup_path, **kwargs)

    monkeypatch.setattr(engine, "_load_backup", fail_the_first)
    engine.state["calls"].clear()

    with pytest.raises(DatabaseBackupError, match="previous contents were put back"):
        backups.restore("postgresql", "shop", taken.name, drop_existing=True)

    safety_dumps = [path for path in taken.info.path.parent.iterdir() if path != taken.info.path]
    assert len(safety_dumps) == 1 and is_sealed(safety_dumps[0])
    assert loaded == [PG_ARCHIVE, PG_ARCHIVE], "the dump, then the safety copy, both in clear"
    assert [call[0] for call in engine.state["calls"]].count("load") == 2


def test_a_dump_whose_key_is_gone_is_not_fed_to_the_loader(
    backups: DatabaseBackups, keys: BackupKeys, loaded: list[bytes]
) -> None:
    taken = backups.dump("postgresql", "shop")
    keys.store.delete(f"{backup_files.KEY_PREFIX}/{keys.current_id()}")

    with pytest.raises(BackupError, match="does not hold its key"):
        backups.restore("postgresql", "shop", taken.name, safety_backup=False)

    assert loaded == []


def test_the_check_of_a_dump_whose_key_is_gone_is_a_result_not_an_exception(
    backups: DatabaseBackups, keys: BackupKeys, engine: type[BaseDatabaseManager]
) -> None:
    taken = backups.dump("postgresql", "shop")
    keys.store.delete(f"{backup_files.KEY_PREFIX}/{keys.current_id()}")

    result = check_dump(engine(), taken.info.path)

    assert not result.ok and "does not hold its key" in result.detail


def test_every_engine_that_is_added_later_gets_the_same_door() -> None:
    """The guard is at the base class, so an engine cannot forget it."""
    from noust.managers.database import base

    class Later(BaseDatabaseManager):
        ENGINE_NAME = "later"

        def backup(
            self,
            database: str,
            output_path: Path | None = None,
            compress: bool = True,
            **kwargs: Any,
        ) -> Any:
            raise NotImplementedError

        def restore(self, database: str, backup_path: Path, *args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError

    assert getattr(Later.backup, "__noust_sealing__", False)
    assert getattr(Later.restore, "__noust_opening__", False)
    assert getattr(base.BaseDatabaseManager.restore, "__noust_opening__", False)


def test_a_backup_that_calls_its_parents_is_sealed_once(
    keys: BackupKeys, dumps_dir: Path, runner: OpensslRunner
) -> None:
    Fake = make_engine(dumps_dir)

    class Child(Fake):  # type: ignore[valid-type,misc]
        def backup(
            self,
            database: str,
            output_path: Path | None = None,
            compress: bool = True,
            **kwargs: Any,
        ) -> Any:
            return super().backup(database, output_path, compress, **kwargs)

    info = Child().backup("shop")

    assert is_sealed(info.path)
    assert backup_files.sealed_with(info.path) == keys.current_id()
