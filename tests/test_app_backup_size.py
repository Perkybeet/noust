# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's scheduled backups are kept by size as well as by number and age (H-04).

The rule is the one every other policy uses (:func:`noust.managers.retention.select_expired`):
the newest that fit in the budget stay, counting back from the newest; one that does not fit
ends the list; the newest always stays. What is defended is the scope: a schedule's size
limit reaches the backups the schedule made, here and on each destination, and never a
manual, pre-deploy or rollback-safety backup.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import Context
from noust.cli.commands.backup import _parse_destination_spec, cli
from noust.core.runner import FakeRunner
from noust.core.store import BackupScheduleRecord, get_store
from noust.managers.backup_destinations import BackupDestinationManager
from noust.managers.backup_manager import SCHEDULED_TAG, BackupManager
from noust.managers.backup_scheduler import BackupSchedule, BackupScheduler
from tests.test_backup_safety_22 import (  # noqa: F401
    APP,
    DOMAIN,
    TARGET,
    TestRemoteRetentionOnASharedFolder,
    _listing,
    _push_setup,
    _remaining,
    _reset_store,
    _sftp_fields,
    _write_backup,
    destinations,
    manager,
    systemd_dir,
)

GB = 1024**3


def sized(manager: BackupManager, day: int, tags: list[str], size: int):  # type: ignore[no-untyped-def]
    """A backup of the sort the safety tests write, claiming ``size`` bytes."""
    metadata = _write_backup(manager, day, tags)
    sidecar = manager.backup_dir / APP / f"{metadata.id}.json"
    data = json.loads(sidecar.read_text())
    data["size_bytes"] = size
    sidecar.write_text(json.dumps(data))
    return metadata


class TestLocal:
    def test_the_newest_that_fit_stay_and_only_the_schedules_own_are_judged(
        self, manager: BackupManager
    ) -> None:
        manual = sized(manager, 1, [], 500)
        pre_deploy = sized(manager, 2, ["pre-deploy", "auto"], 500)
        scheduled = [sized(manager, day, [SCHEDULED_TAG, "auto"], 10) for day in (3, 4, 5, 6)]

        deleted = manager.rotate_by_policy(APP, max_bytes=25, tag=SCHEDULED_TAG)

        assert deleted == 2
        assert _remaining(manager) == {
            manual.id,
            pre_deploy.id,
            scheduled[2].id,
            scheduled[3].id,
        }

    def test_the_newest_stays_even_when_it_alone_is_over_the_budget(
        self, manager: BackupManager
    ) -> None:
        old = sized(manager, 3, [SCHEDULED_TAG], 10)
        new = sized(manager, 4, [SCHEDULED_TAG], 900)

        manager.rotate_by_policy(APP, max_bytes=5, tag=SCHEDULED_TAG)

        assert _remaining(manager) == {new.id} and old.id not in _remaining(manager)

    def test_a_protected_backup_is_never_deleted_whatever_its_size(
        self, manager: BackupManager
    ) -> None:
        oldest = sized(manager, 3, [SCHEDULED_TAG], 900)
        newest = sized(manager, 4, [SCHEDULED_TAG], 10)

        manager.rotate_by_policy(APP, max_bytes=5, tag=SCHEDULED_TAG, protect=[oldest.id])

        assert _remaining(manager) == {oldest.id, newest.id}

    def test_no_size_changes_nothing_from_before(self, manager: BackupManager) -> None:
        for day in (3, 4, 5):
            sized(manager, day, [SCHEDULED_TAG], 10)

        assert manager.rotate_by_policy(APP, max_count=10, tag=SCHEDULED_TAG) == 0

    def test_a_schedule_with_only_a_size_still_rotates_after_a_new_backup(
        self, manager: BackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}
        monkeypatch.setattr(
            BackupManager, "rotate_by_policy", lambda self, *a, **k: seen.update(k) or 0
        )
        app_dir = manager.backup_dir.parent / "apps" / APP
        app_dir.mkdir(parents=True)
        (app_dir / "index.html").write_text("hello")
        monkeypatch.setattr(manager, "_app_path", lambda domain, name: app_dir)

        manager.create(DOMAIN, retention_bytes=1234, retention_tag=SCHEDULED_TAG)

        assert seen["max_bytes"] == 1234 and seen["tag"] == SCHEDULED_TAG


