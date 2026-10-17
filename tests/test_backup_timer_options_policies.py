# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every kind of schedule can be given a time limit and a time zone (checklist H-01).

The managers validate them, hand them to the scheduler that writes the units, and show them back in
the timer's state. The real scheduler writes into a temporary systemd directory, so what is checked
is the unit an operator would find.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

import pytest

from noust.core.exceptions import ValidationError
from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.backup_scheduler import BackupScheduler
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.service import DatabaseService
from noust.managers.volume_backups import VolumeBackupManager
from tests.test_database_backups import (  # noqa: F401
    RcloneRunner,
    _reset_store,
    dumps_dir,
    engine,
    service,
)
from tests.test_database_backups import runner as db_runner  # noqa: F401
from tests.test_self_backup import PASSPHRASE, Runner, Server, old  # noqa: F401
from tests.test_volume_backups import DOMAIN as VOLUME_DOMAIN
from tests.test_volume_backups import app, app_dir, services  # noqa: F401
from tests.test_volume_backups import manager as volume_manager  # noqa: F401
from tests.test_volume_backups import runner as volume_runner  # noqa: F401


@pytest.fixture
def zoneinfo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "zoneinfo"
    for name in ("UTC", "Europe/Madrid"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"TZif2")
    monkeypatch.setattr("noust.managers.backup_timer_options.ZONEINFO_DIRS", (root,))


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


class TestDatabasePolicies:
    def backups(self, service: DatabaseService, runner: RcloneRunner) -> DatabaseBackups:
        return DatabaseBackups(
            service,
            scheduler=BackupScheduler(runner=runner),
            destinations=DestinationFileManager(runner=runner),
        )

    def test_the_limit_and_the_zone_reach_the_units_and_come_back_in_the_view(
        self,
        service: DatabaseService,
        db_runner: RcloneRunner,
        systemd_dir: Path,
        zoneinfo: None,
    ) -> None:
        backups = self.backups(service, db_runner)

        view = backups.set_policy("postgresql", "shop", max_runtime="2h", zone="Europe/Madrid")

        unit = "noust-backup-db-postgresql-shop"
        assert "TimeoutStartSec=7200\n" in (systemd_dir / f"{unit}.service").read_text()
        assert "Europe/Madrid" in (systemd_dir / f"{unit}.timer").read_text()
        timer = view.to_dict()["timer"]
        assert timer["max_runtime_seconds"] == 7200 and timer["timezone"] == "Europe/Madrid"

    def test_saving_the_policy_again_without_them_clears_them(
        self,
        service: DatabaseService,
        db_runner: RcloneRunner,
        systemd_dir: Path,
        zoneinfo: None,
    ) -> None:
        backups = self.backups(service, db_runner)
        backups.set_policy("postgresql", "shop", max_runtime="2h", zone="UTC")

        backups.set_policy("postgresql", "shop")

        unit = "noust-backup-db-postgresql-shop"
        assert "TimeoutStartSec" not in (systemd_dir / f"{unit}.service").read_text()

    @pytest.mark.parametrize(
        ("limit", "zone"), [("10s", None), (None, "Mars/Olympus"), ("nonsense", None)]
    )
    def test_a_bad_value_writes_nothing(
        self,
        limit: str | None,
        zone: str | None,
        service: DatabaseService,
        db_runner: RcloneRunner,
        systemd_dir: Path,
        zoneinfo: None,
    ) -> None:
        backups = self.backups(service, db_runner)

        with pytest.raises(ValidationError):
            backups.set_policy("postgresql", "shop", max_runtime=limit, zone=zone)

        assert list(systemd_dir.iterdir()) == []


class TestDataPolicies:
    def test_the_limit_and_the_zone_reach_the_units(
        self,
        volume_manager: VolumeBackupManager,
        systemd_dir: Path,
        zoneinfo: None,
        volume_runner: RcloneRunner,
    ) -> None:
        volume_manager._scheduler = BackupScheduler(runner=volume_runner)

        view = volume_manager.set_policy(
            VOLUME_DOMAIN, targets=["path:uploads"], max_runtime="45m", zone="UTC"
        )

        unit = "noust-backup-vol-shop-example-com"
        assert "TimeoutStartSec=2700\n" in (systemd_dir / f"{unit}.service").read_text()
        assert "02:00:00 UTC" in (systemd_dir / f"{unit}.timer").read_text()
        assert view.to_dict()["timer"]["max_runtime_seconds"] == 2700


class TestNoustsOwnState:
    def test_the_limit_and_the_zone_reach_the_units_and_the_schedule_shows_them(
        self, old: Server, runner: Runner, systemd_dir: Path, zoneinfo: None
    ) -> None:
        manager = old.manager(runner, scheduler=BackupScheduler(runner=runner))

        schedule = manager.set_schedule(
            passphrase=PASSPHRASE, max_runtime="3h", zone="Europe/Madrid"
        )

        assert "TimeoutStartSec=10800\n" in (systemd_dir / "noust-backup-self.service").read_text()
        assert "Europe/Madrid" in (systemd_dir / "noust-backup-self.timer").read_text()
        assert schedule["timer"]["max_runtime_seconds"] == 10800
        assert schedule["timer"]["timezone"] == "Europe/Madrid"
