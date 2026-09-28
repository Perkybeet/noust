# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the 2.2 backup scheduler: the store row, adoption and run-schedule.

Before 2.2 a schedule was systemd units only, and its retention was written
into the timer's own service unit but never actually applied - the service
ran a bare ``wasm backup create --tags scheduled,auto``. These tests cover
the model this module was rewritten to: the schedule lives in the store,
``run_schedule()`` is what a timer's service now calls, and a timer that
predates schema v10 is adopted - given a store row and a rewritten service
unit - the first time it is listed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.test_notifier import config  # noqa: F401  (pytest resolves fixtures by name)
from wasm.core.config import Config
from wasm.core.exceptions import BackupError
from wasm.core.notifier import NotificationEvent
from wasm.core.runner import FakeRunner
from wasm.core.store import BackupScheduleRecord, WASMStore, get_store
from wasm.managers.backup_destinations import BackupDestinationManager
from wasm.managers.backup_manager import BackupManager, BackupMetadata
from wasm.managers.backup_scheduler import BackupSchedule, BackupScheduler, run_schedule

# The notifier's config fixture is imported rather than replicated, so there
# stays one definition of "a sandboxed configuration".
# ruff: noqa: F811

#: What ``systemctl list-timers --no-legend`` prints for one legacy timer.
LIST_TIMERS_LINE = (
    "Sat 2026-08-15 02:00:00 UTC 5h left "
    "Fri 2026-08-14 02:00:00 UTC 19h ago "
    "wasm-backup-example-com.timer wasm-backup-example-com.service\n"
)

#: What ``systemctl show`` answers about that timer.
SHOW_TIMER_OUTPUT = (
    "Description=WASM backup timer for example.com\n"
    "TimersCalendar={ OnCalendar=*-*-* 02:00:00 ; next_elapse=Sat 2026-08-15 02:00:00 UTC }\n"
    "LastTriggerUSec=Fri 2026-08-14 02:00:00 UTC\n"
    "NextElapseUSecRealtime=Sat 2026-08-15 02:00:00 UTC\n"
)


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    """Force a fresh store singleton per test; see test_backup_destinations.py."""
    WASMStore.reset_instance()
    yield
    WASMStore.reset_instance()


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the scheduler's unit directory into the sandbox."""
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


def _scripted_legacy_timer(runner: FakeRunner) -> None:
    runner.script(("systemctl", "list-timers"), stdout=LIST_TIMERS_LINE)
    runner.script(("systemctl", "show", "wasm-backup-example-com.timer"), stdout=SHOW_TIMER_OUTPUT)


