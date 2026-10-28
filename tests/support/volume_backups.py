# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_volume_backups.py`` that other tests build on."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.runner import set_runner
from noust.core.store import App, NoustStore, get_store
from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.volume_backups import SAFETY_TAG, VolumeBackupManager
from tests.support.database_backup_support import RcloneRunner

DOMAIN = "shop.example.com"
APP = "shop-example-com"


class FakeServices:
    """Stands in for the units: records what was stopped, started and asked."""

    def __init__(self, active: tuple[str, ...] = (APP,)) -> None:
        self.active = set(active)
        self.calls: list[tuple[str, str]] = []
        self.fail_start = False

    def serving_units(self, app: App) -> list[str]:
        return [APP]

    def get_status(self, name: str, require_managed: bool = True) -> dict[str, Any]:
        return {"active": name in self.active}

    def stop(self, name: str) -> bool:
        self.calls.append(("stop", name))
        self.active.discard(name)
        return True

    def start(self, name: str) -> None:
        self.calls.append(("start", name))
        if self.fail_start:
            from noust.core.exceptions import ServiceError

            raise ServiceError("Failed to start", details="unit failed")
        self.active.add(name)


class FakeScheduler:
    """Records the timers asked for; touches no unit."""

    def __init__(self) -> None:
        self.installed: dict[str, str] = {}

    def install_volume_timer(self, domain: str, schedule: str, *, options: Any = None) -> str:
        self.installed[domain] = schedule
        return f"noust-backup-vol-{APP}"

    def remove_volume_timer(self, domain: str) -> bool:
        return self.installed.pop(domain, None) is not None

    def volume_timer_state(self, domain: str) -> dict[str, Any]:
        return {"installed": domain in self.installed, "next_run": "soon", "last_run": None}


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    fake = RcloneRunner(tmp_path / "remote")
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def app_dir(tmp_path: Path) -> Path:
    """A release application with an uploads directory and a SQLite database to keep."""
    root = tmp_path / "apps" / APP
    shared = root / "shared"
    (shared / "uploads" / "sub").mkdir(parents=True)
    (shared / "uploads" / "a.txt").write_text("alpha")
    (shared / "uploads" / "sub" / "b.txt").write_text("beta")
    (shared / "db").mkdir()
    (shared / "releases").mkdir(exist_ok=True)
    (root / "releases" / "20260101-000000-abc1234").mkdir(parents=True)
    (root / "current").symlink_to(root / "releases" / "20260101-000000-abc1234")
    database = sqlite3.connect(shared / "db" / "app.sqlite3")
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("CREATE TABLE notes (text TEXT)")
    database.execute("INSERT INTO notes VALUES ('kept')")
    database.commit()
    # Left open and uncheckpointed: the rows are in the WAL, not in the main file.
    database.execute("INSERT INTO notes VALUES ('in the wal')")
    database.commit()
    return root


@pytest.fixture
def app(app_dir: Path) -> App:
    return get_store().create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            app_path=str(app_dir),
            layout="releases",
            persistent_paths=["uploads", "db"],
        )
    )


@pytest.fixture
def services() -> FakeServices:
    return FakeServices()


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "noust.core.audit.record", lambda event, **kwargs: events.append({"event": event, **kwargs})
    )
    return events


@pytest.fixture
def manager(
    tmp_path: Path,
    runner: RcloneRunner,
    app: App,
    services: FakeServices,
    monkeypatch: pytest.MonkeyPatch,
) -> VolumeBackupManager:
    monkeypatch.setattr(
        VolumeBackupManager, "root", property(lambda self: tmp_path / "backups" / "volumes")
    )
    instance = VolumeBackupManager(
        runner=runner,
        scheduler=FakeScheduler(),  # type: ignore[arg-type]
        destinations=DestinationFileManager(runner=runner),
        services=services,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(instance, "_gate", lambda app: (True, ""))
    return instance


class RetentionTests:
    def make(self, manager: VolumeBackupManager, tag: str | None, days: float) -> str:
        (entry,) = manager.create(
            DOMAIN, targets=["path:uploads"], tags=[tag, "auto"] if tag else []
        )
        # The id carries the second it was taken in: move it so each is its own.
        directory = manager.archive_path(entry).parent
        stamp = f"20260101_{len(list(directory.glob('*.json'))):06d}"
        new_id = f"{APP}.path-uploads_{stamp}"
        for suffix in (".tar.gz", ".json"):
            (directory / f"{entry.id}{suffix}").rename(directory / f"{new_id}{suffix}")
        sidecar = directory / f"{new_id}.json"
        data = json.loads(sidecar.read_text())
        data["id"] = new_id
        data["created_at"] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(
            timespec="seconds"
        )
        sidecar.write_text(json.dumps(data))
        return new_id

    def test_a_schedule_keeps_the_newest_n_of_its_own_per_target(
        self, manager: VolumeBackupManager
    ) -> None:
        ids = [self.make(manager, "scheduled", days) for days in (5, 4, 3, 2, 1)]

        deleted = manager.apply_retention(DOMAIN, retention_count=2, retention_days=None)

        assert sorted(deleted) == sorted(ids[:3])
        assert {e.id for e in manager.list_backups(DOMAIN)} == set(ids[3:])

    def test_by_age_and_never_the_newest(self, manager: VolumeBackupManager) -> None:
        old = self.make(manager, "scheduled", 400)
        older = self.make(manager, "scheduled", 500)

        deleted = manager.apply_retention(DOMAIN, retention_count=None, retention_days=30)

        assert deleted == [older]
        assert manager.get_backup(old) is not None

    def test_a_manual_backup_and_a_safety_copy_are_never_deleted(
        self, manager: VolumeBackupManager
    ) -> None:
        manual = self.make(manager, None, 900)
        safety = self.make(manager, SAFETY_TAG, 900)
        scheduled = [self.make(manager, "scheduled", days) for days in (3, 2, 1)]

        manager.apply_retention(DOMAIN, retention_count=1, retention_days=1)

        left = {e.id for e in manager.list_backups(DOMAIN)}
        assert {manual, safety, scheduled[-1]} <= left
        assert not left & set(scheduled[:2])

    def test_each_target_keeps_its_own(self, manager: VolumeBackupManager) -> None:
        manager.create(DOMAIN, tags=["scheduled"])

        deleted = manager.apply_retention(DOMAIN, retention_count=1, retention_days=None)

        assert deleted == []
        assert {e.target for e in manager.list_backups(DOMAIN)} == {"path:uploads", "path:db"}

    def test_no_limits_delete_nothing(self, manager: VolumeBackupManager) -> None:
        self.make(manager, "scheduled", 9)

        assert manager.apply_retention(DOMAIN, retention_count=None, retention_days=None) == []
