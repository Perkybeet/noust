# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A schedule that keeps no copy on this server (checklist H-13, for the rest of the backups).

Database dumps already could (``tests/test_database_backup_remote_only.py``). This is the same
rule for an application's backups, the data of an application and Noust's own state: once every
destination of the schedule holds the archive and its upload was checked, the local file goes.
What is defended is the direction of the risk: an archive that did not reach every destination,
or that has no destination to reach, is never deleted, and a backup taken by hand is never this
rule's.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import Context
from noust.cli.commands import backup as backup_cli
from noust.cli.commands import backup_volumes, self_backup
from noust.core.exceptions import BackupError, ValidationError
from noust.core.store import BackupScheduleRecord, get_store
from noust.managers.backup_destinations import BackupDestinationManager
from noust.managers.backup_manager import BackupManager, BackupMetadata
from noust.managers.backup_scheduler import BackupSchedule, BackupScheduler, run_schedule
from noust.managers.self_backup import SelfBackupManager
from noust.managers.volume_backups import VolumeBackupManager
from tests.database_backup_support import RcloneRunner
from tests.test_notifier import config  # noqa: F401
from tests.test_self_backup import PASSPHRASE, FakeTimers, old, server  # noqa: F401
from tests.test_self_backup import Runner as SelfRunner
from tests.test_self_backup import audited as self_audited  # noqa: F401
from tests.test_self_backup import runner as self_runner  # noqa: F401
from tests.test_volume_backups import (  # noqa: F401
    DOMAIN,
    _reset_store,
    app,
    app_dir,
    audited,
    manager,
    runner,
    services,
)

# Noust's own state is sealed by the real openssl, as in tests/test_self_backup.py.
pytestmark = pytest.mark.allow_subprocess


def add_destination(runner: RcloneRunner, name: str = "nas") -> None:
    """Make a destination the way an operator would: an SFTP server."""
    from noust.managers.backup_destination_files import DestinationFileManager

    DestinationFileManager(runner=runner).add(
        name, "sftp", {"host": "nas.example.com", "user": "noust", "pass": "s3cr3t"}
    )


# ============================================================ data (volumes)


