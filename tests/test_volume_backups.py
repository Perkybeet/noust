# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Backups of an application's data: persistent paths and Docker volumes (3.4, checklist H-23).

What is pinned here is what an operator learns on the worst day:

- the data an application declares is found, not configured, and one archive
  is taken per target, 0600, with a signed sidecar and a digest;
- a SQLite database inside the data is snapshotted whole while it is written
  to, a link that leaves the data and a socket are left out and said so;
- ``stop`` and ``pause`` still the application only for the copy and always
  let it go, even when the copy fails;
- a schedule's retention deletes only what a schedule made, per target, and
  never the newest; a manual backup and a safety copy are never touched;
- a restore replaces the data (not merges), keeps a safety copy, passes the
  application's health gate and puts the previous data back when it does not;
- a backup is checked before it is sent, and a destination that fails does not
  stop the others.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import tarfile
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import BackupError, ValidationError
from noust.core.runner import set_runner
from noust.core.store import App, NoustStore, get_store
from noust.managers.backup_destination_files import DestinationFileManager
from noust.managers.backup_failures import BackupFailureLog
from noust.managers.backup_manager import BackupManager
from noust.managers.volume_backups import SAFETY_TAG, VolumeBackupManager
from tests.database_backup_support import RcloneRunner, age
from tests.notifications_support import failure_id_of

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


def members(archive: Path) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(archive, "r:gz") as tar:
        return {member.name: member for member in tar.getmembers()}


# ============================================================== the targets


