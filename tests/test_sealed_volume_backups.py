# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's data backed up sealed on the server's disk (checklist H-08).

The fixtures are those of ``tests/test_volume_backups.py`` and openssl is real. What is pinned:
the archive that stays on the disk is an envelope, its recorded size and checksum are the stored
file's (so the sidecar's MAC authenticates what is there), a verification reads through it and
unpacks it, a restore puts the data back, a push sends the stored file, and an archive whose key is
gone is refused with the fix instead of being unpacked.
"""

# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import backup_files
from noust.core.backup_files import BackupKeys, is_sealed
from noust.core.ens import envelope
from noust.core.exceptions import BackupError
from noust.core.runner import SubprocessRunner
from noust.core.secrets import SecretStore
from noust.managers.backup_destination_files import file_sha256
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
    """Sealing on, in a secret store of the test's own, with openssl handed to the real one."""
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
    ring = BackupKeys(store)
    ring.make_key()
    return ring


def test_the_archive_on_the_disk_is_sealed_and_its_record_is_of_the_stored_file(
    manager: VolumeBackupManager, keys: BackupKeys
) -> None:
    (entry,) = manager.create(DOMAIN, targets=["path:uploads"])

    archive = manager.archive_path(entry)

    assert is_sealed(archive) and b"alpha" not in archive.read_bytes()
    assert entry.size_bytes == archive.stat().st_size
    assert entry.checksum == file_sha256(archive)


def test_a_verification_reads_through_it_and_unpacks_it(
    manager: VolumeBackupManager, keys: BackupKeys
) -> None:
    (entry,) = manager.create(DOMAIN, targets=["path:uploads"])

    verdict = manager.verify(entry.id, deep=True)

    assert verdict["valid"] is True, verdict["errors"]
    assert verdict["mac"] == "valid"


def test_a_restore_puts_the_data_back_from_a_sealed_archive(
    manager: VolumeBackupManager, keys: BackupKeys, app_dir: Path
) -> None:
    (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
    uploads = app_dir / "shared" / "uploads"
    (uploads / "a.txt").write_text("lost")

    manager.restore(entry.id)

    assert (uploads / "a.txt").read_text() == "alpha"
    assert (uploads / "sub" / "b.txt").read_text() == "beta"
    assert is_sealed(manager.archive_path(entry)), "the stored archive stays sealed"


def test_a_push_sends_the_stored_file_and_the_copy_restores(
    manager: VolumeBackupManager, keys: BackupKeys, runner: RcloneRunner, app_dir: Path
) -> None:
    from noust.managers.backup_destination_files import DestinationFileManager

    DestinationFileManager(runner=runner).add(
        "nas", "sftp", {"host": "nas.example.com", "user": "noust", "pass": "s3cr3t"}
    )
    (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
    manager.push(entry.id, "nas")
    manager.delete(entry.id)
    uploads = app_dir / "shared" / "uploads"
    (uploads / "a.txt").write_text("lost")

    manager.restore_remote("nas", DOMAIN, "path-uploads", f"{entry.id}.tar.gz")

    assert (uploads / "a.txt").read_text() == "alpha"


def test_an_archive_whose_key_is_gone_is_refused_with_the_fix(
    manager: VolumeBackupManager, keys: BackupKeys, app_dir: Path
) -> None:
    (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
    keys.store.delete(f"{backup_files.KEY_PREFIX}/{keys.current_id()}")
    uploads = app_dir / "shared" / "uploads"
    (uploads / "a.txt").write_text("untouched")

    verdict = manager.verify(entry.id)
    assert verdict["valid"] is False and any(
        "does not hold its key" in e for e in verdict["errors"]
    )
    with pytest.raises(BackupError):
        manager.restore(entry.id)

    assert (uploads / "a.txt").read_text() == "untouched"


def test_a_database_engines_data_directory_is_still_recognised_inside_a_sealed_archive(
    keys: BackupKeys, tmp_path: Path
) -> None:
    import tarfile

    from noust.managers.volume_backups import database_in_archive

    plain = tmp_path / "volume.tar.gz"
    source = tmp_path / "PG_VERSION"
    source.write_text("16")
    with tarfile.open(plain, "w:gz") as tar:
        tar.add(source, arcname="data/PG_VERSION")
    backup_files.seal_in_place(plain, keys=keys, runner=SubprocessRunner())

    assert is_sealed(plain)
    assert database_in_archive(plain) == "PostgreSQL"
