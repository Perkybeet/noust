# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Backups for the ENS (G12: mp.si.2, mp.info.6).

- The metadata sidecar carries an HMAC-SHA256 under a key only this server
  holds, and it covers the archive's checksum: a sidecar and archive replaced
  together on the backup storage are caught by ``verify``.
- Every verification, from any caller, is an audit event (``backups.verify``).
- Under the ``ens-medium`` profile (or ``backup.encryption: required``) no
  backup leaves the server for an unencrypted destination, and the refusal
  comes before rclone runs.
- A schedule verifies the backup it took when the last good verification is
  older than the profile allows, records it, and sends nothing when it fails.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import noust.managers.backup_manager as backup_manager_module
import noust.managers.backup_scheduler as backup_scheduler_module
from noust.core.exceptions import BackupError
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import BackupDestinationRecord, BackupScheduleRecord
from noust.managers.backup_destinations import BackupDestinationManager
from noust.managers.backup_manager import BackupManager, BackupMetadata

DOMAIN = "shop.example.com"


@pytest.fixture
def runner() -> Iterator[FakeRunner]:
    fake = FakeRunner()
    set_runner(fake)
    yield fake
    set_runner(None)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []

    def fake(event: str, **kwargs: Any) -> None:
        recorded.append((event, kwargs))

    monkeypatch.setattr(backup_manager_module, "audit_record", fake)
    return recorded


@pytest.fixture
def manager(runner: FakeRunner, tmp_path: Path, monkeypatch) -> Iterator[BackupManager]:
    apps = tmp_path / "apps"
    app = apps / "shop-example-com"
    (app / "src").mkdir(parents=True)
    (app / "src" / "index.js").write_text("console.log('hello')\n")
    backup_manager = BackupManager(verbose=False, runner=runner)
    backup_manager.backup_dir = tmp_path / "backups"
    previous = backup_manager.config.get("apps_directory")
    backup_manager.config.set("apps_directory", str(apps))
    monkeypatch.setattr(backup_manager_module, "require_server_role", lambda what: None)
    yield backup_manager
    backup_manager.config.set("apps_directory", previous)


def sidecar_of(manager: BackupManager, metadata: BackupMetadata) -> Path:
    return manager.local_files(metadata)[1]


class TestTheSidecarMac:
    def test_a_new_backup_is_signed_and_verifies(self, manager, events) -> None:
        metadata = manager.create(DOMAIN)

        stored = json.loads(sidecar_of(manager, metadata).read_text())
        assert len(stored["mac"]) == 64 and stored["mac_key_id"]

        result = manager.verify(metadata.id, deep=False)

        assert result["valid"], result
        assert result["mac"] == "valid"

    def test_verification_is_signed_again_and_still_verifies(self, manager, events) -> None:
        metadata = manager.create(DOMAIN)
        manager.verify(metadata.id, deep=False)

        stored = json.loads(sidecar_of(manager, metadata).read_text())
        assert stored["verified_ok"] is True
        assert manager.verify(metadata.id, deep=False)["mac"] == "valid"

    def test_an_edited_sidecar_is_caught(self, manager, events) -> None:
        metadata = manager.create(DOMAIN)
        path = sidecar_of(manager, metadata)
        stored = json.loads(path.read_text())
        stored["description"] = "nothing to see"
        path.write_text(json.dumps(stored))

        result = manager.verify(metadata.id, deep=False)

        assert not result["valid"]
        assert result["mac"] == "invalid"
        assert any("MAC" in error for error in result["errors"])

    def test_archive_and_checksum_replaced_together_are_caught(self, manager, events) -> None:
        metadata = manager.create(DOMAIN)
        archive, path = manager.local_files(metadata)
        archive.write_bytes(archive.read_bytes())  # same bytes: checksum still matches
        stored = json.loads(path.read_text())
        stored["checksum"] = "0" * 64
        path.write_text(json.dumps(stored))

        result = manager.verify(metadata.id, deep=False)

        assert result["mac"] == "invalid" and not result["valid"]

    def test_a_backup_from_before_the_mac_is_a_warning(self, manager, events) -> None:
        metadata = manager.create(DOMAIN)
        path = sidecar_of(manager, metadata)
        stored = json.loads(path.read_text())
        stored.pop("mac")
        stored.pop("mac_key_id")
        path.write_text(json.dumps(stored))

        result = manager.verify(metadata.id, deep=False)

        assert result["valid"] and result["mac"] == "unsigned"
        assert any("MAC" in warning for warning in result["warnings"])

    def test_a_backup_signed_by_another_server_is_a_warning(self, manager, events) -> None:
        metadata = manager.create(DOMAIN)
        path = sidecar_of(manager, metadata)
        stored = json.loads(path.read_text())
        stored["mac_key_id"] = "another-server"
        path.write_text(json.dumps(stored))

        result = manager.verify(metadata.id, deep=False)

        assert result["valid"] and result["mac"] == "other_key"


