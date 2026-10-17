# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A restore drill proves the newest backup comes back, without bringing it back (checklist H-31)."""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

import json
import sqlite3
import tarfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli
from noust.core.exceptions import DatabaseError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.backup_manager import BackupManager
from noust.managers.database.backup_verify import RestoreTest
from noust.managers.restore_drill import (
    DEFAULT_DRILL_DAYS,
    DrillRecords,
    RestoreDrill,
    drill_days,
    drill_due,
)
from tests.test_backup_integrity import (  # noqa: F401
    FakeApp,
    FakeDatabase,
    _use_store,
    apps_dir,
    manager,
)

DOMAIN = "shop.example.com"


@pytest.fixture
def records(tmp_path: Path) -> Iterator[NoustStore]:
    """A real store, for the drills it keeps."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


class Engines:
    """A database service whose engines answer the way the test says."""

    def __init__(self) -> None:
        self.loaded: list[tuple[str, str]] = []
        self.outcome = RestoreTest(True, "noust_verify_ab12cd34", "Loaded (3 tables)", 0.1)
        self.engine_type = "postgresql"
        self.missing = False

    def running(self, engine: str) -> Any:
        if self.missing:
            raise DatabaseError(f"{engine} is not installed")
        return SimpleNamespace(engine_type=self.engine_type, engine=engine)


@pytest.fixture
def engines(monkeypatch: pytest.MonkeyPatch) -> Engines:
    """What loading a dump into a temporary database finds."""
    fake = Engines()

    def load(manager: Any, path: Path) -> RestoreTest:
        fake.loaded.append((manager.engine, path.read_text()))
        return fake.outcome

    monkeypatch.setattr("noust.managers.database.backup_verify.restore_test", load)
    return fake


def drill(manager: BackupManager, engines: Engines, records: NoustStore, **kwargs: Any) -> Any:
    """Run a drill over this test's manager and engines."""
    return RestoreDrill(manager, store=records, database_service=lambda: engines, **kwargs).run(
        DOMAIN
    )


def take(manager: BackupManager, monkeypatch: pytest.MonkeyPatch, *, databases: bool = True) -> Any:
    """Take a real backup, with one database in it."""
    _use_store(
        monkeypatch,
        FakeApp(1, DOMAIN),
        [FakeDatabase("shop", "postgresql")] if databases else [],
    )
    runner: FakeRunner = manager.runner
    runner.script(["pg_dump"], stdout="-- dump of shop\n")
    return manager.create(DOMAIN, include_databases=databases)


