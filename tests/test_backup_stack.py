# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for the backup manager's side of a Compose stack's databases.

``tests/test_stack_databases.py`` pins how a database is found, dumped and put
back. Pinned here is what the backups do with that:

- the pre-update backup of a Compose application carries the dumps, and an
  update whose copy fails stops with the way to switch it off, because without
  the copy a migration cannot be undone;
- the application's own directory (the store's ``app_path``) is what is backed
  up and restored, wherever it is: it used to be assumed to be
  ``apps_directory/<name>``;
- a restore puts the dump back into its service with the application stopped,
  a rollback never does that by itself, and ``--databases-only`` does only that;
- the named volumes that are backed up are the ones Docker really has.
"""

from __future__ import annotations

import json
import sys
import tarfile
import types
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import BackupError
from noust.core.runner import CommandResult, FakeRunner
from noust.core.store import App, NoustStore
from noust.managers.backup_manager import (
    DATABASES_DIR,
    PAYLOAD_DIR,
    BackupManager,
    RollbackManager,
)
from noust.managers.stack_databases import StackBackupError, StackDatabase

FIXTURES = Path(__file__).parent / "fixtures" / "stack"
DOMAIN = "proggest.es"
DUMP_ARCHIVE_PATH = f"{PAYLOAD_DIR}/{DATABASES_DIR}/stack-postgres-postgres-proggest.dump.gz"


class StackRunner(FakeRunner):
    """
    A fake Docker for a stack: it resolves the configuration and runs the clients.

    Attributes:
        running: Services that have a running container.
        broken: Substrings of a command that make it fail, with the stderr it fails with.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self.config = config
        self.running: set[str] = {"postgres"}
        self.broken: dict[str, str] = {}
        self.stdin_contents: list[bytes] = []

    def _answer(self, argv: tuple[str, ...] | list[str]) -> CommandResult | None:
        line = " ".join(argv)
        for needle, stderr in self.broken.items():
            if needle in line:
                return CommandResult(argv=tuple(argv), exit_code=1, stderr=stderr)
        if argv[:2] == ["docker", "compose"] and "config" in argv:
            return CommandResult(argv=tuple(argv), exit_code=0, stdout=json.dumps(self.config))
        if argv[:2] == ["docker", "compose"] and "ps" in argv and "-q" in argv:
            running = argv[-1] in self.running
            return CommandResult(
                argv=tuple(argv), exit_code=0, stdout="3f2a9c1d7b8e\n" if running else ""
            )
        return None

    def run(self, argv, *, stdin_path=None, **kwargs):  # type: ignore[no-untyped-def]
        if stdin_path is not None:
            self.stdin_contents.append(Path(stdin_path).read_bytes())
        base = super().run(argv, stdin_path=stdin_path, **kwargs)
        answer = self._answer(list(argv))
        if answer is None:
            return base
        return replace(base, exit_code=answer.exit_code, stdout=answer.stdout, stderr=answer.stderr)

    def capture_to_file(self, argv, destination, **kwargs):  # type: ignore[no-untyped-def]
        answer = self._answer(list(argv))
        if answer is not None and not answer.success:
            self.calls.append(tuple(argv))
            self.envs.append(None)
            return answer
        return super().capture_to_file(argv, destination, **kwargs)

    def exec_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[:2] == ("docker", "compose") and "exec" in call]

    def docker_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] == "docker"]


@pytest.fixture
def proggest_config() -> dict[str, Any]:
    return json.loads((FIXTURES / "proggest-compose-config.json").read_text(encoding="utf-8"))


@pytest.fixture
def runner(proggest_config: dict[str, Any]) -> Iterator[StackRunner]:
    from noust.core.runner import set_runner

    fake = StackRunner(proggest_config)
    fake.script(["docker", "compose"], stdout="PGDMP")
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def app_path(tmp_path: Path, store: NoustStore) -> Path:
    """A deployed Compose application whose directory is not under ``apps_directory``."""
    path = tmp_path / "srv" / "legacy-proggest"
    path.mkdir(parents=True)
    (path / "docker-compose.prod.yml").write_text("services: {}\n")
    (path / ".env").write_text("DB_PASSWORD=dummy-db-password\n")
    store.create_app(
        App(domain=DOMAIN, app_type="docker-compose", app_path=str(path), status="running")
    )
    return path


