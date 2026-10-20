# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The folder backups go to by default is ``noust-backups`` from 4.0.

A destination made before that stored the old default. It moves to the new one, and the old one
is read beside it (an rclone union whose first upstream is the only one written to), so what is
already there stays listed, restorable and under its retention until it has expired. If the new
folder cannot be created at the destination, backups keep going to the old one and the operator
is told, rather than a backup failing the night after an update.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from noust.core import legacy_names
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore, get_store
from noust.managers.backup_destinations import DEFAULT_REMOTE_PATH, BackupDestinationManager
from noust.managers.backup_manager import BackupManager, BackupMetadata

OLD = legacy_names.BACKUP_FOLDER
NEW = legacy_names.CURRENT_BACKUP_FOLDER


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def manager(runner: FakeRunner) -> BackupDestinationManager:
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
    return BackupDestinationManager(runner=runner)


def fields(**overrides: str) -> dict[str, str]:
    values = {"host": "nas.example.com", "user": "backup", "pass": "a-password-of-some-length"}
    values.update(overrides)
    return values


def in_transition(
    manager: BackupDestinationManager, name: str = "nas", *, encrypted: bool = False
) -> None:
    """Leave a destination the way the 4.0 migration leaves one made before: moved, old folder kept."""
    manager.add(name, "sftp", fields(), encrypted=encrypted)
    record = get_store().get_backup_destination(name)
    assert record is not None
    record.settings["path"] = NEW
    record.settings["legacy_path"] = OLD
    get_store().save_backup_destination(record)


def test_a_new_destination_defaults_to_the_new_folder(manager: BackupDestinationManager) -> None:
    destination = manager.add("nas", "sftp", fields())

    assert DEFAULT_REMOTE_PATH == NEW
    assert destination.settings["path"] == NEW
    assert "legacy_path" not in destination.settings
    assert manager.target("nas") == f"nas:{NEW}"


