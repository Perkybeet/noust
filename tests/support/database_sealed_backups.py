# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_database_sealed_backups.py`` that other tests build on."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core import backup_files
from noust.core.backup_files import BackupKeys
from noust.core.ens import envelope
from noust.core.runner import CommandResult, SubprocessRunner, set_runner
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore
from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.service import DatabaseService
from tests.support.database_backup_support import (
    PG_LISTING,
    RcloneRunner,
    make_engine,
    make_service,
)
from tests.support.database_backups import FakeScheduler, add_destination


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