class TestOnADestination:
    def entries(self, sizes: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {
                "Name": f"{backup_id}.tar.gz",
                "Size": size,
                "ModTime": f"2026-01-{index + 1:02d}T00:00:00Z",
            }
            for index, (backup_id, size) in enumerate(sizes.items())
        ]

    def test_this_servers_newest_that_fit_stay_and_a_foreign_one_is_left_alone(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        from noust.managers.backup_manager import server_id

        destinations.add("nas", "sftp", _sftp_fields())
        target = "nas:noust-backups/shop-example-com"
        ids = [f"{APP}_2026010{day}_000000" for day in (1, 2, 3, 4)]
        foreign = f"{APP}_20260105_000000"
        sidecars = "\n".join(
            json.dumps({"id": backup_id, "origin": server_id()}) for backup_id in ids
        ) + json.dumps({"id": foreign, "origin": "0123456789abcdef"})
        runner.script(("rclone", "cat"), stdout=sidecars)

        deleted = destinations._apply_remote_retention(
            destinations.remote_env("nas"),
            target,
            self.entries({**dict.fromkeys(ids, 10), foreign: 10_000}),
            None,
            None,
            [],
            retention_bytes=25,
        )

        assert sorted(deleted) == sorted(ids[:2])

    def test_the_newest_of_this_server_stays_even_when_it_is_older_than_the_age_limit(
        self, destinations: BackupDestinationManager, runner: FakeRunner
    ) -> None:
        # A limit by age must not leave an application with nothing on the destination.
        from noust.managers.backup_manager import server_id

        destinations.add("nas", "sftp", _sftp_fields())
        only = f"{APP}_20200101_000000"
        runner.script(("rclone", "cat"), stdout=json.dumps({"id": only, "origin": server_id()}))

        deleted = destinations._apply_remote_retention(
            destinations.remote_env("nas"),
            "nas:noust-backups/shop-example-com",
            self.entries({only: 10}),
            None,
            1,
            [],
        )

        assert deleted == []

    def test_push_passes_the_size_as_bytes(
        self,
        destinations: BackupDestinationManager,
        manager: BackupManager,
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        metadata = _push_setup(destinations, manager, day=3)
        runner.script(("rclone", "lsjson", "--hash", TARGET), stdout=_listing(manager, metadata))
        seen: dict[str, Any] = {}
        monkeypatch.setattr(
            destinations,
            "_apply_remote_retention",
            lambda *args, **kwargs: seen.update(kwargs) or [],
        )

        destinations.push(metadata, "nas", retention_gb=2.5, backup_manager=manager)

        assert seen["retention_bytes"] == int(2.5 * GB)


class TestTheSchedule:
    def test_it_is_stored_and_read_back_with_the_size(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        scheduler = BackupScheduler(verbose=False, runner=runner)

        scheduler.create_schedule(
            BackupSchedule(
                domain=DOMAIN,
                app_name=APP,
                schedule="daily",
                retention_bytes=int(2.5 * GB),
                destinations=[{"name": "nas", "retention_gb": 4.0}],
            )
        )

        record = get_store().get_backup_schedule(DOMAIN)
        assert record is not None and record.retention_bytes == int(2.5 * GB)
        assert record.destinations == [{"name": "nas", "retention_gb": 4.0}]
        runner.script(("systemctl", "is-enabled"), stdout="enabled")
        read = scheduler.get_schedule(DOMAIN)
        assert read is not None and read.retention_bytes == int(2.5 * GB)

    def test_saving_it_again_without_a_size_clears_it(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        scheduler = BackupScheduler(verbose=False, runner=runner)
        scheduler.create_schedule(
            BackupSchedule(domain=DOMAIN, app_name=APP, schedule="daily", retention_bytes=GB)
        )
        scheduler.create_schedule(BackupSchedule(domain=DOMAIN, app_name=APP, schedule="daily"))

        record = get_store().get_backup_schedule(DOMAIN)
        assert record is not None and record.retention_bytes is None

    def test_a_run_gives_the_backup_its_budget_and_each_destination_its_own(
        self, runner: FakeRunner, systemd_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers.backup_manager import BackupMetadata
        from noust.managers.backup_scheduler import run_schedule

        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain=DOMAIN,
                schedule="daily",
                retention_bytes=GB,
                destinations=[{"name": "nas", "retention_gb": 7.0}],
            )
        )
        made: dict[str, Any] = {}
        metadata = BackupMetadata.from_dict(
            {
                "id": f"{APP}_20260101_000000",
                "domain": DOMAIN,
                "app_name": APP,
                "created_at": "2026-01-01T00:00:00",
            }
        )

        def create(self: BackupManager, **kwargs: Any) -> BackupMetadata:
            made.update(kwargs)
            return metadata

        pushed: dict[str, Any] = {}
        monkeypatch.setattr(BackupManager, "create", create)
        monkeypatch.setattr(
            BackupDestinationManager,
            "push",
            lambda self, backup, name, **kwargs: pushed.update(kwargs) or {"destination": name},
        )
        monkeypatch.setattr(
            "noust.managers.backup_scheduler._verify_when_due", lambda *a, **k: None
        )

        run_schedule(DOMAIN, runner=runner)

        assert made["retention_bytes"] == GB
        assert pushed["retention_gb"] == 7.0


class TestTheCommandLine:
    @pytest.fixture(autouse=True)
    def _rclone(self, runner: FakeRunner) -> None:
        runner.only_knows("rclone")

    def invoke(self, argv: list[str]):  # type: ignore[no-untyped-def]
        return CliRunner().invoke(cli, argv, obj=Context())

    def test_a_destination_reads_a_fourth_part_as_gigabytes(self) -> None:
        assert _parse_destination_spec("nas:7:30:50") == {
            "name": "nas",
            "retention_count": 7,
            "retention_days": 30,
            "retention_gb": 50.0,
        }
        assert _parse_destination_spec("nas:7")["retention_gb"] is None

    def test_a_size_that_is_not_a_number_is_a_usage_error(self) -> None:
        import click

        with pytest.raises(click.BadParameter):
            _parse_destination_spec("nas:7:30:lots")

    def test_create_stores_the_size_and_update_keeps_it_unless_told(
        self, systemd_dir: Path
    ) -> None:
        created = self.invoke(
            ["backup", "schedule", "create", DOMAIN, "--retention-gb", "2.5"],
        )
        assert created.exit_code == 0, created.output
        assert "2.5 GB" in created.output
        record = get_store().get_backup_schedule(DOMAIN)
        assert record is not None and record.retention_bytes == int(2.5 * GB)

        kept = self.invoke(["backup", "schedule", "update", DOMAIN])
        assert kept.exit_code == 0, kept.output
        record = get_store().get_backup_schedule(DOMAIN)
        assert record is not None and record.retention_bytes == int(2.5 * GB)

        changed = self.invoke(["backup", "schedule", "update", DOMAIN, "--retention-gb", "none"])
        assert changed.exit_code == 0, changed.output
        record = get_store().get_backup_schedule(DOMAIN)
        assert record is not None and record.retention_bytes is None