class TestAdoption:
    def test_a_legacy_timer_gets_a_store_row(self, runner: FakeRunner, systemd_dir: Path) -> None:
        _scripted_legacy_timer(runner)
        assert get_store().get_backup_schedule("example.com") is None

        BackupScheduler(verbose=False, runner=runner).list_schedules()

        record = get_store().get_backup_schedule("example.com")
        assert record is not None
        assert record.schedule == "*-*-* 02:00:00"
        assert record.destinations == []

    def test_its_service_unit_is_rewritten_to_run_schedule(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        _scripted_legacy_timer(runner)
        service = systemd_dir / "wasm-backup-example-com.service"
        service.write_text(
            "[Service]\nExecStart=/usr/bin/wasm backup create example.com "
            "--include-databases --tags scheduled,auto\n"
        )

        BackupScheduler(verbose=False, runner=runner).list_schedules()

        assert "wasm backup run-schedule example.com" in service.read_text()
        assert "backup create" not in service.read_text()

    def test_a_schedule_the_store_already_knows_is_left_alone(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        _scripted_legacy_timer(runner)
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="example.com",
                schedule="weekly",
                retention_count=42,
                destinations=[{"name": "nas", "retention_count": None, "retention_days": None}],
            )
        )

        BackupScheduler(verbose=False, runner=runner).list_schedules()

        record = get_store().get_backup_schedule("example.com")
        assert record is not None
        assert record.schedule == "weekly"
        assert record.retention_count == 42
        assert record.destinations == [
            {"name": "nas", "retention_count": None, "retention_days": None}
        ]

    def test_listing_reports_the_stored_retention(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        _scripted_legacy_timer(runner)

        entries = BackupScheduler(verbose=False, runner=runner).list_schedules()

        # Adoption leaves retention at the default of "unset"; a store row
        # created by 'schedule create' would report its own numbers instead.
        assert entries[0]["retention_count"] == ""
        assert entries[0]["retention_days"] == ""


class TestCreateGetRemove:
    def test_create_schedule_writes_the_store_row(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        scheduler = BackupScheduler(verbose=False, runner=runner)
        scheduler.create_schedule(
            BackupSchedule(
                domain="shop.example.com",
                app_name="shop-example-com",
                schedule="daily",
                retention_count=9,
                retention_days=45,
                destinations=[{"name": "nas", "retention_count": 3, "retention_days": None}],
            )
        )

        record = get_store().get_backup_schedule("shop.example.com")
        assert record is not None
        assert record.retention_count == 9
        assert record.retention_days == 45
        assert record.destinations == [
            {"name": "nas", "retention_count": 3, "retention_days": None}
        ]

    def test_create_schedule_again_replaces_the_row(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        scheduler = BackupScheduler(verbose=False, runner=runner)
        base = BackupSchedule(
            domain="shop.example.com", app_name="shop-example-com", schedule="daily"
        )
        scheduler.create_schedule(base)
        scheduler.create_schedule(
            BackupSchedule(
                domain="shop.example.com",
                app_name="shop-example-com",
                schedule="weekly",
                retention_count=2,
            )
        )

        record = get_store().get_backup_schedule("shop.example.com")
        assert record is not None
        assert record.schedule == "Mon *-*-* 02:00:00"
        assert record.retention_count == 2

    def test_get_schedule_merges_the_store_row(self, runner: FakeRunner, systemd_dir: Path) -> None:
        scheduler = BackupScheduler(verbose=False, runner=runner)
        scheduler.create_schedule(
            BackupSchedule(
                domain="shop.example.com",
                app_name="shop-example-com",
                schedule="daily",
                retention_count=3,
                destinations=[{"name": "nas", "retention_count": None, "retention_days": None}],
            )
        )
        runner.script(
            ("systemctl", "is-enabled", "wasm-backup-shop-example-com.timer"), stdout="enabled"
        )

        fetched = scheduler.get_schedule("shop.example.com")

        assert fetched is not None
        assert fetched.retention_count == 3
        assert fetched.destinations == [
            {"name": "nas", "retention_count": None, "retention_days": None}
        ]

    def test_remove_schedule_deletes_the_store_row(
        self, runner: FakeRunner, systemd_dir: Path
    ) -> None:
        scheduler = BackupScheduler(verbose=False, runner=runner)
        scheduler.create_schedule(
            BackupSchedule(domain="shop.example.com", app_name="shop-example-com", schedule="daily")
        )
        assert get_store().get_backup_schedule("shop.example.com") is not None

        scheduler.remove_schedule("shop.example.com")

        assert get_store().get_backup_schedule("shop.example.com") is None


def _metadata(backup_id: str, domain: str) -> BackupMetadata:
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


class TestRunSchedule:
    def test_no_schedule_raises(self) -> None:
        with pytest.raises(BackupError):
            run_schedule("nowhere.example.com")

    def test_creates_a_local_backup_with_the_schedules_retention(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="shop.example.com",
                schedule="daily",
                include_databases=True,
                retention_count=5,
                retention_days=None,
                destinations=[],
            )
        )

        captured: dict[str, object] = {}

        def fake_create(self: BackupManager, **kwargs: object) -> BackupMetadata:
            captured.update(kwargs)
            return _metadata("shop-example-com_20260101_000000", "shop.example.com")

        monkeypatch.setattr(BackupManager, "create", fake_create)

        result = run_schedule("shop.example.com")

        assert captured["retention_count"] == 5
        assert captured["retention_days"] is None
        assert captured["include_databases"] is True
        assert result["backup_id"] == "shop-example-com_20260101_000000"
        assert result["destinations"] == {}

    def test_pushes_to_every_destination_with_its_own_retention(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="shop.example.com",
                schedule="daily",
                destinations=[
                    {"name": "nas", "retention_count": 3, "retention_days": None},
                    {"name": "s3", "retention_count": None, "retention_days": 90},
                ],
            )
        )
        monkeypatch.setattr(
            BackupManager,
            "create",
            lambda self, **kwargs: _metadata(
                "shop-example-com_20260101_000000", "shop.example.com"
            ),
        )

        pushed: dict[str, tuple[int | None, int | None]] = {}

        def fake_push(
            self: BackupDestinationManager,
            backup: BackupMetadata,
            destination_name: str,
            *,
            retention_count: int | None = None,
            retention_days: int | None = None,
            backup_manager: BackupManager | None = None,
        ) -> dict[str, object]:
            pushed[destination_name] = (retention_count, retention_days)
            return {
                "destination": destination_name,
                "backup_id": backup.id,
                "uploaded": [],
                "retention_deleted": [],
            }

        monkeypatch.setattr(BackupDestinationManager, "push", fake_push)

        result = run_schedule("shop.example.com")

        assert pushed == {"nas": (3, None), "s3": (None, 90)}
        assert result["destinations"]["nas"]["ok"] is True
        assert result["destinations"]["s3"]["ok"] is True

    def test_a_failed_destination_still_lets_the_others_run_and_notifies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="shop.example.com",
                schedule="daily",
                destinations=[
                    {"name": "broken", "retention_count": None, "retention_days": None},
                    {"name": "fine", "retention_count": None, "retention_days": None},
                ],
            )
        )
        monkeypatch.setattr(
            BackupManager,
            "create",
            lambda self, **kwargs: _metadata(
                "shop-example-com_20260101_000000", "shop.example.com"
            ),
        )

        def fake_push(self, backup, destination_name, **kwargs):  # type: ignore[no-untyped-def]
            if destination_name == "broken":
                raise BackupError(
                    "Failed to upload to broken",
                    details="rclone: permission denied for user wasm",
                )
            return {"destination": destination_name, "backup_id": backup.id, "uploaded": []}

        monkeypatch.setattr(BackupDestinationManager, "push", fake_push)

        notified: list[NotificationEvent] = []
        monkeypatch.setattr(
            "wasm.core.notifier.Notifier.notify", lambda self, event: notified.append(event)
        )

        with pytest.raises(BackupError) as excinfo:
            run_schedule("shop.example.com")

        assert any(
            event.kind == "backup_failed" and "permission denied for user wasm" in event.body
            for event in notified
        )
        # With no notification channel, the timer's journal has only this.
        assert "broken" in excinfo.value.message
        assert "permission denied for user wasm" in excinfo.value.details

    def test_local_backup_failure_notifies_and_never_pushes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="shop.example.com",
                schedule="daily",
                destinations=[{"name": "nas", "retention_count": None, "retention_days": None}],
            )
        )

        def fake_create(self: BackupManager, **kwargs: object) -> BackupMetadata:
            raise BackupError("Application not found: shop.example.com")

        monkeypatch.setattr(BackupManager, "create", fake_create)

        pushed = []
        monkeypatch.setattr(
            BackupDestinationManager, "push", lambda self, *a, **k: pushed.append(1)
        )

        notified: list[NotificationEvent] = []
        monkeypatch.setattr(
            "wasm.core.notifier.Notifier.notify", lambda self, event: notified.append(event)
        )

        with pytest.raises(BackupError):
            run_schedule("shop.example.com")

        assert pushed == []
        assert any(event.kind == "backup_failed" for event in notified)

    def test_notifications_are_rendered_in_the_configured_language(
        self, monkeypatch: pytest.MonkeyPatch, config: Config
    ) -> None:
        """notifications.language: es translates the title; rclone's stderr stays verbatim."""
        config.set("notifications.language", "es")
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain="shop.example.com",
                schedule="daily",
                destinations=[{"name": "broken", "retention_count": None, "retention_days": None}],
            )
        )
        monkeypatch.setattr(
            BackupManager,
            "create",
            lambda self, **kwargs: _metadata(
                "shop-example-com_20260101_000000", "shop.example.com"
            ),
        )

        def fake_push(self, backup, destination_name, **kwargs):  # type: ignore[no-untyped-def]
            raise BackupError(
                "Failed to upload to broken", details="rclone: permission denied for user wasm"
            )

        monkeypatch.setattr(BackupDestinationManager, "push", fake_push)

        notified: list[NotificationEvent] = []
        monkeypatch.setattr(
            "wasm.core.notifier.Notifier.notify", lambda self, event: notified.append(event)
        )

        with pytest.raises(BackupError):
            run_schedule("shop.example.com")

        assert len(notified) == 1
        assert (
            notified[0].title
            == "No se ha podido subir la copia de seguridad de shop.example.com a broken"
        )
        assert "permission denied for user wasm" in notified[0].body

    def test_the_missing_schedule_notice_is_rendered_in_the_configured_language(
        self, config: Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config.set("notifications.language", "es")
        notified: list[NotificationEvent] = []
        monkeypatch.setattr(
            "wasm.core.notifier.Notifier.notify", lambda self, event: notified.append(event)
        )

        with pytest.raises(BackupError):
            run_schedule("nowhere.example.com")

        assert notified
        assert notified[0].title == (
            "Faltan los ajustes de la copia de seguridad programada: nowhere.example.com"
        )
