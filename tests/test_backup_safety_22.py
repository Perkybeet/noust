# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the pre-release review of 2.2 backups: what must never lose a backup.

Each class is one finding. The common thread is that a backup an operator
meant to keep must survive every automatic deletion Noust performs: a
schedule's retention, ``backup.max_per_app`` rotation, a rollback's safety
backup, remote retention on a folder another server shares, and a failed
upload counting toward retention. The other half is recovery: an encrypted
destination's key must be enterable on a replacement server, and removing the
destination must not silently throw the only copy of that key away.
"""

from __future__ import annotations

import json
import sqlite3
from collections import namedtuple
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.applock import AppBusyError
from noust.core.exceptions import BackupError, ConfigError
from noust.core.notifier import NotificationEvent
from noust.core.runner import FakeRunner
from noust.core.secrets import SecretStore
from noust.core.store import BackupScheduleRecord, DeploymentStatus, NoustStore, get_store
from noust.managers import backup_manager as backup_manager_module
from noust.managers import backup_scheduler as scheduler_module
from noust.managers.backup_destinations import (
    STAGING_DIR_NAME,
    BackupDestinationManager,
    parse_crypt_key,
    validate_destination_name,
)
from noust.managers.backup_manager import (
    SCHEDULED_TAG,
    BackupManager,
    BackupMetadata,
    RollbackManager,
    server_id,
)
from noust.managers.backup_scheduler import BackupScheduler, run_schedule

APP = "shop-example-com"
DOMAIN = "shop.example.com"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def manager(runner: FakeRunner, tmp_path: Path) -> BackupManager:
    backup_manager = BackupManager(verbose=False, runner=runner)
    backup_manager.backup_dir = tmp_path / "backups"
    backup_manager.max_backups = 2
    return backup_manager


@pytest.fixture
def destinations(runner: FakeRunner) -> BackupDestinationManager:
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
    return BackupDestinationManager(runner=runner)


def _sftp_fields() -> dict[str, str]:
    return {"host": "nas.example.com", "user": "wasm", "pass": "s3cr3t-password-value"}


def _write_backup(
    manager: BackupManager, day: int, tags: list[str], *, origin: str | None = None
) -> BackupMetadata:
    backup_id = f"{APP}_202601{day:02d}_000000"
    metadata = BackupMetadata(
        id=backup_id,
        domain=DOMAIN,
        app_name=APP,
        created_at=f"2026-01-{day:02d}T00:00:00",
        size_bytes=7,
        app_type="static",
        version="2.0.0",
        description="",
        includes_env=True,
        includes_node_modules=False,
        tags=tags,
        origin=origin,
    )
    app_dir = manager.backup_dir / APP
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / f"{backup_id}.tar.gz").write_bytes(b"content")
    (app_dir / f"{backup_id}.json").write_text(json.dumps(metadata.to_dict()))
    return metadata


def _remaining(manager: BackupManager) -> set[str]:
    return {backup.id for backup in manager.list_backups(app_name=APP)}


# ---------------------------------------------------------------------------
# 2. Retention never deletes a backup the schedule did not make
# ---------------------------------------------------------------------------


class TestScheduledRetentionScope:
    def test_count_only_prunes_scheduled_backups(self, manager: BackupManager) -> None:
        manual = _write_backup(manager, 1, [])
        pre_deploy = _write_backup(manager, 2, ["pre-deploy", "auto"])
        scheduled = [_write_backup(manager, day, [SCHEDULED_TAG, "auto"]) for day in (3, 4, 5)]

        deleted = manager.rotate_by_policy(APP, max_count=1, tag=SCHEDULED_TAG)

        assert deleted == 2
        assert _remaining(manager) == {manual.id, pre_deploy.id, scheduled[-1].id}

    def test_age_limit_only_prunes_scheduled_backups(self, manager: BackupManager) -> None:
        manual = _write_backup(manager, 1, [])
        old_scheduled = _write_backup(manager, 2, [SCHEDULED_TAG, "auto"])

        manager.rotate_by_policy(APP, max_count=10, max_age_days=1, tag=SCHEDULED_TAG)

        assert manual.id in _remaining(manager)
        assert old_scheduled.id not in _remaining(manager)

    def test_run_schedule_scopes_retention_to_its_own_backups(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        get_store().save_backup_schedule(
            BackupScheduleRecord(app_domain=DOMAIN, schedule="daily", retention_count=3)
        )
        captured: dict[str, Any] = {}

        def fake_create(self: BackupManager, **kwargs: Any) -> BackupMetadata:
            captured.update(kwargs)
            return BackupMetadata.from_dict(
                {
                    "id": f"{APP}_20260101_000000",
                    "domain": DOMAIN,
                    "app_name": APP,
                    "created_at": "2026-01-01T00:00:00",
                }
            )

        monkeypatch.setattr(BackupManager, "create", fake_create)

        run_schedule(DOMAIN)

        assert captured["retention_tag"] == SCHEDULED_TAG
        assert captured["tags"] == [SCHEDULED_TAG, "auto"]

    def test_null_retention_rotates_like_2_1(
        self, manager: BackupManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        called: list[str] = []
        monkeypatch.setattr(
            BackupManager, "_rotate_backups", lambda self, app, protect=(): called.append(app)
        )
        monkeypatch.setattr(
            BackupManager, "rotate_by_policy", lambda *a, **k: pytest.fail("policy applied")
        )
        apps = tmp_path / "apps"
        (apps / APP).mkdir(parents=True)
        (apps / APP / "index.html").write_text("hi")
        previous = manager.config.get("apps_directory")
        manager.config.set("apps_directory", str(apps))
        try:
            metadata = manager.create(DOMAIN, retention_count=None, retention_days=None)
        finally:
            manager.config.set("apps_directory", previous)

        assert called == [APP]
        assert metadata.origin == server_id()


# ---------------------------------------------------------------------------
# 2/8. Rotation never deletes a deployment's snapshot or a rollback target
# ---------------------------------------------------------------------------


class TestRotationProtections:
    def test_a_deployment_snapshot_is_never_rotated(self, manager: BackupManager) -> None:
        snapshot = _write_backup(manager, 1, ["pre-deploy", "auto"])
        newer = [_write_backup(manager, day, [SCHEDULED_TAG, "auto"]) for day in (2, 3, 4)]
        store = get_store()
        deployment = store.record_deployment_start(DOMAIN, "cli", git_commit="abc")
        store.finish_deployment(deployment, DeploymentStatus.SUCCESS.value)
        store.set_deployment_snapshot(deployment, snapshot.id)

        manager._rotate_backups(APP)

        remaining = _remaining(manager)
        assert snapshot.id in remaining
        # The snapshot does not take one of the max_per_app places.
        assert remaining == {snapshot.id, newer[1].id, newer[2].id}

    def test_a_protected_id_is_never_rotated(self, manager: BackupManager) -> None:
        oldest = _write_backup(manager, 1, ["pre-deploy", "auto"])
        for day in (2, 3, 4):
            _write_backup(manager, day, ["pre-deploy", "auto"])

        manager._rotate_backups(APP, protect=[oldest.id])
        manager.rotate_by_policy(APP, max_count=1, max_age_days=1, protect=[oldest.id])

        assert oldest.id in _remaining(manager)

    def test_nothing_is_rotated_when_the_store_cannot_say(
        self, manager: BackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for day in (1, 2, 3, 4):
            _write_backup(manager, day, [SCHEDULED_TAG])

        def broken(self: BackupManager, backups: Any) -> set[str]:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(BackupManager, "_protected_backup_ids", broken)

        manager._rotate_backups(APP)
        assert manager.rotate_by_policy(APP, max_count=1) == 0
        assert len(_remaining(manager)) == 4

    def test_the_rollback_target_is_protected_from_the_safety_backup(
        self, manager: BackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Every backup automatic: the target is the oldest, the one rotation
        # after the safety backup would delete.
        oldest = _write_backup(manager, 1, ["pre-deploy", "auto"])
        _write_backup(manager, 2, ["pre-deploy", "auto"])

        rollback = RollbackManager(verbose=False, runner=manager.runner)
        rollback.backup_manager = manager
        seen: dict[str, Any] = {}

        def safety(domain: str, description: str = "", *, protect: Any = ()) -> None:
            seen["protect"] = list(protect)

        restored: list[str] = []
        monkeypatch.setattr(rollback, "create_pre_deploy_backup", safety)
        monkeypatch.setattr(
            manager, "restore", lambda backup_id, **kwargs: restored.append(backup_id)
        )
        monkeypatch.setattr(rollback.service_manager, "get_status", lambda name: {})

        rollback.rollback(DOMAIN, rebuild=False)

        assert restored == [oldest.id]
        assert seen["protect"] == [oldest.id]

    def test_pre_deploy_backup_forwards_the_protection(
        self, manager: BackupManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        rollback = RollbackManager(verbose=False, runner=manager.runner)
        rollback.backup_manager = manager
        apps = tmp_path / "apps"
        (apps / APP).mkdir(parents=True)
        monkeypatch.setattr(type(rollback.config), "apps_directory", property(lambda _: apps))
        seen: dict[str, Any] = {}

        def fake_create(**kwargs: Any) -> None:
            seen.update(kwargs)

        monkeypatch.setattr(manager, "create", fake_create)

        rollback.create_pre_deploy_backup(DOMAIN, protect=["target-id"])

        assert seen["protect"] == ["target-id"]


# ---------------------------------------------------------------------------
# 3/4. Adoption and run-schedule without a store row
# ---------------------------------------------------------------------------

LIST_LINE = "n/a n/a n/a n/a noust-backup-example-com.timer noust-backup-example-com.service\n"
SHOW_OK = (
    "Description=Noust backup timer for example.com\n"
    "TimersCalendar={ OnCalendar=*-*-* 02:00:00 ; next_elapse=n/a }\n"
)
LEGACY_SERVICE = (
    "[Service]\nExecStart=/usr/bin/noust backup create example.com "
    "--include-databases --tags scheduled,auto\n"
)


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


class TestAdoption:
    def test_a_timer_whose_domain_cannot_be_read_is_left_alone(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        runner.script(("systemctl", "list-timers"), stdout=LIST_LINE)
        runner.script(("systemctl", "show"), stdout="", exit_code=1)
        service = systemd_dir / "noust-backup-example-com.service"
        service.write_text(LEGACY_SERVICE)

        listed = BackupScheduler(verbose=False, runner=runner).list_schedules()

        assert len(listed) == 1
        # Not filed under the guessed "example-com", and the unit still runs
        # its 2.1 command.
        assert get_store().get_backup_schedule("example-com") is None
        assert get_store().list_backup_schedules() == []
        assert service.read_text() == LEGACY_SERVICE

    def test_a_failed_rewrite_is_retried_on_the_next_listing(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        runner.script(("systemctl", "list-timers"), stdout=LIST_LINE)
        runner.script(("systemctl", "show"), stdout=SHOW_OK)
        get_store().save_backup_schedule(
            BackupScheduleRecord(app_domain="example.com", schedule="*-*-* 02:00:00")
        )
        service = systemd_dir / "noust-backup-example-com.service"
        service.write_text(LEGACY_SERVICE)

        BackupScheduler(verbose=False, runner=runner).list_schedules()

        assert "backup run-schedule example.com" in service.read_text()

    def test_a_rewritten_unit_is_not_rewritten_again(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        runner.script(("systemctl", "list-timers"), stdout=LIST_LINE)
        runner.script(("systemctl", "show"), stdout=SHOW_OK)
        scheduler = BackupScheduler(verbose=False, runner=runner)
        service = systemd_dir / "noust-backup-example-com.service"
        service.write_text(LEGACY_SERVICE)
        scheduler.list_schedules()
        reloads = len([c for c in runner.calls_to("systemctl") if c[1] == "daemon-reload"])

        scheduler.list_schedules()

        after = len([c for c in runner.calls_to("systemctl") if c[1] == "daemon-reload"])
        assert after == reloads

    def test_adopted_retention_stays_null(self, runner: FakeRunner, systemd_dir: Path) -> None:
        runner.script(("systemctl", "list-timers"), stdout=LIST_LINE)
        runner.script(("systemctl", "show"), stdout=SHOW_OK)
        runner.script(("systemctl", "is-enabled"), stdout="enabled")
        scheduler = BackupScheduler(verbose=False, runner=runner)

        listed = scheduler.list_schedules()
        fetched = scheduler.get_schedule("example.com")

        assert listed[0]["retention_count"] == ""
        assert fetched is not None
        assert fetched.retention_count is None
        assert fetched.retention_days is None


class TestRunScheduleWithoutARow:
    def test_falls_back_to_a_2_1_backup_and_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_create(self: BackupManager, **kwargs: Any) -> BackupMetadata:
            captured.update(kwargs)
            return BackupMetadata.from_dict(
                {
                    "id": f"{APP}_20260101_000000",
                    "domain": DOMAIN,
                    "app_name": APP,
                    "created_at": "2026-01-01T00:00:00",
                }
            )

        monkeypatch.setattr(BackupManager, "create", fake_create)
        pushed: list[str] = []
        monkeypatch.setattr(BackupDestinationManager, "push", lambda *a, **k: pushed.append("push"))
        notified: list[NotificationEvent] = []
        monkeypatch.setattr(
            "noust.core.notifier.Notifier.notify", lambda self, event: notified.append(event)
        )

        result = run_schedule(DOMAIN)

        assert result["schedule_missing"] is True
        assert captured["include_databases"] is True
        assert captured["retention_count"] is None
        assert captured["retention_days"] is None
        assert pushed == []
        assert any("missing" in event.title for event in notified)

    def test_waits_for_an_operation_holding_the_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        get_store().save_backup_schedule(BackupScheduleRecord(app_domain=DOMAIN, schedule="daily"))
        attempts: list[int] = []

        def busy_once(self: BackupManager, **kwargs: Any) -> BackupMetadata:
            attempts.append(1)
            if len(attempts) == 1:
                raise AppBusyError(DOMAIN, "backup", None)
            return BackupMetadata.from_dict(
                {
                    "id": f"{APP}_20260101_000000",
                    "domain": DOMAIN,
                    "app_name": APP,
                    "created_at": "2026-01-01T00:00:00",
                }
            )

        slept: list[float] = []
        monkeypatch.setattr(BackupManager, "create", busy_once)
        monkeypatch.setattr(scheduler_module.time, "sleep", slept.append)

        result = run_schedule(DOMAIN)

        assert len(attempts) == 2
        assert slept
        assert result["backup_id"] == f"{APP}_20260101_000000"


# ---------------------------------------------------------------------------
# 1. An encrypted destination's key can be entered back, and is not lost
# ---------------------------------------------------------------------------


class TestCryptKey:
    def test_parses_what_show_key_prints(self) -> None:
        printed = (
            "\x1b[33m!\x1b[0m Encryption keys for nas. Store them somewhere safe.\n"
            "\x1b[34mi\x1b[0m   password:  first-passphrase\n"
            "\x1b[34mi\x1b[0m   password2: second-passphrase\n"
        )
        assert parse_crypt_key(printed) == {
            "password": "first-passphrase",
            "password2": "second-passphrase",
        }

    def test_parses_json_and_two_lines(self) -> None:
        expected = {"password": "a1", "password2": "b2"}
        assert parse_crypt_key(json.dumps(expected)) == expected
        assert parse_crypt_key("a1\nb2\n") == expected

    @pytest.mark.parametrize("text", ["", "only-one-line", '{"password": "x"}', "a\nb\nc"])
    def test_refuses_an_incomplete_key(self, text: str) -> None:
        with pytest.raises(BackupError):
            parse_crypt_key(text)

    def test_add_uses_the_given_key(self, destinations: BackupDestinationManager) -> None:
        destinations.add(
            "nas",
            "sftp",
            _sftp_fields(),
            encrypted=True,
            crypt_key={"password": "kept-1", "password2": "kept-2"},
        )
        assert destinations.show_key("nas") == {"password": "kept-1", "password2": "kept-2"}

    def test_a_key_without_encryption_is_refused(
        self, destinations: BackupDestinationManager
    ) -> None:
        with pytest.raises(BackupError):
            destinations.add(
                "nas", "sftp", _sftp_fields(), crypt_key={"password": "a", "password2": "b"}
            )
        assert destinations.get("nas") is None

    def test_an_incomplete_key_leaves_nothing_behind(
        self, destinations: BackupDestinationManager
    ) -> None:
        with pytest.raises(BackupError):
            destinations.add(
                "nas", "sftp", _sftp_fields(), encrypted=True, crypt_key={"password": "a"}
            )
        assert destinations.get("nas") is None
        assert SecretStore().read_json("backup-destinations/nas") == {}

    def test_removing_an_encrypted_destination_needs_the_key_saved(
        self, destinations: BackupDestinationManager
    ) -> None:
        destinations.add("nas", "sftp", _sftp_fields(), encrypted=True)

        with pytest.raises(BackupError, match="encrypted") as excinfo:
            destinations.remove("nas")
        assert "show-key" in (excinfo.value.details or "")
        assert destinations.get("nas") is not None

        destinations.remove("nas", key_saved=True)
        assert destinations.get("nas") is None

    def test_an_encrypted_destination_whose_key_is_gone_can_be_removed(
        self, destinations: BackupDestinationManager
    ) -> None:
        destinations.add("nas", "sftp", _sftp_fields(), encrypted=True)
        SecretStore().write_json("backup-destinations/nas", {"pass": "x"})

        destinations.remove("nas")

        assert destinations.get("nas") is None


# ---------------------------------------------------------------------------
# 7. Names and paths
# ---------------------------------------------------------------------------


class TestNames:
    def test_a_destination_name_with_a_trailing_newline_is_refused(self) -> None:
        with pytest.raises(BackupError):
            validate_destination_name("nas\n")

    def test_a_secret_name_with_a_trailing_newline_is_refused(self) -> None:
        with pytest.raises(ConfigError):
            SecretStore().read_json("backup-destinations/nas\n")

    @pytest.mark.parametrize("app_name", ["../..", "..", "a/b", ""])
    def test_an_app_name_that_is_a_path_is_refused(
        self, destinations: BackupDestinationManager, runner: FakeRunner, app_name: str
    ) -> None:
        destinations.add("nas", "sftp", _sftp_fields())
        with pytest.raises(BackupError, match="Invalid application name"):
            destinations.remote_list("nas", app_name)
        assert not [c for c in runner.calls_to("rclone") if c[1] == "lsjson"]


# ---------------------------------------------------------------------------
# 5/6. Pushing: verification failures, and retention on a shared folder
# ---------------------------------------------------------------------------

TARGET = f"nas:wasm-backups/{APP}"


def _push_setup(
    destinations: BackupDestinationManager, manager: BackupManager, day: int = 2
) -> BackupMetadata:
    destinations.add("nas", "sftp", _sftp_fields())
    return _write_backup(manager, day, [SCHEDULED_TAG], origin=server_id())


def _listing(manager: BackupManager, metadata: BackupMetadata, **archive: Any) -> str:
    sidecar = manager.backup_dir / APP / f"{metadata.id}.json"
    entries = [
        {"Name": f"{metadata.id}.tar.gz", "Size": 7, "ModTime": "2026-01-02T00:00:00Z", **archive},
        {"Name": f"{metadata.id}.json", "Size": sidecar.stat().st_size},
    ]
    return json.dumps(entries)


class TestPushVerification:
    def test_a_failed_verification_removes_the_bad_copy(
        self, destinations: BackupDestinationManager, manager: BackupManager, runner: FakeRunner
    ) -> None:
        metadata = _push_setup(destinations, manager)
        runner.script(
            ("rclone", "lsjson", "--hash", TARGET), stdout=_listing(manager, metadata, Size=3)
        )

        with pytest.raises(BackupError, match="Size mismatch") as excinfo:
            destinations.push(metadata, "nas", backup_manager=manager)

        deleted = [c[2] for c in runner.calls_to("rclone") if c[1] == "deletefile"]
        assert deleted == [f"{TARGET}/{metadata.id}.tar.gz", f"{TARGET}/{metadata.id}.json"]
        assert "was removed from the destination" in (excinfo.value.details or "")

    def test_a_bad_copy_that_cannot_be_removed_reports_both_errors(
        self, destinations: BackupDestinationManager, manager: BackupManager, runner: FakeRunner
    ) -> None:
        metadata = _push_setup(destinations, manager)
        runner.script(
            ("rclone", "lsjson", "--hash", TARGET), stdout=_listing(manager, metadata, Size=3)
        )
        runner.script(("rclone", "deletefile"), stderr="permission denied", exit_code=1)

        with pytest.raises(BackupError, match="Size mismatch") as excinfo:
            destinations.push(metadata, "nas", backup_manager=manager)

        details = excinfo.value.details or ""
        assert "Local file is 7 bytes" in details
        assert "could not be removed" in details
        assert "permission denied" in details

    def test_an_archive_whose_sidecar_failed_to_upload_is_removed(
        self, destinations: BackupDestinationManager, manager: BackupManager, runner: FakeRunner
    ) -> None:
        metadata = _push_setup(destinations, manager)
        sidecar = manager.backup_dir / APP / f"{metadata.id}.json"
        runner.script(("rclone", "copyto", str(sidecar)), stderr="quota exceeded", exit_code=1)

        with pytest.raises(BackupError, match="Failed to upload") as excinfo:
            destinations.push(metadata, "nas", backup_manager=manager)

        deleted = [c[2] for c in runner.calls_to("rclone") if c[1] == "deletefile"]
        assert f"{TARGET}/{metadata.id}.tar.gz" in deleted
        assert "quota exceeded" in (excinfo.value.details or "")

    def test_size_only_verification_is_announced(
        self,
        destinations: BackupDestinationManager,
        manager: BackupManager,
        runner: FakeRunner,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        metadata = _push_setup(destinations, manager)
        runner.script(("rclone", "lsjson", "--hash", TARGET), stdout=_listing(manager, metadata))

        summary = destinations.push(metadata, "nas", backup_manager=manager)

        assert summary["verified_by"] == "size"
        assert "verified by its size only" in capsys.readouterr().out

    def test_a_matching_hash_is_reported(
        self, destinations: BackupDestinationManager, manager: BackupManager, runner: FakeRunner
    ) -> None:
        import hashlib

        metadata = _push_setup(destinations, manager)
        md5 = hashlib.md5(b"content").hexdigest()  # noqa: S324 - what the backend reports
        runner.script(
            ("rclone", "lsjson", "--hash", TARGET),
            stdout=_listing(manager, metadata, Hashes={"md5": md5}),
        )

        summary = destinations.push(metadata, "nas", backup_manager=manager)

        assert summary["verified_by"] == "md5"


class TestRemoteRetentionOnASharedFolder:
    def _entries(self, ids: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "Name": f"{backup_id}.tar.gz",
                "Size": 7,
                "ModTime": f"2026-01-{index + 1:02d}T00:00:00Z",
            }
            for index, backup_id in enumerate(ids)
        ]

    def test_only_this_servers_backups_are_counted_and_deleted(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        destinations.add("nas", "sftp", _sftp_fields())
        own = server_id()
        mine_old, foreign, legacy, mine_new = (
            f"{APP}_20260101_000000",
            f"{APP}_20260102_000000",
            f"{APP}_20260103_000000",
            f"{APP}_20260104_000000",
        )
        sidecars = (
            json.dumps({"id": mine_old, "origin": own})
            + "\n"
            + json.dumps({"id": foreign, "origin": "0123456789abcdef"})
            + json.dumps({"id": legacy})
            + json.dumps({"id": mine_new, "origin": own})
        )
        runner.script(("rclone", "cat", TARGET), stdout=sidecars)

        deleted = destinations._apply_remote_retention(
            destinations.remote_env("nas"),
            TARGET,
            self._entries([mine_old, foreign, legacy, mine_new]),
            1,
            None,
            [],
        )

        assert deleted == [mine_old]

    def test_an_unreadable_sidecar_is_kept(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        destinations.add("nas", "sftp", _sftp_fields())
        old, new = f"{APP}_20260101_000000", f"{APP}_20260102_000000"
        runner.script(
            ("rclone", "cat", TARGET),
            stdout=json.dumps({"id": new, "origin": server_id()})
            + "{not json"
            + json.dumps({"id": old, "origin": server_id()}),
        )

        deleted = destinations._apply_remote_retention(
            destinations.remote_env("nas"), TARGET, self._entries([old, new]), 1, None, []
        )

        assert deleted == []

    def test_a_new_backup_records_this_server(self) -> None:
        metadata = BackupMetadata.from_dict(
            {
                "id": "x_20260101_000000",
                "domain": DOMAIN,
                "app_name": APP,
                "created_at": "2026-01-01T00:00:00",
                "origin": server_id(),
            }
        )
        assert metadata.to_dict()["origin"] == server_id()

    def test_server_id_without_a_machine_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(backup_manager_module, "MACHINE_ID_PATH", tmp_path / "missing")
        first = server_id()
        assert len(first) == 16
        assert first == server_id()


# ---------------------------------------------------------------------------
# 7/8. Downloading and restoring from a destination
# ---------------------------------------------------------------------------


class _WritingRunner(FakeRunner):
    """A fake runner whose ``rclone copyto`` writes what a download would."""

    def __init__(self, archive: bytes, sidecar: dict[str, Any]) -> None:
        super().__init__()
        self.archive = archive
        self.sidecar = sidecar

    def run(self, argv, **kwargs):  # type: ignore[no-untyped-def,override]
        result = super().run(argv, **kwargs)
        if len(argv) >= 4 and argv[0] == "rclone" and argv[1] == "copyto":
            destination = Path(argv[3])
            if destination.name.endswith(".tar.gz"):
                destination.write_bytes(self.archive)
            else:
                destination.write_text(json.dumps(self.sidecar))
        return result


BACKUP_ID = f"{APP}_20260101_000000"


def _downloader(sidecar: dict[str, Any], *, listed: bool = True) -> BackupDestinationManager:
    runner = _WritingRunner(b"archive-bytes", sidecar)
    runner.only_knows("rclone")
    runner.script(("rclone", "obscure", "-"), stdout="OBSCURED\n")
    if listed:
        runner.script(
            ("rclone", "lsjson", TARGET),
            stdout=json.dumps([{"Name": f"{BACKUP_ID}.tar.gz", "Size": 13}]),
        )
    downloader = BackupDestinationManager(runner=runner)
    downloader.add("nas", "sftp", _sftp_fields())
    return downloader


def _sidecar(**overrides: Any) -> dict[str, Any]:
    data = {"id": BACKUP_ID, "domain": DOMAIN, "app_name": APP, "created_at": "2026-01-01T00:00:00"}
    data.update(overrides)
    return data


class TestDownload:
    def test_a_sidecar_naming_another_application_is_refused(self, tmp_path: Path) -> None:
        downloader = _downloader(_sidecar(domain="victim.example.com"))
        with pytest.raises(BackupError, match="belongs to"):
            downloader.download("nas", BACKUP_ID, APP, tmp_path / "staging")

    def test_a_sidecar_naming_another_backup_is_refused(self, tmp_path: Path) -> None:
        downloader = _downloader(_sidecar(id=f"{APP}_20250101_000000"))
        with pytest.raises(BackupError, match="another backup"):
            downloader.download("nas", BACKUP_ID, APP, tmp_path / "staging")

    def test_a_backup_that_is_not_there_is_refused_before_downloading(self, tmp_path: Path) -> None:
        downloader = _downloader(_sidecar(), listed=False)
        with pytest.raises(BackupError, match="not found"):
            downloader.download("nas", BACKUP_ID, APP, tmp_path / "staging")
        runner = downloader.runner
        assert isinstance(runner, FakeRunner)
        assert not [c for c in runner.calls_to("rclone") if c[1] == "copyto"]

    def test_not_enough_space_is_refused_before_downloading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        downloader = _downloader(_sidecar())
        usage = namedtuple("usage", "total used free")
        monkeypatch.setattr(
            "noust.managers.backup_destinations.shutil.disk_usage", lambda path: usage(10, 5, 5)
        )
        with pytest.raises(BackupError, match="Not enough space"):
            downloader.download("nas", BACKUP_ID, APP, tmp_path / "staging")
        runner = downloader.runner
        assert isinstance(runner, FakeRunner)
        assert not [c for c in runner.calls_to("rclone") if c[1] == "copyto"]

    def test_restore_remote_stages_under_the_backup_directory_and_cleans_up(
        self, manager: BackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        downloader = _downloader(_sidecar())
        seen: dict[str, Any] = {}

        def fake_restore(archive: Path, **kwargs: Any) -> bool:
            seen["archive"] = archive
            seen["exists"] = archive.is_file()
            seen.update(kwargs)
            return True

        monkeypatch.setattr(manager, "restore_archive", fake_restore)

        domain = downloader.restore_remote("nas", BACKUP_ID, APP, backup_manager=manager)

        assert domain == DOMAIN
        assert seen["exists"] is True
        assert seen["archive"].is_relative_to(manager.backup_dir / STAGING_DIR_NAME)
        assert seen["target_domain"] == DOMAIN
        assert not seen["archive"].parent.exists()
        # A download in progress is never listed as a local backup.
        assert manager.list_backups() == []