class TestVerificationIsOnRecord:
    def test_every_verification_is_an_audit_event(self, manager, events) -> None:
        metadata = manager.create(DOMAIN)

        manager.verify(metadata.id, deep=False)

        name, kwargs = events[-1]
        assert name == "backups.verify"
        assert kwargs["target"] == f"backup:{metadata.id}"
        assert kwargs["outcome"] == "ok"
        assert kwargs["details"]["mac"] == "valid"

    def test_a_failed_verification_is_recorded_as_a_failure(self, manager, events) -> None:
        metadata = manager.create(DOMAIN)
        manager.local_files(metadata)[0].write_bytes(b"not an archive")

        manager.verify(metadata.id, deep=False)

        assert events[-1][1]["outcome"] == "failure"


class FakeDestinations:
    def __init__(self, records: dict[str, BackupDestinationRecord]) -> None:
        self.records = records

    def get_backup_destination(self, name: str) -> BackupDestinationRecord | None:
        return self.records.get(name)


class TestNothingLeavesUnencrypted:
    @pytest.fixture
    def ens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.core.ens import profile

        monkeypatch.setattr(profile, "backup_encryption_required", lambda config=None: True)

    def destinations(self, runner: FakeRunner, encrypted: bool) -> BackupDestinationManager:
        record = BackupDestinationRecord(
            name="nas", backend="sftp", settings={"path": "x"}, encrypted=encrypted
        )
        return BackupDestinationManager(runner=runner, store=FakeDestinations({"nas": record}))  # type: ignore[arg-type]

    def test_an_unencrypted_destination_is_refused_before_rclone(
        self, runner, manager, events, ens
    ) -> None:
        metadata = manager.create(DOMAIN)
        destinations = self.destinations(runner, encrypted=False)
        runner.calls.clear()

        with pytest.raises(BackupError, match="not encrypted"):
            destinations.push(metadata, "nas", backup_manager=manager)

        assert not any(call[0] == "rclone" and "copyto" in call for call in runner.calls)

    def test_a_database_dump_is_refused_the_same_way(
        self, runner, tmp_path, ens, monkeypatch
    ) -> None:
        from noust.managers.backup_destination_files import DestinationFileManager

        record = BackupDestinationRecord(
            name="nas", backend="sftp", settings={"path": "x"}, encrypted=False
        )
        files = DestinationFileManager(runner=runner, store=FakeDestinations({"nas": record}))  # type: ignore[arg-type]
        dump = tmp_path / "shop.sql.gz"
        dump.write_bytes(b"dump")

        with pytest.raises(BackupError, match="not encrypted"):
            files.push_file(dump, "nas", "databases/postgresql/shop", sidecar={"sha256": "x"})

        assert not any("copyto" in call for call in runner.calls)

    def test_without_the_requirement_an_unencrypted_upload_goes(self, monkeypatch) -> None:
        from noust.core.ens import profile

        monkeypatch.setattr(profile, "backup_encryption_required", lambda config=None: False)
        record = BackupDestinationRecord(name="nas", backend="sftp", settings={}, encrypted=False)
        manager = BackupDestinationManager(
            runner=FakeRunner(),
            store=FakeDestinations({"nas": record}),  # type: ignore[arg-type]
        )

        manager.require_encrypted_upload("nas")

    def test_the_profile_requires_it(self) -> None:
        from noust.core.ens import profile

        class Config:
            def __init__(self, values: dict[str, Any]) -> None:
                self.values = values

            def get(self, key: str, default: Any = None) -> Any:
                return self.values.get(key, default)

        assert profile.backup_encryption_required(Config({"security.profile": "ens-medium"}))
        assert profile.backup_encryption_required(Config({"backup.encryption": "required"}))
        assert not profile.backup_encryption_required(Config({}))


