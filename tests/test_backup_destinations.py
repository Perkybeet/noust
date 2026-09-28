# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for remote backup destinations (rclone, 2.2).

Everything here goes through the FakeRunner: no real rclone, no real
network. What is asserted is the exact environment and argv the manager
builds - a secret must reach rclone only through ``RCLONE_CONFIG_*``
environment variables or through stdin (``rclone obscure -``), never as an
argument, which is what :mod:`wasm.core.runner` would otherwise leak to
every local user's ``ps``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from wasm.core.exceptions import BackupError, DependencyError
from wasm.core.runner import FakeRunner
from wasm.core.secrets import SecretStore
from wasm.core.store import BackupScheduleRecord, WASMStore, get_store
from wasm.managers.backup_destinations import BackupDestinationManager
from wasm.managers.backup_manager import BackupManager, BackupMetadata


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    """
    Force a fresh store singleton per test.

    ``WASMStore`` caches itself on the class; without this, a destination
    created by one test would still be there - at the previous test's
    ``tmp_path`` - when the next one asks for it.
    """
    WASMStore.reset_instance()
    yield
    WASMStore.reset_instance()


@pytest.fixture
def manager(runner: FakeRunner) -> BackupDestinationManager:
    """A destination manager wired to the fake runner."""
    runner.only_knows("rclone")
    return BackupDestinationManager(runner=runner)


def _sftp_fields(**overrides: str) -> dict[str, str]:
    fields = {"host": "nas.example.com", "user": "wasm", "pass": "s3cr3t-password-value"}
    fields.update(overrides)
    return fields


