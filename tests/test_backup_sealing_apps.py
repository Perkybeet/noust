# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Applications' archives made in clear are sealed after the fact (checklist H-08).

The fixtures are those of the sealed-backup tests and openssl is real. What is pinned:
the archive on the disk becomes an envelope; the sidecar is written again with the stored file's
size and checksum and a MAC over them, so a verification passes and a restore puts the files back;
an archive that does not match its record is left in clear and said; a run that stopped between
sealing and writing the sidecar is repaired by running again; an application that is busy is skipped
and the rest go on.
"""

# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core import backup_files
from noust.core.applock import app_lock
from noust.core.backup_files import BackupKeys, is_sealed, seal_in_place
from noust.core.ens import envelope
from noust.core.runner import FakeRunner, SubprocessRunner
from noust.core.secrets import SecretStore
from noust.managers.backup_manager import BackupManager
from noust.managers.backup_sealing import SealExisting
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


def archive_of(manager: BackupManager, backup_id: str) -> Path:
    metadata = manager.get_backup(backup_id)
    assert metadata is not None
    return manager.local_files(metadata)[0]


def sealer(manager: BackupManager, keys: BackupKeys) -> SealExisting:
    return SealExisting(keys=keys, apps=manager, volumes=None, databases=None, pitr=None)


class TestApplicationArchives:
    def test_a_clear_archive_is_sealed_and_its_sidecar_describes_the_stored_file(
        self, manager: BackupManager, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_store(monkeypatch, FakeApp())
        made = manager.create(DOMAIN)
        archive = archive_of(manager, made.id)
        assert not is_sealed(archive)
        keys.make_key()

        report = sealer(manager, keys).run()

        assert [(item.kind, item.name, item.state) for item in report.items] == [
            ("application", made.id, "sealed")
        ]
        assert is_sealed(archive) and b"SECRET=1" not in archive.read_bytes()
        stored = manager.get_backup(made.id)
        assert stored is not None
        assert stored.size_bytes == archive.stat().st_size
        assert stored.checksum == manager.checksum_of(archive)

    def test_it_verifies_and_restores_afterwards(
        self,
        manager: BackupManager,
        keys: BackupKeys,
        apps_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _use_store(monkeypatch, FakeApp())
        made = manager.create(DOMAIN)
        keys.make_key()
        sealer(manager, keys).run()

        verdict = manager.verify(made.id)
        source = apps_dir / APP_NAME / "src" / "index.js"
        source.write_text("changed after the backup\n")
        assert manager.restore(made.id) is True

        assert verdict["valid"] is True, verdict["errors"]
        assert verdict["checksum_ok"] is True and verdict["mac"] == "valid"
        assert source.read_text() == "console.log('original')\n"

    def test_the_sidecar_keeps_everything_else_it_said(
        self, manager: BackupManager, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_store(monkeypatch, FakeApp())
        made = manager.create(DOMAIN, description="before the migration", tags=["pinned"])
        keys.make_key()
        sealer(manager, keys).run()

        stored = manager.get_backup(made.id)

        assert stored is not None
        assert (stored.description, stored.tags, stored.created_at) == (
            "before the migration",
            made.tags,
            made.created_at,
        )

    def test_an_archive_that_does_not_match_its_record_is_left_in_clear_and_said(
        self, manager: BackupManager, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_store(monkeypatch, FakeApp())
        made = manager.create(DOMAIN)
        archive = archive_of(manager, made.id)
        with archive.open("ab") as handle:
            handle.write(b"tampered")
        before = archive.read_bytes()
        keys.make_key()

        report = sealer(manager, keys).run()

        (item,) = report.items
        assert item.state == "damaged" and "does not match" in item.detail
        assert archive.read_bytes() == before
        assert report.ok is False

    def test_a_run_that_stopped_before_the_sidecar_is_repaired_by_running_again(
        self, manager: BackupManager, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_store(monkeypatch, FakeApp())
        made = manager.create(DOMAIN)
        keys.make_key()
        seal_in_place(archive_of(manager, made.id), keys=keys, runner=SubprocessRunner())
        assert manager.verify(made.id)["valid"] is False, (
            "the sidecar still describes the clear file"
        )

        report = sealer(manager, keys).run()

        (item,) = report.items
        assert item.state == "repaired"
        assert manager.verify(made.id)["valid"] is True

    def test_an_archive_that_is_sealed_and_recorded_is_left_alone(
        self, manager: BackupManager, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_store(monkeypatch, FakeApp())
        keys.make_key()
        made = manager.create(DOMAIN)
        before = archive_of(manager, made.id).read_bytes()

        report = sealer(manager, keys).run()

        (item,) = report.items
        assert item.state == "already"
        assert archive_of(manager, made.id).read_bytes() == before

    def test_a_busy_application_is_skipped_and_can_be_done_later(
        self, manager: BackupManager, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import threading

        _use_store(monkeypatch, FakeApp())
        made = manager.create(DOMAIN)
        keys.make_key()
        held = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with app_lock(DOMAIN, "restore"):
                held.set()
                release.wait(10)

        thread = threading.Thread(target=hold)
        thread.start()
        try:
            assert held.wait(10)
            report = sealer(manager, keys).run()
        finally:
            release.set()
            thread.join()

        (item,) = report.items
        assert item.state == "busy" and "restore" in item.detail
        assert not is_sealed(archive_of(manager, made.id))
        assert sealer(manager, keys).run().items[0].state == "sealed"

    def test_the_plan_counts_without_changing_anything(
        self, manager: BackupManager, keys: BackupKeys, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_store(monkeypatch, FakeApp())
        made = manager.create(DOMAIN)
        keys.make_key()

        plan = sealer(manager, keys).plan()

        assert plan["applications"] == 1 and plan["total"] == 1
        assert not is_sealed(archive_of(manager, made.id))