class TestADrill:
    def test_a_good_backup_passes_every_check_and_says_what_it_did_not_do(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backup = take(manager, monkeypatch)

        report = drill(manager, engines, records)

        assert report.ok and report.backup_id == backup.id
        assert [step.name for step in report.steps] == [
            "backup",
            "archive",
            "unpack",
            "postgresql/shop",
            "application",
        ]
        assert engines.loaded == [("postgresql", "-- dump of shop\n")]
        last = report.steps[-1]
        assert last.skipped and "was not started" in last.detail

    def test_nothing_of_the_application_is_touched(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
        apps_dir: Path,
    ) -> None:
        take(manager, monkeypatch)
        app = apps_dir / "shop-example-com"
        (app / "src" / "index.js").write_text("changed since the backup\n")
        runner: FakeRunner = manager.runner
        calls_before = len(runner.calls)

        drill(manager, engines, records)

        assert (app / "src" / "index.js").read_text() == "changed since the backup\n"
        assert not any(
            call[0] in ("systemctl", "psql", "nginx") for call in runner.calls[calls_before:]
        )

    def test_the_report_is_kept_with_its_date_and_listed(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        take(manager, monkeypatch)

        report = drill(manager, engines, records)

        [kept] = DrillRecords(records).list(DOMAIN)
        assert kept.id == report.id is not None
        assert kept.ok and kept.started_at and kept.finished_at and kept.trigger == "manual"
        assert [step.name for step in kept.steps] == [step.name for step in report.steps]
        assert DrillRecords(records).last_good(DOMAIN) is not None

    def test_a_database_that_does_not_load_fails_the_drill_naming_it(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        take(manager, monkeypatch)
        engines.outcome = RestoreTest(False, "noust_verify_x", "syntax error at line 3", 0.1)

        report = drill(manager, engines, records)

        assert not report.ok
        assert report.summary == "postgresql/shop: syntax error at line 3"
        assert DrillRecords(records).last_good(DOMAIN) is None

    def test_an_engine_that_is_not_running_is_a_failed_step_not_a_crash(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        take(manager, monkeypatch)
        engines.missing = True

        report = drill(manager, engines, records)

        assert not report.ok and "not installed" in report.summary

    def test_a_redis_snapshot_is_loaded_into_a_second_server_never_the_real_one(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        take(manager, monkeypatch)
        engines.engine_type = "redis"
        tried: list[tuple[str, str, str]] = []

        def load(runner: Any, dump: Path, *, server: str, client: str, **kwargs: Any) -> Any:
            from noust.managers.database.backup_verify import RestoreTest

            tried.append((dump.name, server, client))
            return RestoreTest(True, "", "Loaded into a second server (4 keys)", 0.2)

        monkeypatch.setattr("noust.managers.restore_drill.redis_restore_test", load)

        report = drill(manager, engines, records)

        step = next(step for step in report.steps if step.name == "postgresql/shop")
        assert step.ok and not step.skipped and "4 keys" in step.detail
        assert tried and tried[0][1:] == ("redis-server", "redis-cli")
        assert engines.loaded == []

    def test_a_snapshot_the_second_server_refuses_fails_the_drill(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        take(manager, monkeypatch)
        engines.engine_type = "redis"

        def refuse(runner: Any, dump: Path, **kwargs: Any) -> Any:
            from noust.managers.database.backup_verify import RestoreTest

            return RestoreTest(False, "", "Can't handle RDB format version 12", 0.1)

        monkeypatch.setattr("noust.managers.restore_drill.redis_restore_test", refuse)

        report = drill(manager, engines, records)

        assert not report.ok and "RDB format version 12" in report.summary

    def test_a_backup_with_no_database_checks_the_files_only(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        take(manager, monkeypatch, databases=False)

        report = drill(manager, engines, records)

        assert report.ok
        assert [step.name for step in report.steps] == [
            "backup",
            "archive",
            "unpack",
            "application",
        ]

    def test_a_corrupted_archive_fails_at_the_archive(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backup = take(manager, monkeypatch)
        archive = manager.backup_dir / "shop-example-com" / f"{backup.id}.tar.gz"
        archive.write_bytes(archive.read_bytes()[:-40] + b"x" * 40)

        report = drill(manager, engines, records)

        assert not report.ok
        assert [step.name for step in report.steps if not step.ok] == ["archive"]
        assert engines.loaded == []

    def test_an_application_with_no_backup_is_told_to_take_one(
        self, manager: BackupManager, engines: Engines, records: NoustStore
    ) -> None:
        report = drill(manager, engines, records)

        assert not report.ok
        assert "noust backup create shop.example.com" in report.steps[0].detail

    def test_a_backup_of_another_application_is_not_rehearsed_under_this_name(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backup = take(manager, monkeypatch)

        report = RestoreDrill(manager, store=records, database_service=lambda: engines).run(
            "other.example.com", backup_id=backup.id
        )

        assert not report.ok and "is not one of other.example.com's" in report.steps[0].detail


def keep_orders_in_sqlite(app: Path, *, rows: int = 20, page_size: int | None = None) -> Path:
    """
    Make the application keep its data in a SQLite file of its own.

    Returns:
        The file.
    """
    path = app / "data" / "orders.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        if page_size is not None:
            connection.execute(f"PRAGMA page_size={page_size}")
        connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, total REAL)")
        connection.execute("CREATE INDEX orders_total ON orders (total)")
        connection.executemany(
            "INSERT INTO orders (total) VALUES (?)", [(n * 1.5,) for n in range(rows)]
        )
        connection.commit()
    finally:
        connection.close()
    return path


class TestASqliteDatabaseInTheBackup:
    def test_a_database_that_comes_back_sound_is_a_step_of_its_own(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
        apps_dir: Path,
    ) -> None:
        keep_orders_in_sqlite(apps_dir / "shop-example-com")
        take(manager, monkeypatch)

        report = drill(manager, engines, records)

        names = [step.name for step in report.steps]
        assert report.ok
        assert names[:2] == ["backup", "archive"]
        [sqlite_step] = [step for step in report.steps if step.name.startswith("sqlite/")]
        assert sqlite_step.name.endswith("data/orders.db") and sqlite_step.ok
        assert "quick_check passes" in sqlite_step.detail
        assert names.index(sqlite_step.name) < names.index("postgresql/shop")

    def test_a_copy_sqlite_cannot_read_fails_the_drill_with_its_own_words(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
        apps_dir: Path,
    ) -> None:
        database = keep_orders_in_sqlite(apps_dir / "shop-example-com", rows=3000, page_size=1024)
        with database.open("r+b") as handle:
            handle.seek(1024 * 6)
            handle.write(b"\xff" * 1024 * 3)
        take(manager, monkeypatch)

        report = drill(manager, engines, records)

        assert not report.ok
        [bad] = [step for step in report.steps if step.name.startswith("sqlite/") and not step.ok]
        assert bad.detail and report.summary.startswith("sqlite/")
        assert DrillRecords(records).last_good(DOMAIN) is None

    def test_an_application_that_keeps_none_has_no_such_step(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        take(manager, monkeypatch)

        report = drill(manager, engines, records)

        assert not [step for step in report.steps if step.name.startswith("sqlite/")]


class TestUnpacking:
    def test_the_archive_is_unpacked_with_a_restores_rules_and_removed_afterwards(
        self,
        manager: BackupManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        backup = take(manager, monkeypatch)

        with manager.unpacked(backup.id) as unpacked:
            root = unpacked.root
            assert (root / "shop-example-com" / "src" / "index.js").is_file()
            assert [(engine, name) for engine, name, _ in unpacked.dumps] == [
                ("postgresql", "shop")
            ]
            assert unpacked.dumps[0][2].is_file() and unpacked.stack_dumps == 0
        assert not root.exists()

    def test_an_archive_the_extractor_refuses_is_refused_here_too(
        self,
        manager: BackupManager,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from noust.core.exceptions import BackupError

        backup = take(manager, monkeypatch)
        archive = manager.backup_dir / "shop-example-com" / f"{backup.id}.tar.gz"
        evil = tmp_path / "evil.tar.gz"
        with tarfile.open(archive) as source, tarfile.open(evil, "w:gz") as target:
            for member in source:
                handle = source.extractfile(member)
                target.addfile(member, handle)
            link = tarfile.TarInfo("shop-example-com/escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            target.addfile(link)
        evil.replace(archive)

        with pytest.raises(BackupError, match="cannot be restored"):
            with manager.unpacked(backup.id):
                pass

    def test_an_unknown_backup_is_a_clear_error(self, manager: BackupManager) -> None:
        from noust.core.exceptions import BackupError

        with pytest.raises(BackupError, match="not found"):
            with manager.unpacked("nobody_20260101_000000"):
                pass


@pytest.mark.real_drill
class TestWhenADrillIsDue:
    def test_never_run_is_due(self, records: NoustStore) -> None:
        assert drill_due(DOMAIN, store=records)

    def test_a_recent_good_drill_is_not(self, records: NoustStore) -> None:
        from datetime import datetime, timezone

        from noust.managers.restore_drill import DrillReport, DrillStep

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        DrillRecords(records).save(
            DrillReport(DOMAIN, "b", "manual", now, now, [DrillStep("archive", True, "ok")])
        )

        assert not drill_due(DOMAIN, store=records)

    def test_an_old_one_is_due_again(self, records: NoustStore) -> None:
        from noust.managers.restore_drill import DrillReport, DrillStep

        long_ago = "2026-01-01T00:00:00+00:00"
        DrillRecords(records).save(
            DrillReport(DOMAIN, "b", "manual", long_ago, long_ago, [DrillStep("a", True, "ok")])
        )

        assert drill_due(DOMAIN, store=records)

    def test_a_failed_drill_does_not_count(self, records: NoustStore) -> None:
        from datetime import datetime, timezone

        from noust.managers.restore_drill import DrillReport, DrillStep

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        DrillRecords(records).save(
            DrillReport(DOMAIN, "b", "manual", now, now, [DrillStep("archive", False, "bad")])
        )

        assert drill_due(DOMAIN, store=records)

    def test_zero_days_turns_the_schedule_off(self, records: NoustStore) -> None:
        class Off:
            def get(self, key: str, default: object = None) -> object:
                return 0 if key == "backup.drill_days" else default

        assert drill_days(Off()) == 0  # type: ignore[arg-type]
        assert not drill_due(DOMAIN, store=records, config=Off())  # type: ignore[arg-type]
        assert drill_days() == DEFAULT_DRILL_DAYS


class TestTheCommand:
    def test_it_runs_a_drill_and_exits_1_when_it_fails(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        take(manager, monkeypatch)
        monkeypatch.setattr(
            "noust.cli.commands.backup_drill.RestoreDrill",
            lambda logger=None: RestoreDrill(
                manager, store=records, database_service=lambda: engines, logger=logger
            ),
        )

        good = CliRunner().invoke(cli, ["backup", "drill", DOMAIN, "--json"])
        engines.outcome = RestoreTest(False, "x", "boom", 0.1)
        bad = CliRunner().invoke(cli, ["backup", "drill", DOMAIN])

        assert good.exit_code == 0, good.output
        assert json.loads(good.output)["ok"] is True
        assert bad.exit_code == 1 and "could not be shown to restore" in bad.output

    def test_drills_lists_what_was_kept(self, records: NoustStore) -> None:
        from noust.managers.restore_drill import DrillReport, DrillStep

        DrillRecords(records).save(
            DrillReport(
                DOMAIN,
                "b",
                "schedule",
                "2026-10-01T03:00:00+00:00",
                "2026-10-01T03:01:00+00:00",
                [DrillStep("archive", False, "checksum mismatch")],
            )
        )

        result = CliRunner().invoke(cli, ["backup", "drills", "--json"])

        assert result.exit_code == 0, result.output
        [item] = json.loads(result.output)["items"]
        assert item["ok"] is False and item["trigger"] == "schedule"
        assert "checksum mismatch" in item["summary"]


@pytest.mark.real_drill
class TestAScheduledBackupRunsOne:
    def sent(self, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        said: list[Any] = []
        monkeypatch.setattr(
            "noust.managers.backup_scheduler._notify_backup", lambda config, note: said.append(note)
        )
        return said

    def context(self) -> Any:
        from noust.core.notifications.context import NotificationContext

        return NotificationContext(server="web-1")

    def test_when_one_is_due_it_runs_and_a_pass_is_a_quiet_heartbeat(
        self, records: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.config import Config
        from noust.managers import backup_scheduler
        from noust.managers.restore_drill import DrillReport, DrillStep

        said = self.sent(monkeypatch)
        ran: list[dict[str, Any]] = []

        def run(self: RestoreDrill, domain: str, **kwargs: Any) -> DrillReport:
            ran.append(kwargs)
            return DrillReport(
                domain, "b-1", kwargs["trigger"], "t", "t", [DrillStep("a", True, "ok")]
            )

        monkeypatch.setattr(RestoreDrill, "run", run)

        result = backup_scheduler._drill_when_due(DOMAIN, "b-1", Config(), self.context())

        assert ran == [{"backup_id": "b-1", "trigger": "schedule"}]
        assert result is not None and result["ok"] is True
        assert [(note.kind, note.code) for note in said] == [("backup_success", "drill.passed")]

    def test_a_failed_drill_is_said_as_a_failed_backup_and_does_not_raise(
        self, records: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.config import Config
        from noust.managers import backup_scheduler
        from noust.managers.restore_drill import DrillReport, DrillStep

        said = self.sent(monkeypatch)
        monkeypatch.setattr(
            RestoreDrill,
            "run",
            lambda self, domain, **kw: DrillReport(
                domain, "b-1", "schedule", "t", "t", [DrillStep("archive", False, "checksum")]
            ),
        )

        result = backup_scheduler._drill_when_due(DOMAIN, "b-1", Config(), self.context())

        assert result is not None and result["ok"] is False
        assert [(note.kind, note.code, note.state.value) for note in said] == [
            ("backup_failed", "drill.failed", "failed")
        ]
        assert "checksum" in " ".join(said[0].excerpt.lines) if said[0].excerpt else True

    def test_when_none_is_due_nothing_runs(
        self, records: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.config import Config
        from noust.managers import backup_scheduler

        said = self.sent(monkeypatch)
        monkeypatch.setattr(RestoreDrill, "run", lambda *a, **k: pytest.fail("ran"))
        monkeypatch.setattr("noust.managers.restore_drill.drill_due", lambda *a, **k: False)

        assert backup_scheduler._drill_when_due(DOMAIN, "b", Config(), self.context()) is None
        assert said == []


class TestTheApi:
    @pytest.fixture
    def client(self, records: NoustStore) -> Any:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from noust.core.store import App
        from noust.web.api import restore_drills
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import install_error_handlers

        records.create_app(App(domain=DOMAIN, app_type="nodejs", app_path="/srv/shop"))
        api = FastAPI()
        install_error_handlers(api)
        api.include_router(restore_drills.router, prefix="/api/apps")
        session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
        api.dependency_overrides[get_current_session] = lambda: session
        return TestClient(api)

    def test_a_drill_is_queued_as_a_job_for_the_application(
        self, client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queued: list[dict[str, Any]] = []

        class Job:
            id = "job-1"
            status = type("Status", (), {"value": "pending"})()

            def to_dict(self) -> dict[str, Any]:
                return {"id": "job-1"}

        manager = SimpleNamespace(create_job=lambda **kw: queued.append(kw) or Job())
        monkeypatch.setattr("noust.web.api.databases.common.get_job_manager", lambda: manager)

        response = client.post(
            f"/api/apps/{DOMAIN}/restore-drills", json={"backup_id": "shop_20260101_000000"}
        )

        assert response.status_code == 202, response.text
        assert queued[0]["kwargs"] == {
            "domain": DOMAIN,
            "backup_id": "shop_20260101_000000",
            "actor": queued[0]["kwargs"]["actor"],
        }
        assert queued[0]["metadata"]["kind"] == "restore_drill"

    def test_the_reports_are_listed_with_how_often_a_schedule_runs_one(
        self, client: Any, records: NoustStore
    ) -> None:
        from noust.managers.restore_drill import DrillReport, DrillStep

        DrillRecords(records).save(
            DrillReport(DOMAIN, "b", "manual", "t0", "t1", [DrillStep("archive", True, "ok")])
        )

        body = client.get(f"/api/apps/{DOMAIN}/restore-drills").json()

        assert body["drill_days"] == DEFAULT_DRILL_DAYS
        assert [(item["backup_id"], item["ok"]) for item in body["items"]] == [("b", True)]

    def test_an_unknown_application_is_a_404(self, client: Any) -> None:
        assert client.get("/api/apps/nobody.example.com/restore-drills").status_code == 404
        assert client.post("/api/apps/nobody.example.com/restore-drills").status_code == 404

    def test_the_job_function_runs_the_drill_and_answers_its_report(
        self, records: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers.restore_drill import DrillReport, DrillStep
        from noust.web.api.restore_drills import drill_job

        monkeypatch.setattr(
            RestoreDrill,
            "run",
            lambda self, domain, **kw: DrillReport(
                domain, "b", "manual", "t0", "t1", [DrillStep("archive", True, "ok")]
            ),
        )

        result = drill_job(DOMAIN, "b", actor="user:alice")

        assert result["ok"] is True and result["backup_id"] == "b"

    def test_the_routes_declare_permissions(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_restore_drills import ROUTES

        assert ROUTES[("GET", "/api/apps/{domain}/restore-drills")] == Permission.BACKUPS_READ
        assert ROUTES[("POST", "/api/apps/{domain}/restore-drills")] == Permission.BACKUPS_RUN


class StackDrills:
    """What loading a stack's dump into a throwaway container answers, per database."""

    def __init__(self) -> None:
        self.tried: list[tuple[str, str, str]] = []
        self.answers: dict[str, Any] = {}

    def __call__(self, runner: Any, database: Any, dump: Path, **kwargs: Any) -> Any:
        from noust.managers.stack_drill import StackRestoreTest

        self.tried.append((database.service, database.engine, database.image))
        return self.answers.get(
            database.service, StackRestoreTest(True, f"{database.label} loaded", 0.5)
        )


@pytest.fixture
def stack_drills(monkeypatch: pytest.MonkeyPatch) -> StackDrills:
    """The throwaway-container test, answering what the test says."""
    fake = StackDrills()
    monkeypatch.setattr("noust.managers.restore_drill.restore_test_stack_dump", fake)
    return fake


def take_with_stack(
    manager: BackupManager, monkeypatch: pytest.MonkeyPatch, *entries: dict[str, str]
) -> Any:
    """Take a real backup whose stack holds the databases ``entries`` describe."""
    _use_store(monkeypatch, FakeApp(1, DOMAIN), [])

    def dump_stack(
        self: BackupManager, domain: str, app_path: Path, destination: Path, *, explicit: bool
    ) -> list[dict[str, Any]]:
        destination.mkdir(parents=True, exist_ok=True)
        found = []
        for entry in entries:
            name = f"stack-{entry['service']}-{entry['engine']}.dump.gz"
            (destination / name).write_bytes(b"PGDMP stack dump")
            found.append(
                {
                    "engine": entry["engine"],
                    "name": entry.get("database", ""),
                    "archive_path": f"wasm-backup/databases/{name}",
                    "size_bytes": 16,
                    "created": "2026-10-17T02:00:00",
                    "stack": entry,
                }
            )
        return found

    monkeypatch.setattr(BackupManager, "_dump_stack_databases", dump_stack)
    return manager.create(DOMAIN, include_databases=True)


POSTGRES_ENTRY = {
    "service": "db",
    "engine": "postgres",
    "database": "shop",
    "user": "shop",
    "image": "postgres:16-alpine",
}


class TestAStacksOwnDatabases:
    def test_each_dump_is_loaded_into_a_throwaway_container_of_its_own_image(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        stack_drills: StackDrills,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        take_with_stack(manager, monkeypatch, POSTGRES_ENTRY)

        report = drill(manager, engines, records)

        assert report.ok, report.summary
        assert stack_drills.tried == [("db", "postgres", "postgres:16-alpine")]
        step = next(step for step in report.steps if step.name == "stack/db")
        assert step.ok and not step.skipped and "loaded" in step.detail

    def test_a_dump_that_does_not_load_fails_the_drill_and_says_whose(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        stack_drills: StackDrills,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.managers.stack_drill import StackRestoreTest

        take_with_stack(manager, monkeypatch, POSTGRES_ENTRY)
        stack_drills.answers["db"] = StackRestoreTest(False, 'role "ghost" does not exist', 1.0)

        report = drill(manager, engines, records)

        assert not report.ok
        assert report.summary == 'stack/db: role "ghost" does not exist'
        assert DrillRecords(records).last_good(DOMAIN) is None

    def test_one_that_could_not_be_tried_is_said_and_does_not_fail_the_drill(
        self,
        manager: BackupManager,
        engines: Engines,
        records: NoustStore,
        stack_drills: StackDrills,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.managers.stack_drill import StackRestoreTest

        take_with_stack(manager, monkeypatch, {**POSTGRES_ENTRY, "image": ""})
        stack_drills.answers["db"] = StackRestoreTest(
            True, "the backup does not record the image", skipped=True
        )

        report = drill(manager, engines, records)

        step = next(step for step in report.steps if step.name == "stack/db")
        assert report.ok and step.ok and step.skipped

    def test_the_manifest_of_an_older_backup_still_unpacks(
        self,
        manager: BackupManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        legacy = {key: value for key, value in POSTGRES_ENTRY.items() if key != "image"}
        backup = take_with_stack(manager, monkeypatch, legacy)

        with manager.unpacked(backup.id) as unpacked:
            [(database, path)] = unpacked.stack
            assert database.image == "" and database.service == "db"
            assert path.is_file() and unpacked.stack_dumps == 1
