# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db backup-schedule``, ``backup-run``, ``backup-verify`` and the rest.

The second client of :class:`~noust.managers.database.backups.DatabaseBackups`:
these tests hold the commands to what the console shows for the same thing, and
hold the timer's own command line to running.
"""

from __future__ import annotations

import io
import json
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.audit_policy import is_read_only
from noust.cli.commands import db as db_cli
from noust.core.logger import Logger
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.backup_scheduler import BackupScheduler
from noust.managers.database.base import BaseDatabaseManager
from tests.database_backup_support import PG_ARCHIVE, PG_LISTING, RcloneRunner, make_engine
from tests.test_database_backups import add_destination

UNIT = "noust-backup-db-postgresql-shop"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    """Force a fresh store singleton per test."""
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    """
    Args:
        tmp_path: Per-test temporary directory.

    Yields:
        A fake rclone that also lists pg_restore content, installed as the runner.
    """
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    fake.script(("systemctl", "show", f"{UNIT}.timer"), stdout="LoadState=loaded\n")
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The unit directory, inside the sandbox.
    """
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


@pytest.fixture
def engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner, systemd_dir: Path
) -> type[BaseDatabaseManager]:
    """
    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Patching helper, scoped to the test.
        runner: The installed fake runner.
        systemd_dir: The sandboxed unit directory.

    Returns:
        A PostgreSQL fake with one database, behind every engine name.
    """
    (tmp_path / "dumps").mkdir()
    cls = make_engine(tmp_path / "dumps")
    monkeypatch.setattr(db_cli, "get_db_manager", lambda engine, verbose=False: cls())
    return cls


@pytest.fixture
def cli_runner() -> CliRunner:
    """
    Returns:
        A Click test runner.
    """
    return CliRunner()


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """
    Capture what a command reports through the logger, which binds ``sys.stdout``
    at import time and so is never seen by the CliRunner.

    Args:
        monkeypatch: Patching helper, scoped to the test.

    Returns:
        The buffer the logger writes to.
    """
    buffer = io.StringIO()

    def factory(**kwargs: Any) -> Logger:
        return Logger(stream=buffer, **kwargs)

    monkeypatch.setattr(cli_app, "Logger", factory)
    monkeypatch.setattr(db_cli, "Logger", factory)
    return buffer


def invoke(cli_runner: CliRunner, *args: str, **kwargs: Any) -> Any:
    """
    Run ``noust db ...``.

    Args:
        cli_runner: The Click test runner.
        *args: What follows ``db``.
        **kwargs: For :meth:`CliRunner.invoke`.

    Returns:
        The result.
    """
    return cli_runner.invoke(cli_app.cli, ["db", *args], **kwargs)


def take_dump(engine: type[BaseDatabaseManager]) -> str:
    """
    Args:
        engine: The fake engine.

    Returns:
        The file name of a dump the fake engine just wrote.
    """
    return engine().backup("shop").path.name


# ============================================================ the schedule