class TestScheduledVerification:
    @pytest.fixture
    def schedule(self, monkeypatch: pytest.MonkeyPatch, manager: BackupManager) -> dict[str, Any]:
        state: dict[str, Any] = {"notified": []}
        record = BackupScheduleRecord(
            app_domain=DOMAIN,
            schedule="daily",
            include_databases=False,
            retention_count=None,
            retention_days=None,
            destinations=[{"name": "nas"}],
        )

        class Store:
            def get_backup_schedule(self, domain: str) -> BackupScheduleRecord:
                return record

        monkeypatch.setattr(backup_scheduler_module, "get_store", lambda: Store())
        monkeypatch.setattr(backup_scheduler_module, "BackupManager", lambda **kwargs: manager)
        monkeypatch.setattr(
            backup_scheduler_module,
            "_notify_backup",
            lambda config, notification: state["notified"].append(notification),
        )

        class Destinations:
            def __init__(self, runner: Any = None) -> None:
                pass

            def push(self, metadata: BackupMetadata, name: str, **kwargs: Any) -> dict[str, Any]:
                state.setdefault("pushed", []).append(metadata.id)
                return {"uploaded": [metadata.id]}

        monkeypatch.setattr(backup_scheduler_module, "BackupDestinationManager", Destinations)
        return state

    def test_a_schedule_verifies_when_the_last_verification_is_old(
        self, manager, schedule, events
    ) -> None:
        result = backup_scheduler_module.run_schedule(DOMAIN)

        assert result["verification"]["valid"] is True
        assert any(name == "backups.verify" for name, _ in events)
        assert schedule["pushed"] == [result["backup_id"]]

    def test_a_recent_good_verification_is_not_repeated(self, manager, schedule, events) -> None:
        earlier = manager.create(DOMAIN)
        manager.verify(earlier.id, deep=False)
        events.clear()

        result = backup_scheduler_module.run_schedule(DOMAIN)

        assert result["verification"] is None
        assert not any(name == "backups.verify" for name, _ in events)

    def test_a_failed_verification_sends_nothing(self, manager, schedule, monkeypatch) -> None:
        monkeypatch.setattr(
            BackupManager,
            "verify",
            lambda self, backup_id, deep=True: {"valid": False, "errors": ["Archive is corrupted"]},
        )

        with pytest.raises(BackupError, match="failed its verification"):
            backup_scheduler_module.run_schedule(DOMAIN)

        assert "pushed" not in schedule
        assert schedule["notified"]

    def test_verification_is_due_after_the_profiles_days(self) -> None:
        now = datetime(2026, 9, 30, 12, 0, 0)
        fresh = BackupMetadata(
            id="a",
            domain=DOMAIN,
            app_name="shop-example-com",
            created_at=now.isoformat(),
            size_bytes=1,
            app_type="nodejs",
            version="1",
            description="",
            includes_env=False,
            includes_node_modules=False,
            last_verified_at=(now - timedelta(days=2)).isoformat(),
            verified_ok=True,
        )
        stale = BackupMetadata(**{**fresh.to_dict(), "id": "b"})
        stale.last_verified_at = (now - timedelta(days=9)).isoformat()

        assert not backup_scheduler_module.verification_due([fresh, stale], days=7, now=now)
        assert backup_scheduler_module.verification_due([stale], days=7, now=now)
        assert backup_scheduler_module.verification_due([], days=7, now=now)