@pytest.fixture
def manager(
    runner: StackRunner, tmp_path: Path, app_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[BackupManager]:
    backups = BackupManager(verbose=False, runner=runner)
    backups.backup_dir = tmp_path / "backups"
    previous = backups.config.get("apps_directory")
    # Nothing is under this directory: the application is where the store says.
    backups.config.set("apps_directory", str(tmp_path / "elsewhere"))
    yield backups
    backups.config.set("apps_directory", previous)


@pytest.fixture
def rollbacks(manager: BackupManager, runner: StackRunner) -> RollbackManager:
    rollback = RollbackManager(verbose=False, runner=runner)
    rollback.backup_manager = manager
    rollback.config = manager.config
    return rollback


def project_file(monkeypatch: pytest.MonkeyPatch, backup_databases: Any) -> list[Path]:
    """Install a stand-in for the project file reader; returns the roots it was asked about."""
    roots: list[Path] = []
    module = types.ModuleType("noust.deployers.helpers.project_file")

    def load_project_file(root: Path) -> Any:
        roots.append(root)
        return types.SimpleNamespace(backup_databases=backup_databases)

    module.load_project_file = load_project_file  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "noust.deployers.helpers.project_file", module)
    return roots


def archive_names(manager: BackupManager, backup_id: str) -> list[str]:
    archive = manager.backup_dir / "proggest-es" / f"{backup_id}.tar.gz"
    with tarfile.open(archive) as tar:
        return tar.getnames()


#: A rollback as the console runs it, with a gate that answers: what is under test is
#: what the restore does, not the unit that would be probed.
ROLLBACK: dict[str, Any] = {
    "rebuild": False,
    "app_type": "docker-compose",
    "gate": lambda: (True, ""),
}


@dataclass(frozen=True)
class Declared:
    service: str
    engine: str
    database: str | None = None
    user: str | None = None


class TestTheApplicationIsWhereTheStoreSaysItIs:
    """``apps_directory/<name>`` was an assumption; ``apps.app_path`` is the record."""

    def test_an_application_outside_the_applications_directory_can_be_backed_up(
        self, manager: BackupManager, app_path: Path
    ) -> None:
        metadata = manager.create(DOMAIN)

        names = archive_names(manager, metadata.id)
        assert any(name.endswith("docker-compose.prod.yml") for name in names)

    def test_and_restored_to_the_same_place(
        self, manager: BackupManager, app_path: Path, tmp_path: Path
    ) -> None:
        metadata = manager.create(DOMAIN)
        (app_path / "docker-compose.prod.yml").write_text("services: {changed: {}}\n")

        manager.restore(metadata.id, stack_databases=False)

        assert (app_path / "docker-compose.prod.yml").read_text() == "services: {}\n"
        assert not (tmp_path / "elsewhere" / "proggest-es").exists()

    def test_the_pre_update_backup_finds_it_too(
        self, rollbacks: RollbackManager, store: NoustStore
    ) -> None:
        store.set_app_backup_before_update(DOMAIN, False)

        backup = rollbacks.create_pre_deploy_backup(DOMAIN)

        assert backup is not None

    def test_an_application_with_no_row_is_where_the_directory_setting_says(
        self, manager: BackupManager, tmp_path: Path
    ) -> None:
        nameless = tmp_path / "elsewhere" / "plain-example-com"
        nameless.mkdir(parents=True)
        (nameless / "index.html").write_text("<p>hi</p>")

        metadata = manager.create("plain.example.com")

        assert metadata.app_name == "plain-example-com"