class TestAdd:
    def test_requires_rclone(self, runner: FakeRunner) -> None:
        runner.only_knows()  # nothing installed
        manager = BackupDestinationManager(runner=runner)
        with pytest.raises(DependencyError):
            manager.add("nas", "sftp", _sftp_fields())

    def test_rejects_bad_name(self, manager: BackupDestinationManager) -> None:
        with pytest.raises(BackupError):
            manager.add("Not Valid!", "sftp", _sftp_fields())

    def test_stores_non_secret_settings_and_defaults_the_path(
        self, manager: BackupDestinationManager
    ) -> None:
        destination = manager.add("nas", "sftp", _sftp_fields())
        assert destination.backend == "sftp"
        assert destination.settings["host"] == "nas.example.com"
        assert destination.settings["user"] == "wasm"
        assert destination.settings["path"] == "wasm-backups"
        assert "pass" not in destination.settings

    def test_secret_field_never_lands_in_the_settings_column(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        stored = get_store().get_backup_destination("nas")
        assert stored is not None
        assert json.dumps(stored.settings).find("s3cr3t-password-value") == -1

    def test_secret_field_is_kept_in_the_secret_store(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        secret_values = SecretStore().read_json("backup-destinations/nas")
        assert secret_values["pass"] == "s3cr3t-password-value"

    def test_missing_required_field_is_refused(self, manager: BackupDestinationManager) -> None:
        with pytest.raises(BackupError):
            manager.add("nas", "smb", {"host": "nas.example.com"})  # user, pass missing

    def test_unknown_field_is_refused(self, manager: BackupDestinationManager) -> None:
        with pytest.raises(BackupError):
            manager.add("nas", "sftp", {**_sftp_fields(), "bogus": "x"})

    def test_duplicate_name_is_refused(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        with pytest.raises(BackupError):
            manager.add("nas", "sftp", _sftp_fields())

    def test_encrypted_generates_two_passphrases(self, manager: BackupDestinationManager) -> None:
        destination = manager.add("nas", "sftp", _sftp_fields(), encrypted=True)
        assert destination.encrypted is True
        secret_values = SecretStore().read_json("backup-destinations/nas")
        assert secret_values["crypt_password"]
        assert secret_values["crypt_password2"]
        assert secret_values["crypt_password"] != secret_values["crypt_password2"]


class TestUpdate:
    def test_blank_secret_field_keeps_the_stored_value(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        manager.update("nas", {"host": "new-host.example.com"})
        secret_values = SecretStore().read_json("backup-destinations/nas")
        assert secret_values["pass"] == "s3cr3t-password-value"
        assert manager.get("nas").settings["host"] == "new-host.example.com"

    def test_given_secret_field_replaces_the_stored_value(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        manager.update("nas", {"pass": "a-brand-new-password"})
        secret_values = SecretStore().read_json("backup-destinations/nas")
        assert secret_values["pass"] == "a-brand-new-password"

    def test_unknown_destination_is_refused(self, manager: BackupDestinationManager) -> None:
        with pytest.raises(BackupError):
            manager.update("ghost", {"host": "x"})

    def test_turning_encryption_on_generates_keys_once(
        self, manager: BackupDestinationManager
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        manager.update("nas", {}, encrypted=True)
        secret_values = SecretStore().read_json("backup-destinations/nas")
        assert secret_values["crypt_password"]


class TestRemove:
    def test_removes_the_record_and_its_secrets(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        manager.remove("nas")
        assert manager.get("nas") is None
        assert SecretStore().read_json("backup-destinations/nas") == {}

    def test_refuses_when_a_schedule_references_it(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="example.com",
                schedule="daily",
                destinations=[{"name": "nas", "retention_count": None, "retention_days": None}],
            )
        )
        with pytest.raises(BackupError):
            manager.remove("nas")

    def test_force_drops_the_schedule_reference(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="example.com",
                schedule="daily",
                destinations=[{"name": "nas", "retention_count": None, "retention_days": None}],
            )
        )
        manager.remove("nas", force=True)
        assert manager.get("nas") is None
        schedule = get_store().get_backup_schedule("example.com")
        assert schedule is not None
        assert schedule.destinations == []


class TestShowKey:
    def test_refuses_when_not_encrypted(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        with pytest.raises(BackupError):
            manager.show_key("nas")

    def test_returns_both_passphrases(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", _sftp_fields(), encrypted=True)
        keys = manager.show_key("nas")
        assert keys["password"] and keys["password2"]


class TestRemoteEnv:
    def test_builds_rclone_config_variables(
        self, manager: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED_PASSWORD\n")

        env = manager.remote_env("nas")

        assert env["RCLONE_CONFIG_NAS_TYPE"] == "sftp"
        assert env["RCLONE_CONFIG_NAS_HOST"] == "nas.example.com"
        assert env["RCLONE_CONFIG_NAS_USER"] == "wasm"
        assert env["RCLONE_CONFIG_NAS_PASS"] == "OBSCURED_PASSWORD"
        # "path" is not an rclone option: it never becomes an env var.
        assert "RCLONE_CONFIG_NAS_PATH" not in env

    def test_password_reaches_obscure_only_through_stdin(
        self, manager: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")

        manager.remote_env("nas")

        assert "s3cr3t-password-value" in runner.inputs
        for call in runner.calls:
            assert "s3cr3t-password-value" not in call

    def test_dashes_in_the_name_become_underscores(
        self, manager: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        manager.add("my-nas", "b2", {"account": "acct123", "key": "topsecretkey"})
        env = manager.remote_env("my-nas")
        assert env["RCLONE_CONFIG_MY_NAS_TYPE"] == "b2"
        assert env["RCLONE_CONFIG_MY_NAS_ACCOUNT"] == "acct123"
        # b2's key is not a rclone "password" option: never obscured.
        assert env["RCLONE_CONFIG_MY_NAS_KEY"] == "topsecretkey"

    def test_encrypted_destination_wraps_the_remote_in_crypt(
        self, manager: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields(path="my-folder"), encrypted=True)
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")

        env = manager.remote_env("nas")

        assert env["RCLONE_CONFIG_NASCRYPT_TYPE"] == "crypt"
        assert env["RCLONE_CONFIG_NASCRYPT_REMOTE"] == "nas:my-folder"
        assert env["RCLONE_CONFIG_NASCRYPT_PASSWORD"] == "OBSCURED"
        assert env["RCLONE_CONFIG_NASCRYPT_PASSWORD2"] == "OBSCURED"
        assert manager.target("nas") == "nascrypt:"


class TestTarget:
    def test_plain_destination(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", _sftp_fields(path="custom/folder"))
        assert manager.target("nas") == "nas:custom/folder"

    def test_default_path(self, manager: BackupDestinationManager) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        assert manager.target("nas") == "nas:wasm-backups"


class TestTest:
    def test_reports_entries(self, manager: BackupDestinationManager, runner: FakeRunner) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
        runner.script(
            ("rclone", "lsf", "nas:wasm-backups", "--max-depth", "1"), stdout="shop-com/\n"
        )

        result = manager.test("nas")

        assert result == {"ok": True, "entries": ["shop-com/"]}

    def test_failure_raises_with_scrubbed_details(
        self, manager: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
        runner.script(
            ("rclone", "lsf", "nas:wasm-backups", "--max-depth", "1"),
            stdout="",
            stderr="ssh: auth failed for password s3cr3t-password-value",
            exit_code=1,
        )

        with pytest.raises(BackupError) as excinfo:
            manager.test("nas")

        assert "s3cr3t-password-value" not in str(excinfo.value)


class TestPushVerifyRetention:
    def _local_backup(
        self, backup_dir: Path, app_name: str, backup_id: str, content: bytes
    ) -> None:
        app_dir = backup_dir / app_name
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / f"{backup_id}.tar.gz").write_bytes(content)
        (app_dir / f"{backup_id}.json").write_text(
            json.dumps(
                {
                    "id": backup_id,
                    "domain": "shop.example.com",
                    "app_name": app_name,
                    "created_at": "2026-01-01T00:00:00",
                    "size_bytes": len(content),
                    "app_type": "static",
                    "version": "2.0.0",
                    "description": "",
                    "includes_env": True,
                    "includes_node_modules": False,
                    "checksum": None,
                }
            )
        )

    def _metadata(self, backup_id: str) -> BackupMetadata:
        return BackupMetadata(
            id=backup_id,
            domain="shop.example.com",
            app_name="shop-example-com",
            created_at="2026-01-01T00:00:00",
            size_bytes=7,
            app_type="static",
            version="2.0.0",
            description="",
            includes_env=True,
            includes_node_modules=False,
        )

    def test_upload_is_verified_by_size(
        self, manager: BackupDestinationManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")

        backup_manager = BackupManager(verbose=False, runner=runner)
        backup_manager.backup_dir = tmp_path / "backups"
        backup_id = "shop-example-com_20260101_000000"
        self._local_backup(backup_manager.backup_dir, "shop-example-com", backup_id, b"content")

        target_dir = "nas:wasm-backups/shop-example-com"
        runner.script(("rclone", "copyto"))
        # Verification lists the destination and compares sizes; script the
        # sidecar's real size so it is not mistaken for a mismatch.
        json_bytes = len(
            (backup_manager.backup_dir / "shop-example-com" / f"{backup_id}.json").read_bytes()
        )
        listing = json.dumps(
            [
                {"Name": f"{backup_id}.tar.gz", "Size": 7, "IsDir": False},
                {"Name": f"{backup_id}.json", "Size": json_bytes, "IsDir": False},
            ]
        )
        runner.script(("rclone", "lsjson", "--hash", target_dir), stdout=listing)

        summary = manager.push(self._metadata(backup_id), "nas", backup_manager=backup_manager)

        assert summary["uploaded"] == [f"{backup_id}.tar.gz", f"{backup_id}.json"]
        copyto_calls = runner.calls_to("rclone")
        copyto = [c for c in copyto_calls if c[1] == "copyto"]
        assert len(copyto) == 2
        assert copyto[0][2].endswith(f"{backup_id}.tar.gz")
        assert copyto[0][3] == f"{target_dir}/{backup_id}.tar.gz"

    def test_size_mismatch_after_upload_raises(
        self, manager: BackupDestinationManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")

        backup_manager = BackupManager(verbose=False, runner=runner)
        backup_manager.backup_dir = tmp_path / "backups"
        backup_id = "shop-example-com_20260101_000000"
        self._local_backup(backup_manager.backup_dir, "shop-example-com", backup_id, b"content")

        target_dir = "nas:wasm-backups/shop-example-com"
        runner.script(("rclone", "copyto"))
        runner.script(
            ("rclone", "lsjson", "--hash", target_dir),
            stdout=json.dumps([{"Name": f"{backup_id}.tar.gz", "Size": 999, "IsDir": False}]),
        )

        with pytest.raises(BackupError, match="Size mismatch"):
            manager.push(self._metadata(backup_id), "nas", backup_manager=backup_manager)

    def test_upload_failure_carries_scrubbed_rclone_stderr(
        self, manager: BackupDestinationManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")

        backup_manager = BackupManager(verbose=False, runner=runner)
        backup_manager.backup_dir = tmp_path / "backups"
        backup_id = "shop-example-com_20260101_000000"
        self._local_backup(backup_manager.backup_dir, "shop-example-com", backup_id, b"content")

        runner.script(
            ("rclone", "copyto"),
            stdout="",
            stderr="permission denied for password s3cr3t-password-value",
            exit_code=1,
        )

        with pytest.raises(BackupError) as excinfo:
            manager.push(self._metadata(backup_id), "nas", backup_manager=backup_manager)

        assert "s3cr3t-password-value" not in str(excinfo.value)
        assert "permission denied" in str(excinfo.value)

    def test_retention_deletes_backups_beyond_the_count(
        self, manager: BackupDestinationManager, runner: FakeRunner, tmp_path: Path
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")

        backup_manager = BackupManager(verbose=False, runner=runner)
        backup_manager.backup_dir = tmp_path / "backups"
        new_id = "shop-example-com_20260201_000000"
        old_id = "shop-example-com_20260101_000000"
        self._local_backup(backup_manager.backup_dir, "shop-example-com", new_id, b"content")

        target_dir = "nas:wasm-backups/shop-example-com"
        runner.script(("rclone", "copyto"))
        json_bytes = len(
            (backup_manager.backup_dir / "shop-example-com" / f"{new_id}.json").read_bytes()
        )
        listing = json.dumps(
            [
                {
                    "Name": f"{new_id}.tar.gz",
                    "Size": 7,
                    "IsDir": False,
                    "ModTime": "2026-02-01T00:00:00.000000000Z",
                },
                {
                    "Name": f"{new_id}.json",
                    "Size": json_bytes,
                    "IsDir": False,
                    "ModTime": "2026-02-01T00:00:00.000000000Z",
                },
                {
                    "Name": f"{old_id}.tar.gz",
                    "Size": 7,
                    "IsDir": False,
                    "ModTime": "2026-01-01T00:00:00.000000000Z",
                },
                {
                    "Name": f"{old_id}.json",
                    "Size": 5,
                    "IsDir": False,
                    "ModTime": "2026-01-01T00:00:00.000000000Z",
                },
            ]
        )
        runner.script(("rclone", "lsjson", "--hash", target_dir), stdout=listing)
        runner.script(("rclone", "deletefile"))

        summary = manager.push(
            self._metadata(new_id), "nas", retention_count=1, backup_manager=backup_manager
        )

        assert summary["retention_deleted"] == [old_id]
        delete_calls = [c for c in runner.calls_to("rclone") if c[1] == "deletefile"]
        assert delete_calls[0][2] == f"{target_dir}/{old_id}.tar.gz"
        assert delete_calls[1][2] == f"{target_dir}/{old_id}.json"


class TestRemoteListAndDownload:
    def test_remote_list_without_app_lists_directories(
        self, manager: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
        runner.script(
            ("rclone", "lsjson", "--hash", "nas:wasm-backups"),
            stdout=json.dumps([{"Name": "shop-example-com", "IsDir": True}]),
        )

        result = manager.remote_list("nas")

        assert result["apps"] == ["shop-example-com"]
        assert result["backups"] == []

    def test_remote_list_with_app_lists_backups_newest_first(
        self, manager: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        manager.add("nas", "sftp", _sftp_fields())
        runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
        target_dir = "nas:wasm-backups/shop-example-com"
        runner.script(
            ("rclone", "lsjson", "--hash", target_dir),
            stdout=json.dumps(
                [
                    {
                        "Name": "shop-example-com_20260101_000000.tar.gz",
                        "Size": 7,
                        "ModTime": "2026-01-01T00:00:00Z",
                    },
                    {
                        "Name": "shop-example-com_20260101_000000.json",
                        "Size": 5,
                        "ModTime": "2026-01-01T00:00:00Z",
                    },
                    {
                        "Name": "shop-example-com_20260202_000000.tar.gz",
                        "Size": 9,
                        "ModTime": "2026-02-02T00:00:00Z",
                    },
                ]
            ),
        )

        result = manager.remote_list("nas", "shop-example-com")

        ids = [entry["backup_id"] for entry in result["backups"]]
        assert ids == [
            "shop-example-com_20260202_000000",
            "shop-example-com_20260101_000000",
        ]
        assert result["backups"][1]["has_metadata"] is True
        assert result["backups"][0]["has_metadata"] is False

    def test_download_verifies_checksum(self, runner: FakeRunner, tmp_path: Path) -> None:
        import hashlib

        backup_id = "shop-example-com_20260101_000000"
        staging = tmp_path / "staging"
        archive_content = b"archive-bytes"
        checksum = hashlib.sha256(archive_content).hexdigest()
        sidecar = json.dumps(
            {
                "id": backup_id,
                "domain": "shop.example.com",
                "app_name": "shop-example-com",
                "created_at": "2026-01-01T00:00:00",
                "size_bytes": len(archive_content),
                "app_type": "static",
                "version": "2.0.0",
                "description": "",
                "includes_env": True,
                "includes_node_modules": False,
                "checksum": checksum,
            }
        )

        class _WritingRunner(FakeRunner):
            """Fake runner whose copyto writes the bytes a real download would."""

            def run(self, argv, **kwargs):  # type: ignore[override]
                result = super().run(argv, **kwargs)
                if len(argv) >= 2 and argv[0] == "rclone" and argv[1] == "copyto":
                    destination = Path(argv[3])
                    if destination.name.endswith(".tar.gz"):
                        destination.write_bytes(archive_content)
                    else:
                        destination.write_text(sidecar)
                return result

        writing_runner = _WritingRunner()
        writing_runner.only_knows("rclone")
        writing_runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
        writing_manager = BackupDestinationManager(runner=writing_runner)
        writing_manager.add("nas", "sftp", _sftp_fields())

        archive_path, metadata_path = writing_manager.download(
            "nas", backup_id, "shop-example-com", staging
        )

        assert archive_path.read_bytes() == archive_content
        assert json.loads(metadata_path.read_text())["checksum"] == checksum

    def test_download_checksum_mismatch_raises(self, tmp_path: Path) -> None:
        backup_id = "shop-example-com_20260101_000000"
        staging = tmp_path / "staging"

        class _WritingRunner(FakeRunner):
            def run(self, argv, **kwargs):  # type: ignore[override]
                result = super().run(argv, **kwargs)
                if len(argv) >= 2 and argv[0] == "rclone" and argv[1] == "copyto":
                    destination = Path(argv[3])
                    if destination.name.endswith(".tar.gz"):
                        destination.write_bytes(b"bytes-on-the-wire")
                    else:
                        destination.write_text(
                            json.dumps(
                                {
                                    "id": backup_id,
                                    "domain": "shop.example.com",
                                    "app_name": "shop-example-com",
                                    "created_at": "2026-01-01T00:00:00",
                                    "size_bytes": 5,
                                    "app_type": "static",
                                    "version": "2.0.0",
                                    "description": "",
                                    "includes_env": True,
                                    "includes_node_modules": False,
                                    "checksum": "does-not-match",
                                }
                            )
                        )
                return result

        writing_runner = _WritingRunner()
        writing_runner.only_knows("rclone")
        writing_runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
        writing_manager = BackupDestinationManager(runner=writing_runner)
        writing_manager.add("nas", "sftp", _sftp_fields())

        with pytest.raises(BackupError, match="Checksum mismatch"):
            writing_manager.download("nas", backup_id, "shop-example-com", staging)
