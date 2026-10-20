# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's data backups made in clear are sealed after the fact (checklist H-08).

The fixtures are those of the sealed volume-backup tests and openssl is real. What is pinned: the
archive on the disk becomes an envelope, the sidecar is written again with the stored file's size,
checksum and a MAC over them (so a verification passes and a restore puts the data back), one that
does not match its record stays in clear and is said, and a run that stopped before the sidecar is
repaired by running again.
"""

# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import backup_files
from noust.core.backup_files import BackupKeys, is_sealed, seal_in_place
from noust.core.ens import envelope
from noust.core.runner import SubprocessRunner
from noust.core.secrets import SecretStore
from noust.managers.backup_destination_files import file_sha256
from noust.managers.backup_sealing import SealExisting
from noust.managers.volume_backups import VolumeBackupManager
from tests.database_backup_support import RcloneRunner
from tests.test_volume_backups import (  # noqa: F401 - fixtures
    DOMAIN,
    _reset_store,
    app,
    app_dir,
    audited,
    manager,
    runner,
    services,
)

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture
def keys(tmp_path: Path, runner: RcloneRunner, monkeypatch: pytest.MonkeyPatch) -> BackupKeys:
    """Keys in a store of the test's own, openssl real, and sealing still off."""
    store = SecretStore(root=tmp_path / "sealing-secrets")
    monkeypatch.setattr(backup_files, "SecretStore", lambda: store)
    monkeypatch.setattr(backup_files.paths, "backup_dir", lambda: tmp_path / "scratch-root")
    real = SubprocessRunner()
    original = runner.run

    def run(argv: Any, **kwargs: Any) -> Any:
        if argv and argv[0] == "openssl":
            return real.run(argv, **kwargs)
        return original(argv, **kwargs)

    monkeypatch.setattr(runner, "run", run)
    return BackupKeys(store)


def sealer(manager: VolumeBackupManager, keys: BackupKeys) -> SealExisting:
    return SealExisting(keys=keys, apps=None, volumes=manager, databases=None, pitr=None)


def test_a_clear_archive_is_sealed_and_its_sidecar_describes_the_stored_file(
    manager: VolumeBackupManager, keys: BackupKeys
) -> None:
    (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
    archive = manager.archive_path(entry)
    assert not is_sealed(archive)
    keys.make_key()

    report = sealer(manager, keys).run()

    assert [(item.kind, item.name, item.state) for item in report.items] == [
        ("data", entry.id, "sealed")
    ]
    assert is_sealed(archive) and b"alpha" not in archive.read_bytes()
    stored = manager.get_backup(entry.id)
    assert stored is not None
    assert stored.size_bytes == archive.stat().st_size
    assert stored.checksum == file_sha256(archive)


def test_it_verifies_and_restores_afterwards(
    manager: VolumeBackupManager, keys: BackupKeys, app_dir: Path
) -> None:
    (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
    keys.make_key()
    sealer(manager, keys).run()
    uploads = app_dir / "shared" / "uploads"
    (uploads / "a.txt").write_text("lost")

    verdict = manager.verify(entry.id, deep=True)
    manager.restore(entry.id)

    assert verdict["valid"] is True, verdict["errors"]
    assert verdict["mac"] == "valid"
    assert (uploads / "a.txt").read_text() == "alpha"


def test_an_archive_that_does_not_match_its_record_is_left_in_clear_and_said(
    manager: VolumeBackupManager, keys: BackupKeys
) -> None:
    (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
    archive = manager.archive_path(entry)
    with archive.open("ab") as handle:
        handle.write(b"tampered")
    before = archive.read_bytes()
    keys.make_key()

    report = sealer(manager, keys).run()

    (item,) = report.items
    assert item.state == "damaged"
    assert archive.read_bytes() == before


def test_a_run_that_stopped_before_the_sidecar_is_repaired_by_running_again(
    manager: VolumeBackupManager, keys: BackupKeys
) -> None:
    (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
    keys.make_key()
    seal_in_place(manager.archive_path(entry), keys=keys, runner=SubprocessRunner())
    assert manager.verify(entry.id)["valid"] is False

    report = sealer(manager, keys).run()

    (item,) = report.items
    assert item.state == "repaired"
    assert manager.verify(entry.id)["valid"] is True