class TestThePreUpdateBackupCarriesTheDatabase:
    """The dump is inside the archive, with the manifest entry that says how to put it back."""

    def test_the_dump_and_its_entry_are_in_the_backup(
        self, rollbacks: RollbackManager, manager: BackupManager, runner: StackRunner
    ) -> None:
        backup = rollbacks.create_pre_deploy_backup(DOMAIN)

        assert backup is not None
        assert backup.includes_databases is True
        (entry,) = backup.database_backups
        assert entry["engine"] == "postgres"
        assert entry["stack"]["service"] == "postgres"
        assert entry["archive_path"] == DUMP_ARCHIVE_PATH
        assert DUMP_ARCHIVE_PATH in archive_names(manager, backup.id)

    def test_it_is_the_default_for_a_compose_application(
        self, rollbacks: RollbackManager, runner: StackRunner
    ) -> None:
        rollbacks.create_pre_deploy_backup(DOMAIN)

        assert len(runner.exec_calls()) == 1

    def test_switched_off_it_takes_no_copy_and_asks_docker_nothing(
        self, rollbacks: RollbackManager, runner: StackRunner, store: NoustStore
    ) -> None:
        store.set_app_backup_before_update(DOMAIN, False)

        backup = rollbacks.create_pre_deploy_backup(DOMAIN)

        assert backup is not None and backup.includes_databases is False
        assert runner.docker_calls() == []

    def test_an_application_that_is_not_a_stack_is_backed_up_as_before(
        self,
        rollbacks: RollbackManager,
        runner: StackRunner,
        store: NoustStore,
        tmp_path: Path,
    ) -> None:
        site = tmp_path / "srv" / "site"
        site.mkdir(parents=True)
        (site / "package.json").write_text("{}")
        store.create_app(App(domain="site.example.com", app_type="nodejs", app_path=str(site)))

        backup = rollbacks.create_pre_deploy_backup("site.example.com")

        assert backup is not None and backup.includes_databases is False
        assert runner.docker_calls() == []

    def test_a_dump_that_fails_stops_the_backup_and_says_how_to_switch_it_off(
        self, rollbacks: RollbackManager, manager: BackupManager, runner: StackRunner
    ) -> None:
        runner.broken["pg_dump"] = 'service "postgres" is not running'

        with pytest.raises(StackBackupError) as raised:
            rollbacks.create_pre_deploy_backup(DOMAIN)

        assert raised.value.output == 'service "postgres" is not running'
        assert "noust app backup-before-update proggest.es off" in raised.value.details
        # An archive without the copy it promised must not be left to be mistaken for a backup.
        assert manager.list_backups(domain=DOMAIN) == []

    def test_a_stack_compose_cannot_resolve_stops_it_the_same_way(
        self, rollbacks: RollbackManager, runner: StackRunner
    ) -> None:
        runner.broken["config --format json"] = "required variable DB_PASSWORD is missing a value"

        with pytest.raises(StackBackupError) as raised:
            rollbacks.create_pre_deploy_backup(DOMAIN)

        assert "DB_PASSWORD" in raised.value.output

    def test_the_project_file_can_switch_it_off(
        self,
        rollbacks: RollbackManager,
        runner: StackRunner,
        app_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        roots = project_file(monkeypatch, "off")

        backup = rollbacks.create_pre_deploy_backup(DOMAIN)

        assert backup is not None and backup.includes_databases is False
        assert roots == [app_path]
        assert runner.docker_calls() == []

    def test_the_project_file_can_declare_what_detection_cannot_see(
        self,
        rollbacks: RollbackManager,
        runner: StackRunner,
        proggest_config: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        proggest_config["services"]["postgres"]["image"] = "bitnami/postgresql:16"
        project_file(monkeypatch, (Declared("postgres", "postgres", "proggest", "proggest"),))

        backup = rollbacks.create_pre_deploy_backup(DOMAIN)

        assert backup is not None and len(backup.database_backups) == 1

    def test_a_bitnami_database_is_not_dumped_unless_declared(
        self,
        rollbacks: RollbackManager,
        runner: StackRunner,
        proggest_config: dict[str, Any],
    ) -> None:
        proggest_config["services"]["postgres"]["image"] = "bitnami/postgresql:16"

        backup = rollbacks.create_pre_deploy_backup(DOMAIN)

        assert backup is not None and backup.includes_databases is False

    def test_a_stack_without_a_compose_file_has_nothing_to_dump_and_is_not_blocked(
        self, rollbacks: RollbackManager, runner: StackRunner, app_path: Path
    ) -> None:
        # The update itself fails, saying there is no compose file, before it changes anything.
        (app_path / "docker-compose.prod.yml").unlink()

        backup = rollbacks.create_pre_deploy_backup(DOMAIN)

        assert backup is not None and backup.includes_databases is False
        assert runner.docker_calls() == []

    def test_a_rehearsal_dumps_nothing(
        self, rollbacks: RollbackManager, runner: StackRunner
    ) -> None:
        from noust.core.fs import DryRunFileSystem, set_fs

        set_fs(DryRunFileSystem())
        try:
            rollbacks.create_pre_deploy_backup(DOMAIN)
        finally:
            set_fs(None)

        assert runner.exec_calls() == []

    def test_a_manual_backup_with_databases_includes_the_stacks_even_when_the_update_copy_is_off(
        self, manager: BackupManager, runner: StackRunner, store: NoustStore
    ) -> None:
        store.set_app_backup_before_update(DOMAIN, False)

        backup = manager.create(DOMAIN, include_databases=True)

        assert backup.includes_databases is True
        assert backup.database_backups[0]["stack"]["service"] == "postgres"

    def test_a_manual_backup_without_databases_asks_docker_nothing(
        self, manager: BackupManager, runner: StackRunner
    ) -> None:
        manager.create(DOMAIN)

        assert runner.docker_calls() == []


class TestPuttingTheDatabaseBack:
    """A restore replaces the database, with the application stopped; a rollback leaves it alone."""

    @pytest.fixture
    def backup_id(self, rollbacks: RollbackManager) -> str:
        backup = rollbacks.create_pre_deploy_backup(DOMAIN)
        assert backup is not None
        return backup.id

    def restores(self, runner: StackRunner) -> list[tuple[str, ...]]:
        return [call for call in runner.exec_calls() if "pg_restore" in call]

    def test_restore_puts_the_dump_back_into_its_service(
        self, manager: BackupManager, runner: StackRunner, backup_id: str
    ) -> None:
        manager.restore(backup_id)

        (restore,) = self.restores(runner)
        assert "postgres" in restore and "--clean" in restore and "--if-exists" in restore
        assert runner.stdin_contents == [b"PGDMP"]

    def test_the_application_is_stopped_before_and_started_after(
        self, manager: BackupManager, runner: StackRunner, backup_id: str
    ) -> None:
        class Unit:
            """A unit that is running, and says so in the order commands ran."""

            def get_status(self, name: str) -> dict[str, Any]:
                return {"exists": True, "active": True}

            def stop(self, name: str) -> None:
                runner.calls.append(("systemctl", "stop", name))

            def start(self, name: str) -> None:
                runner.calls.append(("systemctl", "start", name))

        manager.service_manager = Unit()  # type: ignore[assignment]

        manager.restore(backup_id)

        calls = [" ".join(call) for call in runner.calls]
        stop = next(i for i, call in enumerate(calls) if "systemctl stop" in call)
        restore = next(i for i, call in enumerate(calls) if "pg_restore" in call)
        start = next(i for i, call in enumerate(calls) if "systemctl start" in call)
        assert stop < restore < start

    def test_a_restore_that_would_leave_the_application_running_is_refused_before_anything_changes(
        self, manager: BackupManager, app_path: Path, backup_id: str, runner: StackRunner
    ) -> None:
        (app_path / "marker").write_text("untouched")

        with pytest.raises(BackupError) as raised:
            manager.restore(backup_id, stop_service=False)

        assert "stopped" in raised.value.message
        assert (app_path / "marker").read_text() == "untouched"
        assert self.restores(runner) == []

    def test_a_rollback_does_not_restore_the_database(
        self,
        rollbacks: RollbackManager,
        manager: BackupManager,
        runner: StackRunner,
        backup_id: str,
    ) -> None:
        rollbacks.rollback(DOMAIN, backup_id, **ROLLBACK)

        assert self.restores(runner) == []

    def test_a_rollback_says_the_copy_is_there_and_how_to_put_it_back(
        self,
        rollbacks: RollbackManager,
        backup_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from noust.core.logger import Logger

        said: list[str] = []
        monkeypatch.setattr(
            Logger, "_write", lambda self, message, newline=True: said.append(message)
        )

        rollbacks.rollback(DOMAIN, backup_id, **ROLLBACK)

        assert any(f"noust backup restore {backup_id} --databases-only" in line for line in said)

    def test_the_safety_backup_of_a_rollback_does_not_dump_the_database_again(
        self, rollbacks: RollbackManager, runner: StackRunner, backup_id: str
    ) -> None:
        before = len(runner.exec_calls())

        rollbacks.rollback(DOMAIN, backup_id, **ROLLBACK)

        assert len(runner.exec_calls()) == before

    def test_databases_only_puts_back_the_dump_and_touches_no_file(
        self, manager: BackupManager, runner: StackRunner, app_path: Path, backup_id: str
    ) -> None:
        (app_path / "docker-compose.prod.yml").write_text("services: {changed: {}}\n")

        restored = manager.restore_stack_databases(backup_id)

        assert [db.service for db in restored] == ["postgres"]
        assert len(self.restores(runner)) == 1
        assert (app_path / "docker-compose.prod.yml").read_text() == "services: {changed: {}}\n"

    def test_databases_only_on_a_backup_without_a_copy_says_so(
        self, manager: BackupManager, store: NoustStore, runner: StackRunner
    ) -> None:
        store.set_app_backup_before_update(DOMAIN, False)
        plain = manager.create(DOMAIN)

        with pytest.raises(BackupError) as raised:
            manager.restore_stack_databases(plain.id)

        assert "no copy of a database" in raised.value.message

    def test_a_restore_of_a_dump_the_engine_rejects_names_what_it_said(
        self, manager: BackupManager, runner: StackRunner, backup_id: str
    ) -> None:
        runner.broken["pg_restore"] = "pg_restore: error: could not execute query"

        with pytest.raises(BackupError) as raised:
            manager.restore(backup_id)

        assert raised.value.output == "pg_restore: error: could not execute query"


class TestVolumesKeepTheirRealNames:
    """``_discover_docker_volumes`` listed the keys of ``volumes:``; Docker names them otherwise."""

    def test_they_come_from_the_resolved_configuration(
        self, manager: BackupManager, app_path: Path
    ) -> None:
        assert manager._discover_docker_volumes(app_path, DOMAIN) == [
            "proggest_pgdata",
            "proggest_redisdata",
        ]

    def test_a_volume_that_names_itself_keeps_its_name(
        self, manager: BackupManager, app_path: Path, proggest_config: dict[str, Any]
    ) -> None:
        proggest_config["volumes"]["shared"] = {"name": "company-shared"}

        names = manager._discover_docker_volumes(app_path, DOMAIN)

        assert "company-shared" in names and "proggest_shared" not in names

    def test_without_docker_the_compose_file_and_the_project_name_say_the_same(
        self, manager: BackupManager, app_path: Path, runner: StackRunner
    ) -> None:
        (app_path / "docker-compose.prod.yml").write_text(
            "services: {}\nvolumes:\n  pgdata:\n  shared:\n    name: company-shared\n"
        )
        runner.broken["config --format json"] = "required variable X is missing a value"

        names = manager._discover_docker_volumes(app_path, DOMAIN)

        # The project is the directory's name, as Compose derives it.
        assert names == ["legacy-proggest_pgdata", "company-shared"]

    def test_the_backup_archives_the_volume_that_has_the_data(
        self, manager: BackupManager, runner: StackRunner
    ) -> None:
        manager.create(DOMAIN, include_docker_volumes=True)

        mounts = [
            part
            for call in runner.docker_calls()
            if call[:2] == ("docker", "run")
            for part in call
            if part.endswith(":/data:ro")
        ]
        assert mounts == ["proggest_pgdata:/data:ro", "proggest_redisdata:/data:ro"]


class Reached(Exception):
    """The update went past its backup."""


class TestAnUpdateDoesNotGoOnWithoutItsCopy:
    """
    Without the copy there is no way back from a migration, so the update stops
    and says how to go on without it. Any other reason a backup cannot be
    taken still only warns, exactly as before 3.2.
    """

    @pytest.fixture
    def update(self, app_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        from types import SimpleNamespace

        from noust.deployers import lifecycle

        def go_past(*args: Any, **kwargs: Any) -> None:
            raise Reached

        monkeypatch.setattr(lifecycle, "SourceManager", lambda verbose=False: SimpleNamespace())
        monkeypatch.setattr(lifecycle, "_serving_commit", go_past)

        def run(backup_error: Exception | None) -> None:
            def create_pre_deploy_backup(**kwargs: Any) -> None:
                if backup_error is not None:
                    raise backup_error

            monkeypatch.setattr(
                lifecycle,
                "RollbackManager",
                lambda verbose=False: SimpleNamespace(
                    create_pre_deploy_backup=create_pre_deploy_backup
                ),
            )
            lifecycle.update_app(DOMAIN)

        return run

    def test_stack_dump_failure_stops_with_fix(self, update: Any) -> None:
        failure = StackBackupError(
            "Could not dump postgres/proggest (service postgres)",
            details="To update proggest.es without a copy of its databases, run: "
            "noust app backup-before-update proggest.es off.",
            output="service postgres is not running",
        )

        with pytest.raises(StackBackupError) as raised:
            update(failure)

        assert raised.value is failure

    def test_any_other_backup_failure_still_only_warns(self, update: Any) -> None:
        with pytest.raises(Reached):
            update(BackupError("No space left on device"))

    def test_update_without_project_file_matches_3_1(self, update: Any) -> None:
        # Nothing wrong with the backup: the update proceeds to the pull, as in 3.1.
        with pytest.raises(Reached):
            update(None)


class TestDatabasesOnlyIsCarefulWithTheApplication:
    """Only the databases go back: the application stops while they do, and comes back."""

    @pytest.fixture
    def backup_id(self, rollbacks: RollbackManager) -> str:
        backup = rollbacks.create_pre_deploy_backup(DOMAIN)
        assert backup is not None
        return backup.id

    @pytest.fixture
    def unit(self, manager: BackupManager, runner: StackRunner) -> Any:
        class Unit:
            """A unit that is running, and says what was done to it, in order."""

            def get_status(self, name: str) -> dict[str, Any]:
                return {"exists": True, "active": True}

            def stop(self, name: str) -> None:
                runner.calls.append(("systemctl", "stop", name))

            def start(self, name: str) -> None:
                runner.calls.append(("systemctl", "start", name))

        manager.service_manager = Unit()  # type: ignore[assignment]
        return manager.service_manager

    def order(self, runner: StackRunner) -> list[str]:
        words = []
        for call in runner.calls:
            joined = " ".join(call)
            if "systemctl stop" in joined:
                words.append("stop")
            elif "pg_restore" in joined:
                words.append("restore")
            elif "systemctl start" in joined:
                words.append("start")
        return words

    def test_stopped_while_the_dump_goes_back_and_started_after(
        self, manager: BackupManager, runner: StackRunner, backup_id: str, unit: Any
    ) -> None:
        manager.restore_stack_databases(backup_id)

        assert self.order(runner) == ["stop", "restore", "start"]

    def test_started_again_even_when_the_engine_refuses_the_dump(
        self, manager: BackupManager, runner: StackRunner, backup_id: str, unit: Any
    ) -> None:
        runner.broken["pg_restore"] = "pg_restore: error: boom"

        with pytest.raises(BackupError):
            manager.restore_stack_databases(backup_id)

        # The client was run and refused; the application is back either way.
        assert self.order(runner) == ["stop", "restore", "start"]

    def test_a_rehearsal_changes_nothing(
        self, manager: BackupManager, runner: StackRunner, backup_id: str, unit: Any
    ) -> None:
        from noust.core.fs import DryRunFileSystem, set_fs

        before = len(runner.calls)
        set_fs(DryRunFileSystem())
        try:
            restored = manager.restore_stack_databases(backup_id)
        finally:
            set_fs(None)

        assert [db.service for db in restored] == ["postgres"]
        assert len(runner.calls) == before

    def test_a_restored_tree_without_a_compose_file_cannot_take_the_dump(
        self, manager: BackupManager, runner: StackRunner, app_path: Path, backup_id: str
    ) -> None:
        (app_path / "docker-compose.prod.yml").unlink()

        with pytest.raises(BackupError) as raised:
            manager.restore_stack_databases(backup_id)

        assert "Cannot put back the databases of proggest.es" in raised.value.message


class TestATamperedManifestIsNotTrusted:
    """The manifest sits in an archive that may have come from anywhere."""

    def entry(self, **changes: Any) -> dict[str, Any]:
        stack = {"service": "postgres", "engine": "postgres", "database": "x", "user": "x"}
        return {"stack": {**stack, **changes}, "archive_path": DUMP_ARCHIVE_PATH}

    @pytest.mark.parametrize(
        "changes",
        [
            {"engine": "oracle"},
            {"service": "../etc"},
            {"service": "a b"},
            {"user": "-rf"},
            {"database": "x;y"},
            {"engine": 7},
        ],
    )
    def test_an_entry_a_restore_cannot_use_is_refused(self, changes: dict[str, Any]) -> None:
        assert StackDatabase.from_entry(self.entry(**changes)["stack"]) is None

    def test_a_dump_named_outside_the_databases_directory_is_refused(
        self, manager: BackupManager, rollbacks: RollbackManager, tmp_path: Path
    ) -> None:
        from noust.managers.stack_databases import extract_stack_dumps

        backup = rollbacks.create_pre_deploy_backup(DOMAIN)
        assert backup is not None
        archive = manager.backup_dir / "proggest-es" / f"{backup.id}.tar.gz"

        for name in ("legacy-proggest/.env", f"{PAYLOAD_DIR}/{DATABASES_DIR}/../manifest.json"):
            with pytest.raises(BackupError) as raised:
                extract_stack_dumps(
                    archive,
                    [name],
                    tmp_path,
                    prefix=f"{PAYLOAD_DIR}/{DATABASES_DIR}/",
                    max_bytes=10**9,
                )
            assert "outside the archive" in raised.value.message

    def test_a_dump_the_archive_does_not_carry_is_refused(
        self, manager: BackupManager, rollbacks: RollbackManager, tmp_path: Path
    ) -> None:
        from noust.managers.stack_databases import extract_stack_dumps

        backup = rollbacks.create_pre_deploy_backup(DOMAIN)
        assert backup is not None
        archive = manager.backup_dir / "proggest-es" / f"{backup.id}.tar.gz"

        with pytest.raises(BackupError) as raised:
            extract_stack_dumps(
                archive,
                [f"{PAYLOAD_DIR}/{DATABASES_DIR}/stack-nothing.dump.gz"],
                tmp_path,
                prefix=f"{PAYLOAD_DIR}/{DATABASES_DIR}/",
                max_bytes=10**9,
            )

        assert "does not carry" in raised.value.message

    def test_a_dump_larger_than_an_archive_may_hold_is_refused(
        self, manager: BackupManager, rollbacks: RollbackManager, tmp_path: Path
    ) -> None:
        from noust.managers.stack_databases import extract_stack_dumps

        backup = rollbacks.create_pre_deploy_backup(DOMAIN)
        assert backup is not None
        archive = manager.backup_dir / "proggest-es" / f"{backup.id}.tar.gz"

        with pytest.raises(BackupError):
            extract_stack_dumps(
                archive, [DUMP_ARCHIVE_PATH], tmp_path, prefix=f"{PAYLOAD_DIR}/", max_bytes=1
            )


class TestTheProjectFileReader:
    """``backup.databases`` comes from the project file when its reader exists."""

    def test_without_a_reader_detection_is_all_there_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers.stack_databases import declared_databases

        # A None entry in sys.modules is how an import is made to fail.
        monkeypatch.setitem(sys.modules, "noust.deployers.helpers.project_file", None)

        assert declared_databases(tmp_path) == "auto"

    def test_the_value_is_the_readers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers.stack_databases import declared_databases

        roots = project_file(monkeypatch, "off")

        assert declared_databases(tmp_path) == "off"
        assert roots == [tmp_path]


class TestNothingChangesForWhatIsNotAStack:
    """A site, with databases asked for, is backed up as it always was."""

    def test_a_manual_backup_with_databases_of_a_site_asks_docker_nothing(
        self, manager: BackupManager, runner: StackRunner, store: NoustStore, tmp_path: Path
    ) -> None:
        site = tmp_path / "srv" / "site"
        site.mkdir(parents=True)
        (site / "index.html").write_text("<p>hi</p>")
        store.create_app(App(domain="site.example.com", app_type="static", app_path=str(site)))

        manager.create("site.example.com", include_databases=True)

        assert runner.docker_calls() == []


class TestTheWholeUpdateStopsBeforeTouchingAnything:
    """The real backup managers, a stack whose database cannot be dumped, the real update."""

    def test_nothing_is_pulled_built_or_recreated_and_the_error_says_how_to_go_on(
        self,
        rollbacks: RollbackManager,
        manager: BackupManager,
        runner: StackRunner,
        app_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace

        from noust.deployers import lifecycle

        runner.broken["pg_dump"] = "pg_dump: error: password authentication failed"
        touched: list[str] = []
        monkeypatch.setattr(lifecycle, "RollbackManager", lambda verbose=False: rollbacks)
        monkeypatch.setattr(
            lifecycle,
            "SourceManager",
            lambda verbose=False: SimpleNamespace(
                pull=lambda *a, **k: touched.append("pull"),
                fetch=lambda *a, **k: touched.append("fetch"),
            ),
        )
        monkeypatch.setattr(
            lifecycle, "_rebuild_compose", lambda *a, **k: touched.append("rebuild")
        )
        (app_path / "marker").write_text("as it was")

        with pytest.raises(StackBackupError) as raised:
            lifecycle.update_app(DOMAIN)

        assert raised.value.output == "pg_dump: error: password authentication failed"
        assert "noust app backup-before-update proggest.es off" in raised.value.details
        assert touched == []
        assert (app_path / "marker").read_text() == "as it was"
        assert manager.list_backups(domain=DOMAIN) == []
        # Nothing of the stack was recreated or restarted.
        assert not [call for call in runner.docker_calls() if "up" in call or "build" in call]

    def test_switched_off_the_same_update_goes_on(
        self,
        rollbacks: RollbackManager,
        runner: StackRunner,
        store: NoustStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from types import SimpleNamespace

        from noust.deployers import lifecycle

        runner.broken["pg_dump"] = "pg_dump: error: password authentication failed"
        store.set_app_backup_before_update(DOMAIN, False)
        monkeypatch.setattr(lifecycle, "RollbackManager", lambda verbose=False: rollbacks)
        monkeypatch.setattr(lifecycle, "SourceManager", lambda verbose=False: SimpleNamespace())

        def go_past(*args: Any, **kwargs: Any) -> None:
            raise Reached

        monkeypatch.setattr(lifecycle, "_serving_commit", go_past)

        with pytest.raises(Reached):
            lifecycle.update_app(DOMAIN)