class TestDataPolicy:
    def test_it_is_stored_and_shown_back(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_destination(runner)

        view = manager.set_policy(
            DOMAIN, targets=["path:uploads"], destinations=[{"name": "nas"}], remote_only=True
        )

        assert view.to_dict()["remote_only"] is True
        assert manager.records.policy(DOMAIN).remote_only is True  # type: ignore[union-attr]

    def test_it_is_off_unless_asked_for(self, manager: VolumeBackupManager) -> None:
        view = manager.set_policy(DOMAIN, targets=["path:uploads"])

        assert view.to_dict()["remote_only"] is False

    def test_a_policy_with_no_destination_is_refused_and_leaves_nothing(
        self, manager: VolumeBackupManager
    ) -> None:
        with pytest.raises(ValidationError) as refused:
            manager.set_policy(DOMAIN, targets=["path:uploads"], remote_only=True)

        assert refused.value.field == "remote_only"
        assert "destination" in str(refused.value)
        assert manager.records.policy(DOMAIN) is None
        assert manager.scheduler.installed == {}  # type: ignore[union-attr]

    def test_a_run_that_reached_every_destination_leaves_nothing_here(
        self,
        manager: VolumeBackupManager,
        runner: RcloneRunner,
        audited: list[dict[str, Any]],
    ) -> None:
        add_destination(runner)
        manager.set_policy(
            DOMAIN,
            targets=["path:uploads"],
            destinations=[{"name": "nas"}],
            remote_only=True,
        )

        result = manager.run_policy(DOMAIN)

        (taken,) = result["backups"]
        assert result["destinations"]["nas"][taken]["ok"] is True
        assert manager.list_backups(DOMAIN) == []
        assert result["removed_after_sending"] == [taken]
        removal = [e for e in audited if e["event"] == "volume_backup.delete"]
        assert removal and removal[0]["details"]["reason"] == "remote_only"
        policy = manager.records.policy(DOMAIN)
        assert policy is not None and policy.last_status == "ok" and policy.last_backup == taken

    def test_the_copy_on_the_destination_is_the_one_that_remains(
        self, manager: VolumeBackupManager, runner: RcloneRunner, tmp_path: Path
    ) -> None:
        add_destination(runner)
        manager.set_policy(
            DOMAIN, targets=["path:uploads"], destinations=[{"name": "nas"}], remote_only=True
        )

        (taken,) = manager.run_policy(DOMAIN)["backups"]

        remote = list((tmp_path / "remote" / "nas").rglob(f"{taken}.tar.gz"))
        assert len(remote) == 1 and remote[0].stat().st_size > 0

    def test_a_run_that_failed_to_reach_one_destination_keeps_what_it_took(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_destination(runner, "nas")
        add_destination(runner, "cloud")
        runner.refusing["nas"] = "connection refused"
        manager.set_policy(
            DOMAIN,
            targets=["path:uploads"],
            destinations=[{"name": "nas"}, {"name": "cloud"}],
            remote_only=True,
        )

        with pytest.raises(BackupError):
            manager.run_policy(DOMAIN)

        assert len(manager.list_backups(DOMAIN)) == 1

    def test_without_the_setting_the_local_copy_stays(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_destination(runner)
        manager.set_policy(DOMAIN, targets=["path:uploads"], destinations=[{"name": "nas"}])

        manager.run_policy(DOMAIN)

        assert len(manager.list_backups(DOMAIN)) == 1

    def test_a_backup_taken_by_hand_is_never_this_rules(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_destination(runner)
        (by_hand,) = manager.create(DOMAIN, targets=["path:uploads"])
        manager.set_policy(
            DOMAIN, targets=["path:uploads"], destinations=[{"name": "nas"}], remote_only=True
        )

        manager.run_policy(DOMAIN)

        assert [entry.id for entry in manager.list_backups(DOMAIN)] == [by_hand.id]

    def test_the_command_takes_the_flag(
        self, manager: VolumeBackupManager, runner: RcloneRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        add_destination(runner)
        monkeypatch.setattr(backup_volumes, "_manager", lambda ctx: manager)

        result = CliRunner().invoke(
            backup_volumes.set_command,
            [DOMAIN, "--target", "path:uploads", "--to", "nas", "--remote-only"],
            obj=Context(),
        )

        assert result.exit_code == 0, result.output
        assert "nothing (each backup goes once every destination holds it)" in result.output


# ==================================================== Noust's own state


class TestSelfBackupSchedule:
    @pytest.fixture
    def own(self, old, self_runner: SelfRunner) -> SelfBackupManager:
        from noust.managers.backup_destination_files import DestinationFileManager

        return old.manager(
            self_runner,
            scheduler=FakeTimers(),  # type: ignore[arg-type]
            destinations=DestinationFileManager(runner=self_runner),
        )

    def test_a_schedule_with_no_destination_is_refused(self, own: SelfBackupManager) -> None:
        with pytest.raises(ValidationError) as refused:
            own.set_schedule(passphrase=PASSPHRASE, remote_only=True)

        assert refused.value.field == "remote_only"
        assert own.records.get() is None

    def test_it_is_stored_and_shown_back(
        self, own: SelfBackupManager, self_runner: SelfRunner
    ) -> None:
        add_destination(self_runner)

        view = own.set_schedule(
            passphrase=PASSPHRASE, destinations=[{"name": "nas"}], remote_only=True
        )

        assert view["remote_only"] is True

    def test_a_run_that_reached_every_destination_leaves_no_bundle_here(
        self, own: SelfBackupManager, self_runner: SelfRunner, tmp_path: Path
    ) -> None:
        add_destination(self_runner)
        own.set_schedule(passphrase=PASSPHRASE, destinations=[{"name": "nas"}], remote_only=True)

        result = own.run_schedule()

        assert own.entries() == []
        assert result["removed_after_sending"] == [result["file"]]
        remote = tmp_path / "remote" / "nas" / "noust-backups" / "self-backups" / own.host_slug()
        assert (remote / result["file"]).is_file()
        schedule = own.records.get()
        assert schedule is not None and schedule.last_status == "ok"

    def test_a_bundle_that_was_not_sent_everywhere_stays(
        self, own: SelfBackupManager, self_runner: SelfRunner
    ) -> None:
        add_destination(self_runner)
        self_runner.refusing["nas"] = "connection refused"
        own.set_schedule(passphrase=PASSPHRASE, destinations=[{"name": "nas"}], remote_only=True)

        with pytest.raises(BackupError):
            own.run_schedule()

        assert len(own.entries()) == 1

    def test_the_command_takes_the_flag(
        self,
        own: SelfBackupManager,
        self_runner: SelfRunner,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        add_destination(self_runner)
        monkeypatch.setattr(self_backup, "_manager", lambda ctx: own)
        passphrase = tmp_path / "pass"
        passphrase.write_text(PASSPHRASE)
        passphrase.chmod(0o600)

        result = CliRunner().invoke(
            self_backup.set_command,
            ["--to", "nas", "--remote-only", "--passphrase-file", str(passphrase)],
            obj=Context(),
        )

        assert result.exit_code == 0, result.output
        assert "nothing (each bundle goes once every destination holds it)" in result.output


# ============================================ an application's own schedule


def _metadata(backup_id: str, domain: str = "shop.example.com") -> BackupMetadata:
    return BackupMetadata(
        id=backup_id,
        domain=domain,
        app_name=domain.replace(".", "-"),
        created_at="2026-01-01T00:00:00",
        size_bytes=123,
        app_type="static",
        version="2.0.0",
        description="",
        includes_env=True,
        includes_node_modules=False,
    )


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


class TestApplicationSchedule:
    def schedule(self, **fields: Any) -> BackupScheduleRecord:
        return get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="shop.example.com",
                schedule="daily",
                destinations=[{"name": "nas"}, {"name": "s3"}],
                **fields,
            )
        )

    @pytest.fixture
    def taken(self, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        """What the manager deleted; creating returns one backup, pushing succeeds."""
        deleted: list[str] = []
        monkeypatch.setattr(
            BackupManager, "create", lambda self, **kwargs: _metadata("shop-example-com_1")
        )
        monkeypatch.setattr(
            BackupManager, "delete", lambda self, backup_id: deleted.append(backup_id) or True
        )
        monkeypatch.setattr(BackupManager, "get_backup", lambda self, backup_id: None)
        monkeypatch.setattr(
            BackupDestinationManager,
            "push",
            lambda self, backup, name, **kwargs: {"destination": name, "backup_id": backup.id},
        )
        return deleted

    def test_the_row_remembers_it(self) -> None:
        stored = self.schedule(remote_only=True)

        assert stored.remote_only is True
        assert get_store().get_backup_schedule("shop.example.com").remote_only is True  # type: ignore[union-attr]

    def test_it_is_off_by_default(self) -> None:
        assert self.schedule().remote_only is False

    def test_the_scheduler_refuses_it_without_a_destination(
        self, systemd_dir: Path, runner: RcloneRunner
    ) -> None:
        with pytest.raises(ValidationError) as refused:
            BackupScheduler().create_schedule(
                BackupSchedule(
                    domain="shop.example.com",
                    app_name="shop-example-com",
                    schedule="daily",
                    remote_only=True,
                )
            )

        assert refused.value.field == "remote_only"
        assert get_store().get_backup_schedule("shop.example.com") is None
        assert not list(systemd_dir.iterdir())

    def test_the_scheduler_writes_and_reads_it_back(
        self, systemd_dir: Path, runner: RcloneRunner
    ) -> None:
        scheduler = BackupScheduler()
        scheduler.create_schedule(
            BackupSchedule(
                domain="shop.example.com",
                app_name="shop-example-com",
                schedule="daily",
                remote_only=True,
                destinations=[{"name": "nas"}],
            )
        )

        assert get_store().get_backup_schedule("shop.example.com").remote_only is True  # type: ignore[union-attr]
        read = scheduler.get_schedule("shop.example.com")
        assert read is not None and read.remote_only is True

    def test_a_run_that_reached_every_destination_deletes_the_local_backup(
        self, taken: list[str]
    ) -> None:
        self.schedule(remote_only=True)

        result = run_schedule("shop.example.com")

        assert taken == ["shop-example-com_1"]
        assert result["removed_after_sending"] == ["shop-example-com_1"]
        assert result["kept_locally"] is False

    def test_without_the_setting_it_is_kept(self, taken: list[str]) -> None:
        self.schedule()

        result = run_schedule("shop.example.com")

        assert taken == []
        assert result["kept_locally"] is True

    def test_a_destination_that_failed_keeps_the_backup(
        self, taken: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.schedule(remote_only=True)

        def push(self: Any, backup: BackupMetadata, name: str, **kwargs: Any) -> dict[str, Any]:
            if name == "s3":
                raise BackupError("Failed to upload to s3", details="connection refused")
            return {"destination": name}

        monkeypatch.setattr(BackupDestinationManager, "push", push)

        with pytest.raises(BackupError, match="could not be sent to: s3"):
            run_schedule("shop.example.com")

        assert taken == []

    def test_a_row_edited_to_have_no_destination_deletes_nothing(self, taken: list[str]) -> None:
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="shop.example.com", schedule="daily", remote_only=True, destinations=[]
            )
        )

        result = run_schedule("shop.example.com")

        assert taken == [] and result["kept_locally"] is True

    def test_updating_the_schedule_from_the_command_keeps_the_setting(
        self, systemd_dir: Path, runner: RcloneRunner
    ) -> None:
        scheduler = BackupScheduler()
        scheduler.create_schedule(
            BackupSchedule(
                domain="shop.example.com",
                app_name="shop-example-com",
                schedule="daily",
                remote_only=True,
                destinations=[{"name": "nas"}],
            )
        )

        result = CliRunner().invoke(
            backup_cli.schedule_update,
            ["shop.example.com", "--destination", "nas"],
            obj=Context(),
        )
        assert result.exit_code == 0, result.output
        assert get_store().get_backup_schedule("shop.example.com").remote_only is True  # type: ignore[union-attr]

        result = CliRunner().invoke(
            backup_cli.schedule_update,
            ["shop.example.com", "--destination", "nas", "--keep-local"],
            obj=Context(),
        )
        assert result.exit_code == 0, result.output
        assert get_store().get_backup_schedule("shop.example.com").remote_only is False  # type: ignore[union-attr]

    def test_the_create_command_takes_the_flag(
        self, systemd_dir: Path, runner: RcloneRunner
    ) -> None:
        result = CliRunner().invoke(
            backup_cli.schedule_create,
            ["shop.example.com", "--destination", "nas", "--remote-only"],
            obj=Context(),
        )

        assert result.exit_code == 0, result.output
        assert get_store().get_backup_schedule("shop.example.com").remote_only is True  # type: ignore[union-attr]
        assert "Kept on this server: nothing" in result.output

    def test_the_create_command_refuses_it_without_a_destination(
        self, systemd_dir: Path, runner: RcloneRunner
    ) -> None:
        result = CliRunner().invoke(
            backup_cli.schedule_create, ["shop.example.com", "--remote-only"], obj=Context()
        )

        assert result.exit_code != 0
        assert "needs a destination" in result.output
        assert get_store().get_backup_schedule("shop.example.com") is None
