# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db backup-schedule set --all`` and the exclusions (3.4, checklist H-02).

The command line and the timer's unit: ``--all`` instead of ``*`` (which a
shell expands), the exclusions printed back, and the unit of the policy that
covers all databases never able to be a database's.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.commands import db as db_cli
from noust.core.runner import set_runner
from noust.core.store import NoustStore
from noust.managers.backup_scheduler import BackupScheduler
from noust.managers.database.base import BaseDatabaseManager
from tests.database_backup_support import PG_LISTING, RcloneRunner, make_engine

ALL_UNIT = "noust-backup-db-postgresql--all"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    fake.script(("systemctl", "show"), stdout="LoadState=loaded\n")
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "systemd"
    path.mkdir()
    monkeypatch.setattr(BackupScheduler, "SYSTEMD_DIR", path)
    return path


@pytest.fixture
def engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner, systemd_dir: Path
) -> type[BaseDatabaseManager]:
    (tmp_path / "dumps").mkdir()
    cls = make_engine(tmp_path / "dumps", databases=("shop", "blog"))
    monkeypatch.setattr(db_cli, "get_db_manager", lambda engine, verbose=False: cls())
    return cls


def invoke(*args: str) -> Any:
    return CliRunner().invoke(cli_app.cli, ["db", *args])


class TestSetAll:
    def test_all_installs_the_all_timer_that_runs_with_all_and_never_a_star(
        self, engine: type[BaseDatabaseManager], systemd_dir: Path
    ) -> None:
        result = invoke("backup-schedule", "set", "--all", "-e", "postgresql", "--json")

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["scope"] == "all"
        assert data["database"] == "*"
        service = (systemd_dir / f"{ALL_UNIT}.service").read_text()
        assert "db backup-run --all --engine postgresql" in service
        exec_line = next(line for line in service.splitlines() if line.startswith("ExecStart"))
        assert "*" not in exec_line
        assert (systemd_dir / f"{ALL_UNIT}.timer").is_file()

    def test_the_exclusions_are_kept_and_printed(
        self, engine: type[BaseDatabaseManager], systemd_dir: Path
    ) -> None:
        result = invoke(
            "backup-schedule",
            "set",
            "--all",
            "-e",
            "postgresql",
            "--exclude-database",
            "scratch",
            "--exclude-schema",
            "audit",
            "--exclude-table",
            "sessions",
        )

        assert result.exit_code == 0, result.output
        assert "every database of the engine" in result.output
        assert "databases: scratch" in result.output
        assert "schemas: audit" in result.output
        assert "tables or collections: sessions" in result.output

    def test_a_database_or_all_is_needed_but_not_both(
        self, engine: type[BaseDatabaseManager]
    ) -> None:
        neither = invoke("backup-schedule", "set", "-e", "postgresql")
        both = invoke("backup-schedule", "set", "shop", "--all", "-e", "postgresql")

        assert neither.exit_code != 0
        assert both.exit_code != 0

    def test_a_name_that_looks_like_an_option_is_refused(
        self, engine: type[BaseDatabaseManager]
    ) -> None:
        result = invoke(
            "backup-schedule", "set", "shop", "-e", "postgresql", "--exclude-table=--help"
        )

        assert result.exit_code != 0

    def test_show_and_remove_take_all_too(
        self, engine: type[BaseDatabaseManager], systemd_dir: Path
    ) -> None:
        invoke("backup-schedule", "set", "--all", "-e", "postgresql")

        shown = invoke("backup-schedule", "show", "--all", "-e", "postgresql", "--json")
        removed = invoke("backup-schedule", "remove", "--all", "-e", "postgresql", "--force")

        assert json.loads(shown.output)[0]["scope"] == "all"
        assert removed.exit_code == 0, removed.output
        assert not (systemd_dir / f"{ALL_UNIT}.timer").exists()

    def test_run_all_dumps_every_database(
        self, engine: type[BaseDatabaseManager], systemd_dir: Path
    ) -> None:
        invoke("backup-schedule", "set", "--all", "-e", "postgresql")

        result = invoke("backup-run", "--all", "-e", "postgresql", "--json")

        assert result.exit_code == 0, result.output
        assert set(json.loads(result.output)["databases"]) == {"shop", "blog"}
