# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's backup sealed on the server's disk (checklist H-08).

The fixtures are those of ``tests/test_backup_restore_safety.py`` and openssl is real. What is
pinned: the archive that stays on the disk is an envelope; the size and the checksum the sidecar
records, and its MAC authenticates, are the stored file's; verification reads through it and
unpacks it; a restore puts the files back; the database dumps staged inside it are not sealed a
second time; and an archive that was altered, or whose key is gone, restores nothing.
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
from noust.core.runner import FakeRunner, SubprocessRunner
from noust.core.secrets import SecretStore
from noust.managers.backup_manager import BackupManager
from tests.test_backup_restore_safety import (  # noqa: F401 - fixtures
    APP_NAME,
    DOMAIN,
    FakeApp,
    _use_store,
    apps_dir,
    manager,
)

pytestmark = pytest.mark.allow_subprocess


@pytest.fixture(autouse=True)
def cheap_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(envelope, "SCRYPT_N", 2**10)


@pytest.fixture
def keys(tmp_path: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> BackupKeys:
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


def archive_of(manager: BackupManager, backup_id: str) -> Path:
    """The archive on the disk of a backup."""
    metadata = manager.get_backup(backup_id)
    assert metadata is not None
    return manager.local_files(metadata)[0]


def test_the_archive_on_the_disk_is_sealed_and_its_record_is_of_the_stored_file(
    manager: BackupManager, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_store(monkeypatch, FakeApp())

    metadata = manager.create(DOMAIN)

    archive = archive_of(manager, metadata.id)
    assert is_sealed(archive) and b"SECRET=1" not in archive.read_bytes()
    assert metadata.size_bytes == archive.stat().st_size
    assert metadata.checksum == manager.checksum_of(archive)


def test_a_verification_reads_through_it_and_unpacks_it(
    manager: BackupManager, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_store(monkeypatch, FakeApp())
    metadata = manager.create(DOMAIN)

    verdict = manager.verify(metadata.id)

    assert verdict["valid"] is True, verdict["errors"]
    assert verdict["checksum_ok"] is True and verdict["files_ok"] is True
    assert verdict["mac"] == "valid"


def test_a_restore_puts_the_files_back_from_a_sealed_archive(
    manager: BackupManager,
    keys: BackupKeys,
    apps_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_store(monkeypatch, FakeApp())
    metadata = manager.create(DOMAIN)
    source = apps_dir / APP_NAME / "src" / "index.js"
    source.write_text("console.log('changed after the backup')\n")

    assert manager.restore(metadata.id) is True

    assert source.read_text() == "console.log('original')\n"
    assert (apps_dir / APP_NAME / ".env").read_text() == "SECRET=1\n"
    assert is_sealed(archive_of(manager, metadata.id))
    scratch = backup_files._scratch_root()
    assert not scratch.exists() or list(scratch.iterdir()) == []


def test_the_archive_is_opened_once_for_a_whole_restore(
    manager: BackupManager,
    keys: BackupKeys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manifest is read several times and the archive extracted once: one decryption."""
    _use_store(monkeypatch, FakeApp())
    metadata = manager.create(DOMAIN)
    decryptions: list[Path] = []
    original = backup_files.open_envelope

    def counting(source: Path, *args: Any, **kwargs: Any) -> Any:
        decryptions.append(source)
        return original(source, *args, **kwargs)

    monkeypatch.setattr(backup_files, "open_envelope", counting)

    manager.restore(metadata.id)

    assert len(decryptions) == 1


def test_the_dumps_staged_inside_the_archive_are_not_sealed_a_second_time(
    manager: BackupManager,
    keys: BackupKeys,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_store(monkeypatch, FakeApp())
    staged = tmp_path / "staged.dump"
    seen: list[bool] = []

    def dump_databases(domain: str, directory: Path, **kwargs: Any) -> list[dict[str, Any]]:
        staged.write_bytes(b"a dump")
        seen.append(backup_files.seal_in_place(staged, keys=keys, runner=SubprocessRunner()))
        return []

    monkeypatch.setattr(manager, "_dump_databases", dump_databases)

    manager.create(DOMAIN, include_databases=True)

    assert seen == [False] and staged.read_bytes() == b"a dump"


def test_an_altered_archive_is_refused_and_restores_nothing(
    manager: BackupManager,
    keys: BackupKeys,
    apps_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_store(monkeypatch, FakeApp())
    metadata = manager.create(DOMAIN)
    archive = archive_of(manager, metadata.id)
    blob = bytearray(archive.read_bytes())
    blob[-5] ^= 0xFF
    archive.write_bytes(bytes(blob))
    source = apps_dir / APP_NAME / "src" / "index.js"
    source.write_text("untouched\n")

    assert manager.verify(metadata.id)["valid"] is False
    with pytest.raises(BackupError):
        manager.restore(metadata.id)

    assert source.read_text() == "untouched\n"


def test_an_archive_whose_key_is_gone_restores_nothing_and_says_how_to_get_it_back(
    manager: BackupManager,
    keys: BackupKeys,
    apps_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_store(monkeypatch, FakeApp())
    metadata = manager.create(DOMAIN)
    keys.store.delete(f"{backup_files.KEY_PREFIX}/{keys.current_id()}")
    source = apps_dir / APP_NAME / "src" / "index.js"
    source.write_text("untouched\n")

    with pytest.raises(BackupError, match="does not hold its key") as caught:
        manager.restore(metadata.id)

    assert "show-key" in str(caught.value.details)
    assert source.read_text() == "untouched\n"


def test_an_archive_made_before_sealing_still_restores_after_it_was_turned_on(
    manager: BackupManager,
    keys: BackupKeys,
    apps_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_store(monkeypatch, FakeApp())
    keys.stop_sealing()
    before = manager.create(DOMAIN)
    keys.make_key()
    after = manager.create(DOMAIN)
    source = apps_dir / APP_NAME / "src" / "index.js"

    assert not is_sealed(archive_of(manager, before.id))
    assert is_sealed(archive_of(manager, after.id))
    for backup in (before, after):
        source.write_text("changed\n")
        assert manager.restore(backup.id) is True
        assert source.read_text() == "console.log('original')\n"