class TestTargets:
    def test_the_declared_paths_are_found_under_shared(
        self, manager: VolumeBackupManager, app_dir: Path
    ) -> None:
        found = manager.targets(DOMAIN)

        assert [t.key for t in found] == ["path:uploads", "path:db"]
        assert found[0].host_path == app_dir / "shared" / "uploads"
        assert all(t.exists for t in found)
        assert found[0].slug == "path-uploads"

    def test_a_declared_path_that_is_not_there_yet_says_so(
        self, manager: VolumeBackupManager, app_dir: Path
    ) -> None:
        get_store().update_app(
            App(
                **{**get_store().get_app(DOMAIN).__dict__, "persistent_paths": ["uploads", "later"]}
            )
        )

        found = {t.key: t for t in manager.targets(DOMAIN)}

        assert found["path:later"].exists is False

    def test_a_policy_can_add_a_data_directory_and_it_cannot_climb(
        self, manager: VolumeBackupManager
    ) -> None:
        assert "path:storage/app" in [
            t.key for t in manager.targets(DOMAIN, extra_paths=["storage/app"])
        ]
        for bad in ("../etc", "/etc", "", "a/../../b"):
            with pytest.raises(ValidationError):
                manager.targets(DOMAIN, extra_paths=[bad])

    def test_an_in_place_application_has_only_what_the_policy_adds(
        self, manager: VolumeBackupManager, tmp_path: Path
    ) -> None:
        inplace = tmp_path / "apps" / "old-example-com"
        (inplace / "data").mkdir(parents=True)
        get_store().create_app(
            App(
                domain="old.example.com", app_type="python", app_path=str(inplace), layout="inplace"
            )
        )

        assert manager.targets("old.example.com") == []
        (target,) = manager.targets("old.example.com", extra_paths=["data"])
        assert target.host_path == inplace / "data"

    def test_a_compose_stack_has_its_named_volumes(
        self,
        manager: VolumeBackupManager,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        get_store().create_app(
            App(
                domain="stack.example.com",
                app_type="docker-compose",
                app_path=str(tmp_path / "apps" / "stack-example-com"),
                layout="inplace",
            )
        )
        monkeypatch.setattr(
            BackupManager, "docker_volumes", lambda self, domain: ["stack_pgdata", "bad name!"]
        )

        found = manager.targets("stack.example.com")

        assert [(t.key, t.kind) for t in found] == [("volume:stack_pgdata", "docker")]
        assert found[0].slug == "vol-stack_pgdata"

    def test_naming_a_target_the_application_does_not_have_lists_the_ones_it_has(
        self, manager: VolumeBackupManager
    ) -> None:
        with pytest.raises(ValidationError) as failure:
            manager.create(DOMAIN, targets=["path:nope"])

        assert "path:uploads" in failure.value.details


# ================================================================= the backup


class TestCreate:
    def test_one_archive_per_target_owner_only_with_a_signed_sidecar(
        self,
        manager: VolumeBackupManager,
        tmp_path: Path,
        audited: list[dict[str, Any]],
    ) -> None:
        taken = manager.create(DOMAIN)

        assert [t.target for t in taken] == ["path:uploads", "path:db"]
        first = taken[0]
        archive = manager.archive_path(first)
        assert first.id.startswith(f"{APP}.path-uploads_")
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600
        sidecar = archive.with_name(f"{first.id}.json")
        assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
        data = json.loads(sidecar.read_text())
        assert data["checksum"] == first.checksum and len(first.checksum or "") == 64
        assert data["mac"] and data["mac_key_id"]
        assert set(members(archive)) >= {"a.txt", "sub", "sub/b.txt"}
        assert [e["event"] for e in audited] == ["volume_backup.create"] * 2

    def test_a_sqlite_database_is_whole_with_what_is_still_in_its_wal(
        self, manager: VolumeBackupManager, tmp_path: Path
    ) -> None:
        taken = manager.create(DOMAIN, targets=["path:db"])

        (entry,) = taken
        assert entry.sqlite_files == ["app.sqlite3"]
        restored = tmp_path / "check"
        restored.mkdir()
        with tarfile.open(manager.archive_path(entry)) as tar:
            tar.extractall(restored, filter="data")
        rows = (
            sqlite3.connect(restored / "app.sqlite3").execute("SELECT text FROM notes").fetchall()
        )
        assert sorted(rows) == [("in the wal",), ("kept",)]
        # The WAL and its index travel inside the snapshot, not beside it.
        assert not [n for n in members(manager.archive_path(entry)) if n.endswith(("-wal", "-shm"))]

    def test_a_link_out_of_the_data_and_a_socket_are_left_out_and_said_so(
        self, manager: VolumeBackupManager, app_dir: Path
    ) -> None:
        uploads = app_dir / "shared" / "uploads"
        (uploads / "inside").symlink_to("a.txt")
        (uploads / "out").symlink_to("/etc/passwd")
        (uploads / "up").symlink_to("../db")
        os.mkfifo(uploads / "sock")
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])

        names = set(members(manager.archive_path(entry)))
        assert "inside" in names
        assert not names & {"out", "up", "sock"}
        assert {s["path"] for s in entry.skipped} == {"out", "up", "sock"}

    def test_a_single_file_is_a_target_too(
        self, manager: VolumeBackupManager, app_dir: Path
    ) -> None:
        (app_dir / "shared" / "license.key").write_text("KEY")
        (manager.store.get_app(DOMAIN))
        get_store().update_app(
            App(
                **{
                    **get_store().get_app(DOMAIN).__dict__,
                    "persistent_paths": ["uploads", "db", "license.key"],
                }
            )
        )

        (entry,) = manager.create(DOMAIN, targets=["path:license.key"])

        assert entry.is_file is True
        assert list(members(manager.archive_path(entry))) == ["license.key"]

    def test_the_owner_of_the_data_root_is_recorded_for_the_restore(
        self, manager: VolumeBackupManager
    ) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])

        assert entry.owner == {"uid": os.getuid(), "gid": os.getgid()}

    def test_a_missing_target_is_skipped_and_all_missing_is_an_error(
        self, manager: VolumeBackupManager, app_dir: Path
    ) -> None:
        get_store().update_app(
            App(
                **{**get_store().get_app(DOMAIN).__dict__, "persistent_paths": ["uploads", "later"]}
            )
        )

        taken = manager.create(DOMAIN)

        assert [t.target for t in taken] == ["path:uploads"]
        with pytest.raises(BackupError, match="no data to back up"):
            manager.create(DOMAIN, targets=["path:later"])

    def test_an_unknown_consistency_is_refused(self, manager: VolumeBackupManager) -> None:
        with pytest.raises(ValidationError, match="consistency"):
            manager.create(DOMAIN, consistency="freeze")

    def test_a_rehearsal_takes_nothing(self, manager: VolumeBackupManager, tmp_path: Path) -> None:
        from noust.core.fs import DryRunFileSystem

        manager._fs = DryRunFileSystem()

        assert manager.create(DOMAIN) == []
        assert not (tmp_path / "backups").exists()

    def test_a_docker_volume_is_tarred_by_a_throw_away_container(
        self,
        manager: VolumeBackupManager,
        runner: RcloneRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        get_store().create_app(
            App(
                domain="stack.example.com",
                app_type="docker-compose",
                app_path=str(tmp_path / "apps" / "stack-example-com"),
                layout="inplace",
            )
        )
        monkeypatch.setattr(BackupManager, "docker_volumes", lambda self, domain: ["stack_pgdata"])

        def tar_it(argv: list[str]) -> None:
            # What the container does: an archive appears in the mounted directory.
            directory = Path(argv[argv.index("-v", argv.index("-v") + 1) + 1].split(":")[0])
            (directory / argv[argv.index("czf") + 1].rsplit("/", 1)[1]).write_bytes(b"archive")

        original = runner.run

        def run(argv, **kwargs):
            if argv[:3] == ["docker", "run", "--rm"]:
                tar_it(list(argv))
            return original(argv, **kwargs)

        monkeypatch.setattr(runner, "run", run)

        (entry,) = manager.create("stack.example.com", targets=["volume:stack_pgdata"])

        docker = next(c for c in runner.calls if c[0] == "docker")
        assert docker[:6] == ("docker", "run", "--rm", "-v", "stack_pgdata:/data:ro", "-v")
        assert docker[-5:] == ("czf", f"/backup/{entry.id}.tar.gz", "-C", "/data", ".")
        assert entry.kind == "docker" and entry.size_bytes == len(b"archive")


# =========================================================== staying still


class TestConsistency:
    def test_stop_stops_the_application_for_the_copy_and_starts_it_again(
        self, manager: VolumeBackupManager, services: FakeServices
    ) -> None:
        manager.create(DOMAIN, consistency="stop")

        # One window for every target, not one per target.
        assert services.calls == [("stop", APP), ("start", APP)]

    def test_a_stopped_application_stays_stopped(
        self, manager: VolumeBackupManager, services: FakeServices
    ) -> None:
        services.active.clear()

        manager.create(DOMAIN, consistency="stop")

        assert services.calls == []

    def test_the_application_is_started_even_when_the_copy_fails(
        self,
        manager: VolumeBackupManager,
        services: FakeServices,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def broken(*args: Any, **kwargs: Any) -> Any:
            raise BackupError("disk full")

        monkeypatch.setattr(manager, "_write_path_archive", broken)

        with pytest.raises(BackupError, match="disk full"):
            manager.create(DOMAIN, consistency="stop")

        assert services.calls == [("stop", APP), ("start", APP)]

    def test_a_start_that_fails_is_an_error_naming_the_fix(
        self, manager: VolumeBackupManager, services: FakeServices
    ) -> None:
        services.fail_start = True

        with pytest.raises(BackupError) as failure:
            manager.create(DOMAIN, consistency="stop")

        assert "could not be started again" in str(failure.value)
        assert f"noust start {DOMAIN}" in failure.value.details

    def test_pause_freezes_the_unit_and_thaws_it(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        manager.create(DOMAIN, consistency="pause")

        signals = [c for c in runner.calls if c[0] == "systemctl"]
        assert signals == [
            ("systemctl", "freeze", f"{APP}.service"),
            ("systemctl", "thaw", f"{APP}.service"),
        ]

    def test_pause_of_a_compose_stack_pauses_its_containers_and_unpauses_them(
        self,
        manager: VolumeBackupManager,
        runner: RcloneRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        runner.script(("docker", "ps", "-q"), stdout="abc123\ndef456\n")
        app = get_store().create_app(
            App(
                domain="stack.example.com",
                app_type="docker-compose",
                app_path=str(tmp_path / "apps" / "stack-example-com"),
                layout="inplace",
                compose_project="stack",
            )
        )
        (tmp_path / "apps" / "stack-example-com" / "data").mkdir(parents=True)

        manager.create(app.domain, extra_paths=["data"], consistency="pause")

        assert ("docker", "pause", "abc123", "def456") in runner.calls
        assert ("docker", "unpause", "abc123", "def456") in runner.calls
        listed = next(c for c in runner.calls if c[:3] == ("docker", "ps", "-q"))
        assert "label=com.docker.compose.project=stack" in listed

    def test_a_freeze_that_fails_stops_the_backup_before_it_copies(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        runner.script(("systemctl", "freeze"), stderr="not supported", exit_code=1)

        with pytest.raises(BackupError, match="freeze") as failure:
            manager.create(DOMAIN, consistency="pause")

        assert "not supported" in failure.value.details
        assert manager.list_backups(DOMAIN) == []


# ================================================================ verifying


class TestVerify:
    def test_a_good_backup_verifies_and_the_verdict_is_kept(
        self, manager: VolumeBackupManager
    ) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])

        verdict = manager.verify(entry.id, deep=True)

        assert verdict["valid"] is True
        assert verdict["mac"] == "valid"
        assert manager.get_backup(entry.id).verified_ok is True  # type: ignore[union-attr]

    def test_a_changed_archive_fails_its_digest(self, manager: VolumeBackupManager) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        archive = manager.archive_path(entry)
        archive.write_bytes(archive.read_bytes() + b"tampered")

        verdict = manager.verify(entry.id)

        assert verdict["valid"] is False
        assert "SHA-256" in verdict["errors"][0]

    def test_a_changed_sidecar_fails_its_mac(self, manager: VolumeBackupManager) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        sidecar = manager.archive_path(entry).with_name(f"{entry.id}.json")
        data = json.loads(sidecar.read_text())
        data["description"] = "changed by hand"
        sidecar.write_text(json.dumps(data))

        verdict = manager.verify(entry.id)

        assert verdict["valid"] is False
        assert "MAC" in verdict["errors"][0]

    def test_a_truncated_archive_does_not_read_through(self, manager: VolumeBackupManager) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        archive = manager.archive_path(entry)
        data = archive.read_bytes()
        archive.write_bytes(data[: len(data) // 2])

        verdict = manager.verify(entry.id)

        assert verdict["valid"] is False

    def test_an_unknown_id_says_how_to_list(self, manager: VolumeBackupManager) -> None:
        for bad in ("nope", "../../etc/passwd", f"{APP}.path-x_20260101_000000"):
            with pytest.raises(BackupError, match="not found"):
                manager.verify(bad)


# ================================================================ retention


class TestRetention:
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


# ================================================================ restoring


class TestRestore:
    def test_the_data_is_replaced_not_merged_and_the_application_is_started_through_its_gate(
        self,
        manager: VolumeBackupManager,
        services: FakeServices,
        app_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        uploads = app_dir / "shared" / "uploads"
        (uploads / "a.txt").write_text("changed since")
        (uploads / "new.txt").write_text("written after the backup")
        services.calls.clear()
        gates: list[str] = []
        monkeypatch.setattr(manager, "_gate", lambda app: (gates.append(app.domain) or True, ""))

        result = manager.restore(entry.id)

        assert (uploads / "a.txt").read_text() == "alpha"
        assert (uploads / "sub" / "b.txt").read_text() == "beta"
        assert not (uploads / "new.txt").exists()
        assert result["started"] is True and result["gate"] == "passed"
        assert gates == [DOMAIN]
        assert services.calls[0] == ("stop", APP)

    def test_a_safety_copy_of_what_was_there_is_taken_and_kept(
        self, manager: VolumeBackupManager, app_dir: Path
    ) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        (app_dir / "shared" / "uploads" / "new.txt").write_text("precious")

        result = manager.restore(entry.id)

        safety = manager.get_backup(result["safety_backup"])
        assert safety is not None and SAFETY_TAG in safety.tags and not safety.scheduled
        assert "new.txt" in members(manager.archive_path(safety))

    def test_the_restore_is_on_the_audit_trail(
        self, manager: VolumeBackupManager, audited: list[dict[str, Any]]
    ) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])

        manager.restore(entry.id)

        event = next(e for e in audited if e["event"] == "volume_backup.restore")
        assert event["details"]["id"] == entry.id and event["details"]["gate"] == "passed"

    def test_ownership_is_given_back_to_who_owned_the_data(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])

        manager.restore(entry.id)

        chown = next(c for c in runner.calls if c[0] == "chown")
        assert chown[:2] == ("chown", "-R")
        assert chown[2] == f"{os.getuid()}:{os.getgid()}"

    def test_a_file_target_is_restored_as_a_file(
        self, manager: VolumeBackupManager, app_dir: Path
    ) -> None:
        (app_dir / "shared" / "license.key").write_text("KEY")
        get_store().update_app(
            App(
                **{
                    **get_store().get_app(DOMAIN).__dict__,
                    "persistent_paths": ["uploads", "license.key"],
                }
            )
        )
        (entry,) = manager.create(DOMAIN, targets=["path:license.key"])
        (app_dir / "shared" / "license.key").write_text("OTHER")

        manager.restore(entry.id)

        assert (app_dir / "shared" / "license.key").read_text() == "KEY"

    def test_a_failed_gate_puts_the_previous_data_back(
        self,
        manager: VolumeBackupManager,
        services: FakeServices,
        app_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        uploads = app_dir / "shared" / "uploads"
        (uploads / "a.txt").write_text("what it has now")
        monkeypatch.setattr(manager, "_gate", lambda app: (False, "502 from the probe"))

        with pytest.raises(BackupError) as failure:
            manager.restore(entry.id)

        assert "did not pass its health check" in str(failure.value)
        assert "502 from the probe" in failure.value.details
        assert "put back" in failure.value.details
        assert (uploads / "a.txt").read_text() == "what it has now"
        assert services.active == {APP}

    def test_a_damaged_backup_restores_nothing(
        self, manager: VolumeBackupManager, app_dir: Path
    ) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        archive = manager.archive_path(entry)
        archive.write_bytes(archive.read_bytes() + b"x")
        uploads = app_dir / "shared" / "uploads"
        (uploads / "a.txt").write_text("untouched")

        with pytest.raises(BackupError, match="failed its check"):
            manager.restore(entry.id)

        assert (uploads / "a.txt").read_text() == "untouched"

    def test_an_extraction_that_fails_leaves_the_data_as_it_was_and_starts_the_application(
        self,
        manager: VolumeBackupManager,
        services: FakeServices,
        app_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        uploads = app_dir / "shared" / "uploads"
        (uploads / "a.txt").write_text("untouched")
        from noust.core.exceptions import SourceError
        from noust.managers import volume_backups

        def refuse(*args: Any, **kwargs: Any) -> None:
            raise SourceError("unsafe member")

        monkeypatch.setattr(volume_backups, "extract_archive", refuse)

        with pytest.raises(BackupError, match=r"Refusing to restore|Restoring"):
            manager.restore(entry.id)

        assert (uploads / "a.txt").read_text() == "untouched"
        assert services.active == {APP}
        assert not list(uploads.parent.glob(".noust-restore-*"))

    def test_a_docker_volume_is_emptied_then_loaded(
        self,
        manager: VolumeBackupManager,
        runner: RcloneRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        get_store().create_app(
            App(
                domain="stack.example.com",
                app_type="docker-compose",
                app_path=str(tmp_path / "apps" / "stack-example-com"),
                layout="inplace",
            )
        )
        monkeypatch.setattr(BackupManager, "docker_volumes", lambda self, domain: ["stack_pgdata"])
        original = runner.run

        def run(argv, **kwargs):
            if argv[:3] == ["docker", "run", "--rm"] and "czf" in argv:
                directory = Path(argv[argv.index("-v", argv.index("-v") + 1) + 1].split(":")[0])
                with tarfile.open(
                    directory / argv[argv.index("czf") + 1].rsplit("/", 1)[1], "w:gz"
                ):
                    pass
            return original(argv, **kwargs)

        monkeypatch.setattr(runner, "run", run)
        monkeypatch.setattr(manager, "_running_units", lambda app: [])
        (entry,) = manager.create("stack.example.com", targets=["volume:stack_pgdata"])

        manager.restore(entry.id, safety_backup=False)

        restore = next(c for c in runner.calls if "sh" in c and "-c" in c)
        assert "find /data -mindepth 1 -delete && tar xzf" in restore[restore.index("-c") + 1]
        assert restore[-2:] == ("sh", f"{entry.id}.tar.gz")
        assert f"{entry.id}.tar.gz" not in restore[restore.index("-c") + 1]


# ================================================================ policies


class TestPolicy:
    def test_it_is_saved_with_its_timer_and_read_back(
        self, manager: VolumeBackupManager, audited: list[dict[str, Any]]
    ) -> None:
        view = manager.set_policy(
            DOMAIN, schedule="weekly", retention_count=4, retention_days=21, consistency="pause"
        )

        data = view.to_dict()
        assert data["configured"] is True
        assert data["schedule"] == "Mon *-*-* 02:00:00"
        assert (data["retention_count"], data["retention_days"]) == (4, 21)
        assert data["consistency"] == "pause"
        assert data["timer"]["installed"] is True
        assert [e["event"] for e in audited] == ["volume_backup.policy"]

    def test_replacing_it_keeps_what_the_last_run_recorded(
        self, manager: VolumeBackupManager
    ) -> None:
        manager.set_policy(DOMAIN)
        manager.records.record_run(DOMAIN, ok=True, error=None, backup="x")

        manager.set_policy(DOMAIN, schedule="hourly")

        policy = manager.get_policy(DOMAIN).policy
        assert policy is not None and policy.last_status == "ok" and policy.last_backup == "x"

    def test_a_disabled_policy_keeps_its_settings_and_has_no_timer(
        self, manager: VolumeBackupManager
    ) -> None:
        manager.set_policy(DOMAIN)

        view = manager.set_policy(DOMAIN, enabled=False)

        assert view.to_dict()["enabled"] is False
        assert view.timer == {}

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"consistency": "freeze"},
            {"retention_count": 0},
            {"retention_days": 99999},
            {"schedule": "daily\nExecStart=/bin/evil"},
            {"destinations": [{"name": "nowhere"}]},
            {"targets": ["path:nope"]},
            {"extra_paths": ["../x"]},
        ],
    )
    def test_an_unusable_policy_is_refused_and_installs_no_timer(
        self, manager: VolumeBackupManager, kwargs: dict[str, Any]
    ) -> None:
        with pytest.raises((ValidationError, BackupError)):
            manager.set_policy(DOMAIN, **kwargs)

        assert manager.scheduler.installed == {}  # type: ignore[attr-defined]
        assert manager.records.policy(DOMAIN) is None

    def test_an_application_with_nothing_declared_cannot_have_one(
        self, manager: VolumeBackupManager, tmp_path: Path
    ) -> None:
        get_store().create_app(
            App(domain="bare.example.com", app_type="python", app_path=str(tmp_path / "bare"))
        )

        with pytest.raises(ValidationError, match="declares no data"):
            manager.set_policy("bare.example.com")

    def test_removing_it_removes_its_timer_and_leaves_the_backups(
        self, manager: VolumeBackupManager
    ) -> None:
        manager.set_policy(DOMAIN)
        manager.create(DOMAIN, targets=["path:uploads"])

        assert manager.remove_policy(DOMAIN) is True

        assert manager.scheduler.installed == {}  # type: ignore[attr-defined]
        assert len(manager.list_backups(DOMAIN)) == 1
        assert manager.remove_policy(DOMAIN) is False


# ============================================================ destinations


def add_destination(runner: RcloneRunner, name: str = "nas", *, encrypted: bool = False) -> None:
    DestinationFileManager(runner=runner).add(
        name,
        "sftp",
        {"host": "nas.example.com", "user": "noust", "pass": "s3cr3t"},
        encrypted=encrypted,
    )


class TestSending:
    def test_a_backup_is_sent_verified_and_recorded(
        self, manager: VolumeBackupManager, runner: RcloneRunner, tmp_path: Path
    ) -> None:
        add_destination(runner)
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])

        summary = manager.push(entry.id, "nas", scheduled=True)

        remote = tmp_path / "remote" / "nas" / "wasm-backups" / "volumes" / APP / "path-uploads"
        assert (remote / f"{entry.id}.tar.gz").is_file()
        assert summary["verified_by"]
        sidecar = json.loads((remote / f"{entry.id}.tar.gz.json").read_text())
        assert sidecar["scheduled"] is True and sidecar["sha256"] == entry.checksum
        assert sidecar["domain"] == DOMAIN and sidecar["target"] == "path:uploads"
        copies = manager.get_backup(entry.id).remote_copies  # type: ignore[union-attr]
        assert copies[0]["destination"] == "nas"

    def test_a_backup_that_fails_its_check_is_not_sent(
        self, manager: VolumeBackupManager, runner: RcloneRunner, tmp_path: Path
    ) -> None:
        add_destination(runner)
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        archive = manager.archive_path(entry)
        archive.write_bytes(archive.read_bytes() + b"x")

        with pytest.raises(BackupError, match="not sent"):
            manager.push(entry.id, "nas")

        assert not (tmp_path / "remote" / "nas").exists() or not list(
            (tmp_path / "remote" / "nas").rglob("*.tar.gz")
        )

    def test_a_destination_that_does_not_exist_is_a_validation_error(
        self, manager: VolumeBackupManager
    ) -> None:
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])

        with pytest.raises(ValidationError, match="does not exist"):
            manager.push(entry.id, "nowhere")

    def test_the_remote_listing_is_by_target(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_destination(runner)
        for entry in manager.create(DOMAIN):
            manager.push(entry.id, "nas")

        listing = manager.remote_backups("nas", DOMAIN)

        assert set(listing) == {"path-uploads", "path-db"}
        assert listing["path-uploads"][0]["target"] == "path:uploads"

    def test_a_backup_on_a_destination_restores_on_a_server_that_does_not_have_it(
        self, manager: VolumeBackupManager, runner: RcloneRunner, app_dir: Path
    ) -> None:
        add_destination(runner)
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        manager.push(entry.id, "nas")
        manager.delete(entry.id)
        uploads = app_dir / "shared" / "uploads"
        (uploads / "a.txt").write_text("lost")

        result = manager.restore_remote("nas", DOMAIN, "path-uploads", f"{entry.id}.tar.gz")

        assert (uploads / "a.txt").read_text() == "alpha"
        assert result["target"] == "path:uploads"

    def test_a_download_that_does_not_match_its_digest_restores_nothing(
        self, manager: VolumeBackupManager, runner: RcloneRunner, tmp_path: Path, app_dir: Path
    ) -> None:
        add_destination(runner)
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        manager.push(entry.id, "nas")
        remote = tmp_path / "remote" / "nas" / "wasm-backups" / "volumes" / APP / "path-uploads"
        (remote / f"{entry.id}.tar.gz").write_bytes(b"swapped by someone")
        uploads = app_dir / "shared" / "uploads"
        (uploads / "a.txt").write_text("untouched")

        with pytest.raises(BackupError, match="Checksum mismatch"):
            manager.restore_remote("nas", DOMAIN, "path-uploads", f"{entry.id}.tar.gz")

        assert (uploads / "a.txt").read_text() == "untouched"

    def test_data_of_another_application_is_refused(
        self, manager: VolumeBackupManager, runner: RcloneRunner, tmp_path: Path
    ) -> None:
        add_destination(runner)
        (entry,) = manager.create(DOMAIN, targets=["path:uploads"])
        manager.push(entry.id, "nas")
        # The same folder name under another application's directory.
        base = tmp_path / "remote" / "nas" / "wasm-backups" / "volumes"
        (base / "other-example-com").mkdir()
        (base / APP / "path-uploads").rename(base / "other-example-com" / "path-uploads")
        get_store().create_app(
            App(domain="other.example.com", app_type="python", app_path=str(tmp_path / "other"))
        )

        with pytest.raises(BackupError, match=r"not of other\.example\.com"):
            manager.restore_remote("nas", "other.example.com", "path-uploads", f"{entry.id}.tar.gz")


# =========================================================== the policy run


class TestRunPolicy:
    def test_it_backs_up_sends_and_prunes(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_destination(runner)
        manager.set_policy(
            DOMAIN,
            targets=["path:uploads"],
            retention_count=1,
            retention_days=None,
            destinations=[{"name": "nas", "retention_count": 2}],
        )

        result = manager.run_policy(DOMAIN)

        assert len(result["backups"]) == 1
        (entry,) = manager.list_backups(DOMAIN)
        assert entry.scheduled and entry.remote_copies[0]["destination"] == "nas"
        assert manager.get_policy(DOMAIN).policy.last_status == "ok"  # type: ignore[union-attr]

    def test_a_second_run_prunes_the_first_locally_by_count(
        self, manager: VolumeBackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager.set_policy(DOMAIN, targets=["path:uploads"], retention_count=1, retention_days=None)
        first = manager.run_policy(DOMAIN)["backups"]
        # Backups are named by the second they were taken in.
        for suffix in (".tar.gz", ".json"):
            path = manager.archive_path(manager.get_backup(first[0])).with_name(
                f"{first[0]}{suffix}"
            )  # type: ignore[arg-type]
            age(path, 1)
        sidecar = manager.archive_path(manager.get_backup(first[0])).with_name(f"{first[0]}.json")  # type: ignore[arg-type]
        data = json.loads(sidecar.read_text())
        data["created_at"] = "2020-01-01T00:00:00+00:00"
        sidecar.write_text(json.dumps(data))

        second = manager.run_policy(DOMAIN)

        assert second["local_deleted"] == first
        assert len(manager.list_backups(DOMAIN)) == 1

    def test_one_destination_failing_does_not_stop_the_others_and_the_run_fails(
        self, manager: VolumeBackupManager, runner: RcloneRunner, tmp_path: Path
    ) -> None:
        add_destination(runner, "nas")
        add_destination(runner, "cloud")
        runner.refusing["nas"] = "connection refused"
        manager.set_policy(
            DOMAIN,
            targets=["path:uploads"],
            destinations=[{"name": "nas"}, {"name": "cloud"}],
        )

        with pytest.raises(BackupError) as failure:
            manager.run_policy(DOMAIN)

        assert "nas" in str(failure.value) and "connection refused" in failure.value.details
        (entry,) = manager.list_backups(DOMAIN)
        assert [c["destination"] for c in entry.remote_copies] == ["cloud"]
        policy = manager.get_policy(DOMAIN).policy
        assert policy is not None and policy.last_status == "failed"
        assert "connection refused" in (policy.last_error or "")

    def test_the_notices_of_a_failed_run_link_to_all_that_was_said(
        self,
        manager: VolumeBackupManager,
        runner: RcloneRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        add_destination(runner, "nas")
        runner.refusing["nas"] = "connection refused " * 100
        manager.set_policy(DOMAIN, targets=["path:uploads"], destinations=[{"name": "nas"}])
        sent: list[Any] = []
        monkeypatch.setattr(
            "noust.core.notifier.Notifier.notify",
            lambda self, notification: sent.append(notification),
        )

        with pytest.raises(BackupError):
            manager.run_policy(DOMAIN, notify=True)

        (notice,) = [n for n in sent if n.code == "backup.upload_failed"]
        failure_id = failure_id_of(notice)
        stored = BackupFailureLog().get(failure_id)
        assert stored is not None and (stored.kind, stored.subject) == ("upload", DOMAIN)
        assert stored.output.count("connection refused") == 100

    def test_a_run_that_could_not_take_the_data_links_to_its_report(
        self, manager: VolumeBackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager.set_policy(DOMAIN, targets=["path:uploads"])
        sent: list[Any] = []
        monkeypatch.setattr(
            "noust.core.notifier.Notifier.notify",
            lambda self, notification: sent.append(notification),
        )

        def broken(*args: Any, **kwargs: Any) -> Any:
            raise BackupError("disk full", details="tar: write error")

        monkeypatch.setattr(manager, "_write_path_archive", broken)

        with pytest.raises(BackupError):
            manager.run_policy(DOMAIN, notify=True)

        (notice,) = [n for n in sent if n.code == "backup.failed"]
        failure_id = failure_id_of(notice)
        stored = BackupFailureLog().get(failure_id)
        assert stored is not None and "tar: write error" in stored.output

    def test_with_no_row_the_data_is_still_backed_up(self, manager: VolumeBackupManager) -> None:
        result = manager.run_policy(DOMAIN)

        assert result["policy_missing"] is True
        assert len(result["backups"]) == 2

    def test_a_backup_that_fails_is_recorded_and_announced(
        self, manager: VolumeBackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager.set_policy(DOMAIN, targets=["path:uploads"])
        sent: list[Any] = []
        monkeypatch.setattr(
            "noust.core.notifier.Notifier.notify",
            lambda self, notification: sent.append(notification),
        )

        def broken(*args: Any, **kwargs: Any) -> Any:
            raise BackupError("disk full")

        monkeypatch.setattr(manager, "_write_path_archive", broken)

        with pytest.raises(BackupError):
            manager.run_policy(DOMAIN, notify=True)

        assert manager.get_policy(DOMAIN).policy.last_status == "failed"  # type: ignore[union-attr]
        assert len(sent) == 1
        assert sent[0].kind == "backup_failed"


class TestRetentionBySize:
    """A target's scheduled backups are kept by size too (checklist H-04)."""

    # The way the retention tests above make a backup of a given age and tag.
    make = TestRetention.make

    def sizes(self, manager: VolumeBackupManager, ids: list[str], size: int) -> None:
        """Make every backup claim the same size."""
        for backup_id in ids:
            entry = manager.get_backup(backup_id)
            assert entry is not None
            sidecar = manager.archive_path(entry).with_name(f"{backup_id}.json")
            data = json.loads(sidecar.read_text())
            data["size_bytes"] = size
            sidecar.write_text(json.dumps(data))

    def test_the_newest_that_fit_stay_per_target(self, manager: VolumeBackupManager) -> None:
        ids = [self.make(manager, "scheduled", days) for days in (5, 4, 3, 2, 1)]
        self.sizes(manager, ids, 10)

        deleted = manager.apply_retention(
            DOMAIN, retention_count=None, retention_days=None, retention_bytes=25
        )

        assert sorted(deleted) == sorted(ids[:3])

    def test_a_manual_backup_is_never_counted_or_deleted(
        self, manager: VolumeBackupManager
    ) -> None:
        manual = self.make(manager, None, 900)
        ids = [self.make(manager, "scheduled", days) for days in (3, 2, 1)]
        self.sizes(manager, [manual, *ids], 10)

        manager.apply_retention(
            DOMAIN, retention_count=None, retention_days=None, retention_bytes=15
        )

        left = {e.id for e in manager.list_backups(DOMAIN)}
        assert manual in left and ids[-1] in left and not left & set(ids[:2])

    def test_the_policy_stores_the_size_and_shows_gigabytes(
        self, manager: VolumeBackupManager
    ) -> None:
        data = manager.set_policy(DOMAIN, retention_gb=2.5).to_dict()

        assert data["retention_gb"] == 2.5 and "retention_bytes" not in data
        policy = manager.records.policy(DOMAIN)
        assert policy is not None and policy.retention_bytes == int(2.5 * 1024**3)
        assert manager.set_policy(DOMAIN).to_dict()["retention_gb"] is None

    @pytest.mark.parametrize("bad", [0, -1, 1_000_001, "5", True])
    def test_a_size_that_is_not_one_installs_no_timer(
        self, manager: VolumeBackupManager, bad: Any
    ) -> None:
        with pytest.raises(ValidationError, match="retention_gb"):
            manager.set_policy(DOMAIN, retention_gb=bad)

        assert manager.scheduler.installed == {}  # type: ignore[attr-defined]

    def test_a_destination_may_have_one_of_its_own(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_destination(runner)

        view = manager.set_policy(
            DOMAIN, destinations=[{"name": "nas", "retention_count": 2, "retention_gb": 40}]
        )

        assert view.to_dict()["destinations"][0]["retention_gb"] == 40.0