class TestBackupSchedule:
    def test_set_installs_the_timer_and_prints_the_policy(
        self,
        cli_runner: CliRunner,
        engine: type[BaseDatabaseManager],
        logged: io.StringIO,
        systemd_dir: Path,
    ) -> None:
        result = invoke(
            cli_runner,
            "backup-schedule",
            "set",
            "shop",
            "-e",
            "postgresql",
            "--schedule",
            "weekly",
            "--keep",
            "4",
            "--keep-days",
            "21",
        )

        assert result.exit_code == 0, result.output + logged.getvalue()
        assert (systemd_dir / f"{UNIT}.timer").is_file()
        assert "Mon *-*-* 02:00:00" in result.output
        assert "last 4, 21 days" in result.output

    def test_set_as_json_is_the_console_s_policy(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(
            cli_runner,
            "backup-schedule",
            "set",
            "shop",
            "-e",
            "postgresql",
            "--verify-restore",
            "--json",
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["configured"] is True
        assert data["verify_restore"] is True
        assert data["retention_count"] == 7
        assert data["engine"] == "postgresql"

    def test_a_destination_takes_its_own_retention(
        self,
        cli_runner: CliRunner,
        engine: type[BaseDatabaseManager],
        runner: RcloneRunner,
    ) -> None:
        add_destination(runner)

        result = invoke(
            cli_runner,
            "backup-schedule",
            "set",
            "shop",
            "-e",
            "postgresql",
            "--to",
            "nas:3:14",
            "--json",
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["destinations"] == [
            {
                "name": "nas",
                "retention_count": 3,
                "retention_days": 14,
                "exists": True,
                "encrypted": False,
            }
        ]

    def test_none_means_no_limit(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(
            cli_runner,
            "backup-schedule",
            "set",
            "shop",
            "-e",
            "postgresql",
            "--keep",
            "none",
            "--keep-days",
            "none",
            "--json",
        )

        data = json.loads(result.output)
        assert (data["retention_count"], data["retention_days"]) == (None, None)

    def test_a_retention_that_is_not_a_number_is_a_usage_error(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], systemd_dir: Path
    ) -> None:
        result = invoke(
            cli_runner, "backup-schedule", "set", "shop", "-e", "postgresql", "--keep", "many"
        )

        assert result.exit_code != 0
        assert list(systemd_dir.iterdir()) == []

    def test_a_destination_that_does_not_exist_is_refused_with_the_fix(
        self,
        cli_runner: CliRunner,
        engine: type[BaseDatabaseManager],
        logged: io.StringIO,
        systemd_dir: Path,
    ) -> None:
        result = invoke(
            cli_runner, "backup-schedule", "set", "shop", "-e", "postgresql", "--to", "nowhere"
        )

        assert result.exit_code == 1
        assert "nowhere" in logged.getvalue()
        assert "noust backup destination add" in logged.getvalue()
        assert list(systemd_dir.iterdir()) == []

    def test_disable_keeps_the_settings_and_installs_no_timer(
        self,
        cli_runner: CliRunner,
        engine: type[BaseDatabaseManager],
        systemd_dir: Path,
    ) -> None:
        result = invoke(
            cli_runner,
            "backup-schedule",
            "set",
            "shop",
            "-e",
            "postgresql",
            "--disable",
            "--json",
        )

        assert json.loads(result.output)["enabled"] is False
        assert list(systemd_dir.iterdir()) == []

    def test_show_lists_every_policy_or_one(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        invoke(cli_runner, "backup-schedule", "set", "shop", "-e", "postgresql")

        every = invoke(cli_runner, "backup-schedule", "show", "--json")
        one = invoke(cli_runner, "backup-schedule", "show", "shop", "-e", "postgresql", "--json")
        none = invoke(cli_runner, "backup-schedule", "show", "ghost", "-e", "postgresql", "--json")

        assert [p["database"] for p in json.loads(every.output)] == ["shop"]
        assert json.loads(one.output)[0]["configured"] is True
        assert json.loads(none.output)[0]["configured"] is False

    def test_show_of_a_database_needs_its_engine(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], logged: io.StringIO
    ) -> None:
        result = invoke(cli_runner, "backup-schedule", "show", "shop")

        assert result.exit_code == 1
        assert "--engine" in logged.getvalue()

    def test_show_says_what_is_missing_in_words(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(cli_runner, "backup-schedule", "show", "shop", "-e", "postgresql")

        assert "No backup policy" in result.output

    def test_remove_asks_and_then_removes_the_timer(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], systemd_dir: Path
    ) -> None:
        invoke(cli_runner, "backup-schedule", "set", "shop", "-e", "postgresql")

        declined = invoke(
            cli_runner, "backup-schedule", "remove", "shop", "-e", "postgresql", input="n\n"
        )
        assert (systemd_dir / f"{UNIT}.timer").exists()
        assert declined.exit_code == 0

        removed = invoke(
            cli_runner, "backup-schedule", "remove", "shop", "-e", "postgresql", "--force"
        )

        assert removed.exit_code == 0
        assert not (systemd_dir / f"{UNIT}.timer").exists()

    def test_only_show_is_read_only_for_the_audit_policy(self) -> None:
        group = db_cli.cli.commands["backup-schedule"]

        assert is_read_only(group.commands["show"], "db backup-schedule show") is True
        assert is_read_only(group.commands["set"], "db backup-schedule set") is False
        assert is_read_only(group.commands["remove"], "db backup-schedule remove") is False
        assert is_read_only(db_cli.cli.commands["backup-remote"], "db backup-remote") is True
        assert is_read_only(db_cli.cli.commands["backup-run"], "db backup-run") is False


# ================================================================ running


class TestBackupRun:
    def test_a_run_dumps_checks_and_says_so(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], logged: io.StringIO
    ) -> None:
        invoke(cli_runner, "backup-schedule", "set", "shop", "-e", "postgresql")

        result = invoke(cli_runner, "backup-run", "shop", "-e", "postgresql")

        assert result.exit_code == 0, result.output + logged.getvalue()
        assert "Backup complete: postgresql-shop-" in logged.getvalue()

    def test_a_run_as_json_names_the_dump_and_what_each_destination_said(
        self,
        cli_runner: CliRunner,
        engine: type[BaseDatabaseManager],
        runner: RcloneRunner,
    ) -> None:
        add_destination(runner)
        invoke(cli_runner, "backup-schedule", "set", "shop", "-e", "postgresql", "--to", "nas")

        result = invoke(cli_runner, "backup-run", "shop", "-e", "postgresql", "--json")

        data = json.loads(result.output)
        assert data["dump"]["kind"] == "scheduled"
        assert data["destinations"]["nas"]["ok"] is True

    def test_a_failure_exits_1_says_the_tools_words_and_is_announced(
        self,
        cli_runner: CliRunner,
        engine: type[BaseDatabaseManager],
        runner: RcloneRunner,
        logged: io.StringIO,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sent: list[Any] = []
        monkeypatch.setattr("noust.managers.database.backups.deliver", sent.append)
        runner.script(("pg_restore", "--list"), stderr="pg_restore: error: bad header", exit_code=1)

        result = invoke(cli_runner, "backup-run", "shop", "-e", "postgresql")

        assert result.exit_code == 1
        assert "pg_restore: error: bad header" in logged.getvalue()
        assert len(sent) == 1

    def test_the_timers_own_command_line_runs(
        self,
        cli_runner: CliRunner,
        engine: type[BaseDatabaseManager],
        systemd_dir: Path,
        logged: io.StringIO,
    ) -> None:
        invoke(cli_runner, "backup-schedule", "set", "shop", "-e", "postgresql")
        line = next(
            row
            for row in (systemd_dir / f"{UNIT}.service").read_text().splitlines()
            if row.startswith("ExecStart=")
        )
        argv = shlex.split(line.removeprefix("ExecStart="))
        assert argv[1] == "db"

        result = cli_runner.invoke(cli_app.cli, argv[1:])

        assert result.exit_code == 0, result.output + logged.getvalue()

    def test_under_the_ens_profile_the_timers_command_line_still_runs(
        self,
        cli_runner: CliRunner,
        engine: type[BaseDatabaseManager],
        systemd_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("noust.cli.audit_policy.security_profile", lambda: "ens-medium")
        invoke(cli_runner, "backup-schedule", "set", "shop", "-e", "postgresql", "--reason", "test")
        line = next(
            row
            for row in (systemd_dir / f"{UNIT}.service").read_text().splitlines()
            if row.startswith("ExecStart=")
        )
        argv = shlex.split(line.removeprefix("ExecStart="))

        without = cli_runner.invoke(
            cli_app.cli, ["db", "backup-run", "shop", "--engine", "postgresql"]
        )
        with_reason = cli_runner.invoke(cli_app.cli, argv[1:])

        assert without.exit_code == 2
        assert with_reason.exit_code == 0, with_reason.output


# ============================================================== dumps


class TestDumps:
    def test_a_backup_is_checked_and_says_how(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], logged: io.StringIO
    ) -> None:
        result = invoke(cli_runner, "backup", "shop", "-e", "postgresql")

        assert result.exit_code == 0, logged.getvalue()
        assert "Checked: 1 objects listed." in logged.getvalue()

    def test_a_backup_that_fails_its_check_exits_1(
        self,
        cli_runner: CliRunner,
        engine: type[BaseDatabaseManager],
        runner: RcloneRunner,
        logged: io.StringIO,
    ) -> None:
        runner.script(("pg_restore", "--list"), stderr="pg_restore: error: bad", exit_code=1)

        result = invoke(cli_runner, "backup", "shop", "-e", "postgresql")

        assert result.exit_code == 1
        assert "failed its check" in logged.getvalue()

    def test_a_backup_to_a_path_of_the_operators_choosing_is_only_written(
        self,
        cli_runner: CliRunner,
        engine: type[BaseDatabaseManager],
        tmp_path: Path,
        runner: RcloneRunner,
    ) -> None:
        destination = tmp_path / "out.dump"

        result = invoke(cli_runner, "backup", "shop", "-e", "postgresql", "-o", str(destination))

        assert result.exit_code == 0
        assert destination.read_bytes() == PG_ARCHIVE
        assert not runner.ran("pg_restore")

    def test_backups_lists_what_a_restore_can_be_named_from(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        take_dump(engine)

        result = invoke(cli_runner, "backups", "-e", "postgresql", "--json")

        assert json.loads(result.output)[0]["database"] == "shop"

    def test_backups_says_who_made_each_dump_and_whether_it_was_checked(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], runner: RcloneRunner
    ) -> None:
        add_destination(runner)
        invoke(cli_runner, "backup", "shop", "-e", "postgresql")
        name = next(iter(engine.BACKUP_DIR.iterdir())).name
        invoke(cli_runner, "backup-push", name, "-e", "postgresql", "--to", "nas")

        result = invoke(cli_runner, "backups", "-e", "postgresql")

        assert "Checked: ok (manual)" in result.output
        assert "Copy:    nas" in result.output

    def test_verify_prints_the_evidence_and_exits_0(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)

        result = invoke(cli_runner, "backup-verify", name, "-e", "postgresql")

        assert result.exit_code == 0, result.output
        assert "ok  pg_restore --list" in result.output
        assert "1 objects listed." in result.output

    def test_verify_of_a_bad_dump_exits_1_with_the_tools_words(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], runner: RcloneRunner
    ) -> None:
        name = take_dump(engine)
        runner.script(("pg_restore", "--list"), stderr="pg_restore: error: bad header", exit_code=1)

        result = invoke(cli_runner, "backup-verify", name, "-e", "postgresql")

        assert result.exit_code == 1
        assert "pg_restore: error: bad header" in result.output

    def test_verify_with_a_restore_test_shows_its_evidence(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)

        result = invoke(cli_runner, "backup-verify", name, "-e", "postgresql", "--restore-test")

        assert result.exit_code == 0, result.output
        assert "Restore test: ok" in result.output
        assert "dropped afterwards" in result.output

    def test_verify_as_json_is_the_listings_row(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)

        result = invoke(cli_runner, "backup-verify", name, "-e", "postgresql", "--json")

        data = json.loads(result.output)
        assert data["name"] == name
        assert data["verify_status"] == "ok"

    def test_delete_asks_first_and_removes_the_file(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)
        path = engine.BACKUP_DIR / name

        invoke(cli_runner, "backup-delete", name, "-e", "postgresql", input="n\n")
        assert path.exists()

        result = invoke(cli_runner, "backup-delete", name, "-e", "postgresql", "--force")

        assert result.exit_code == 0
        assert not path.exists()


# ================================================================ remote


class TestRemote:
    @pytest.fixture(autouse=True)
    def destination(self, runner: RcloneRunner) -> None:
        add_destination(runner)

    def test_push_sends_the_dump_and_says_how_it_was_checked(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], logged: io.StringIO
    ) -> None:
        name = take_dump(engine)

        result = invoke(cli_runner, "backup-push", name, "-e", "postgresql", "--to", "nas")

        assert result.exit_code == 0, logged.getvalue()
        assert f"{name} sent to nas, verified by md5" in logged.getvalue()

    def test_push_to_a_destination_that_does_not_exist_is_refused(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], logged: io.StringIO
    ) -> None:
        name = take_dump(engine)

        result = invoke(cli_runner, "backup-push", name, "-e", "postgresql", "--to", "nowhere")

        assert result.exit_code == 1
        assert "nowhere" in logged.getvalue()

    def test_remote_lists_databases_then_dumps(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)
        invoke(cli_runner, "backup-push", name, "-e", "postgresql", "--to", "nas")

        databases = invoke(cli_runner, "backup-remote", "-e", "postgresql", "--from", "nas")
        dumps = invoke(
            cli_runner, "backup-remote", "-e", "postgresql", "--from", "nas", "-d", "shop", "--json"
        )

        assert databases.output.split() == ["shop"]
        (dump,) = json.loads(dumps.output)
        assert dump["name"] == name
        assert dump["own"] is True

    def test_delete_from_a_destination_leaves_the_local_file(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], runner: RcloneRunner
    ) -> None:
        name = take_dump(engine)
        invoke(cli_runner, "backup-push", name, "-e", "postgresql", "--to", "nas")

        result = invoke(
            cli_runner,
            "backup-delete",
            name,
            "-e",
            "postgresql",
            "--from",
            "nas",
            "-d",
            "shop",
            "--force",
        )

        assert result.exit_code == 0
        folder = runner.root / "nas" / "wasm-backups" / "databases" / "postgresql" / "shop"
        assert list(folder.iterdir()) == []
        assert (engine.BACKUP_DIR / name).is_file()

    def test_delete_from_a_destination_needs_the_database(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager], logged: io.StringIO
    ) -> None:
        name = take_dump(engine)

        result = invoke(
            cli_runner, "backup-delete", name, "-e", "postgresql", "--from", "nas", "--force"
        )

        assert result.exit_code == 1
        assert "--database" in logged.getvalue()

    def test_restore_remote_brings_a_dump_back_as_a_new_database(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)
        invoke(cli_runner, "backup-push", name, "-e", "postgresql", "--to", "nas")
        (engine.BACKUP_DIR / name).unlink()

        result = invoke(
            cli_runner,
            "restore-remote",
            "shop",
            name,
            "-e",
            "postgresql",
            "--from",
            "nas",
            "--as-new",
            "shop_copy",
            "--force",
        )

        assert result.exit_code == 0, result.output
        assert ("load", "shop_copy", name) in engine.state["calls"]  # type: ignore[attr-defined]

    def test_restore_remote_asks_first_and_declining_changes_nothing(
        self, cli_runner: CliRunner, engine: type[BaseDatabaseManager]
    ) -> None:
        name = take_dump(engine)
        invoke(cli_runner, "backup-push", name, "-e", "postgresql", "--to", "nas")

        result = invoke(
            cli_runner,
            "restore-remote",
            "shop",
            name,
            "-e",
            "postgresql",
            "--from",
            "nas",
            "--drop",
            input="n\n",
        )

        assert result.exit_code == 0
        assert not [c for c in engine.state["calls"] if c[0] in ("load", "drop")]  # type: ignore[attr-defined]