class TestReading:
    def test_the_old_folder_is_read_beside_the_new_one_and_only_the_new_is_written(
        self, manager: BackupDestinationManager
    ) -> None:
        in_transition(manager)

        env = manager.remote_env("nas")

        assert manager.target("nas") == "nasunion:"
        assert env["RCLONE_CONFIG_NASUNION_TYPE"] == "union"
        assert env["RCLONE_CONFIG_NASUNION_UPSTREAMS"] == f"nas:{NEW} nas:{OLD}"
        assert env["RCLONE_CONFIG_NASUNION_CREATE_POLICY"] == "ff"
        assert env["RCLONE_CONFIG_NASUNION_SEARCH_POLICY"] == "ff"
        assert env["RCLONE_CONFIG_NASUNION_ACTION_POLICY"] == "epall"

    def test_a_folder_with_a_space_in_its_name_is_quoted_for_the_union(
        self, manager: BackupDestinationManager
    ) -> None:
        in_transition(manager)
        record = get_store().get_backup_destination("nas")
        assert record is not None
        record.settings["path"] = "my backups"
        get_store().save_backup_destination(record)

        env = manager.remote_env("nas")

        assert env["RCLONE_CONFIG_NASUNION_UPSTREAMS"] == f'"nas:my backups" nas:{OLD}'

    def test_an_encrypted_destination_reads_the_old_folder_through_a_second_crypt_remote(
        self, manager: BackupDestinationManager
    ) -> None:
        in_transition(manager, "vault", encrypted=True)

        env = manager.remote_env("vault")

        assert env["RCLONE_CONFIG_VAULTCRYPT_REMOTE"] == f"vault:{NEW}"
        assert env["RCLONE_CONFIG_VAULTLEGACYCRYPT_TYPE"] == "crypt"
        assert env["RCLONE_CONFIG_VAULTLEGACYCRYPT_REMOTE"] == f"vault:{OLD}"
        assert (
            env["RCLONE_CONFIG_VAULTLEGACYCRYPT_PASSWORD"]
            == env["RCLONE_CONFIG_VAULTCRYPT_PASSWORD"]
        )
        assert env["RCLONE_CONFIG_VAULTUNION_UPSTREAMS"] == "vaultcrypt: vaultlegacycrypt:"
        assert manager.target("vault") == "vaultunion:"

    def test_a_destination_that_never_had_the_old_folder_is_as_it_was(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", fields(path="custom"))

        env = manager.remote_env("nas")

        assert not any("UNION" in key for key in env)
        assert manager.target("nas") == "nas:custom"


class TestFirstWrite:
    def local_backup(self, backup_dir: Path, backup_id: str) -> BackupMetadata:
        app_dir = backup_dir / "shop-example-com"
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / f"{backup_id}.tar.gz").write_bytes(b"content")
        (app_dir / f"{backup_id}.json").write_text(json.dumps({"id": backup_id}))
        return BackupMetadata(
            id=backup_id,
            domain="shop.example.com",
            app_name="shop-example-com",
            created_at="2026-01-01T00:00:00",
            size_bytes=7,
            app_type="static",
            version="4.0.0",
            description="",
            includes_env=True,
            includes_node_modules=False,
        )

    def push(
        self,
        manager: BackupDestinationManager,
        runner: FakeRunner,
        tmp_path: Path,
        *,
        old_folder: str = '[{"Name": "shop-example-com/old.tar.gz", "Size": 5, "IsDir": false}]',
    ) -> dict[str, object]:
        backups = BackupManager(verbose=False, runner=runner)
        backups.backup_dir = tmp_path / "backups"
        metadata = self.local_backup(backups.backup_dir, "shop-example-com_20260101_000000")
        listing = json.dumps(
            [
                {"Name": f"{metadata.id}.tar.gz", "Size": 7, "IsDir": False},
                {
                    "Name": f"{metadata.id}.json",
                    "Size": len(json.dumps({"id": metadata.id})),
                    "IsDir": False,
                },
            ]
        )
        runner.script(("rclone", "copyto"))
        runner.script(("rclone", "lsjson", "--hash"), stdout=listing)
        runner.script(("rclone", "lsjson", "-R"), stdout=old_folder)
        return manager.push(metadata, "nas", backup_manager=backups)

    def test_the_new_folder_is_made_before_the_first_backup_goes_there(
        self, manager: BackupDestinationManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        in_transition(manager)
        runner.script(("rclone", "mkdir"))

        self.push(manager, runner, tmp_path)

        made = [call for call in runner.calls if call[:2] == ("rclone", "mkdir")]
        assert made == [("rclone", "mkdir", f"nas:{NEW}")]
        copied = [call for call in runner.calls if call[:2] == ("rclone", "copyto")]
        assert all(call[3].startswith("nasunion:/shop-example-com/") for call in copied)
        record = get_store().get_backup_destination("nas")
        assert record is not None and record.settings["path"] == NEW
        assert record.settings.get("path_checked") == "1"

    def test_it_is_checked_only_once(
        self, manager: BackupDestinationManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        in_transition(manager)
        runner.script(("rclone", "mkdir"))
        self.push(manager, runner, tmp_path)
        runner.calls.clear()

        self.push(manager, runner, tmp_path)

        assert not [call for call in runner.calls if call[:2] == ("rclone", "mkdir")]

    def test_a_destination_that_cannot_make_the_new_folder_keeps_the_old_one(
        self,
        manager: BackupDestinationManager,
        runner: FakeRunner,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A bucket another account owns, or a key that may not create one: no backup is lost."""
        in_transition(manager)
        runner.script(
            ("rclone", "mkdir"),
            stderr="BucketAlreadyExists: the requested bucket name is not available",
            exit_code=1,
        )

        summary = self.push(manager, runner, tmp_path)

        record = get_store().get_backup_destination("nas")
        assert record is not None
        assert record.settings["path"] == OLD
        assert "legacy_path" not in record.settings
        assert manager.target("nas") == f"nas:{OLD}"
        assert summary["backup_id"] == "shop-example-com_20260101_000000"

    def test_the_old_folder_stops_being_read_once_nothing_is_left_in_it(
        self, manager: BackupDestinationManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        """Retention removes the backups that were there; then there is one folder again."""
        in_transition(manager)
        runner.script(("rclone", "mkdir"))

        self.push(manager, runner, tmp_path, old_folder="[]")

        record = get_store().get_backup_destination("nas")
        assert record is not None
        assert record.settings["path"] == NEW
        assert "legacy_path" not in record.settings
        assert manager.target("nas") == f"nas:{NEW}"

    def test_a_folder_that_cannot_be_listed_is_not_taken_for_empty(
        self, manager: BackupDestinationManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        in_transition(manager)
        runner.script(("rclone", "mkdir"))
        runner.script(("rclone", "lsjson", "-R"), stderr="connection refused", exit_code=1)
        backups = BackupManager(verbose=False, runner=runner)
        backups.backup_dir = tmp_path / "backups"
        self.local_backup(backups.backup_dir, "shop-example-com_20260101_000000")

        assert manager.forget_exhausted_legacy("nas") is False


class TestOperatorChoice:
    def test_naming_a_folder_ends_the_transition(self, manager: BackupDestinationManager) -> None:
        in_transition(manager)

        manager.update("nas", {"path": "elsewhere"})

        assert manager.target("nas") == "nas:elsewhere"
        assert not any("UNION" in key for key in manager.remote_env("nas"))
