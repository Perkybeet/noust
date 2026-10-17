# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db import`` (3.4, checklist H-18).

The command line of the import the console does through the API: any file on
the server, what it is read from its bytes, the same safety copy, and nothing
changed when the file is not a dump the engine can load.
"""

from __future__ import annotations

import bz2
import io
import json
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
from noust.managers.database.base import BaseDatabaseManager
from tests.database_backup_support import PG_LISTING, RcloneRunner, make_engine

SQL = b"-- PostgreSQL database dump\nSELECT 1;\n-- PostgreSQL database dump complete\n"


@pytest.fixture(autouse=True)
def _reset_store() -> Iterator[None]:
    NoustStore.reset_instance()
    yield
    NoustStore.reset_instance()


@pytest.fixture
def runner(tmp_path: Path) -> Iterator[RcloneRunner]:
    fake = RcloneRunner(tmp_path / "remote")
    fake.script(("pg_restore", "--list"), stdout=PG_LISTING)
    set_runner(fake)
    try:
        yield fake
    finally:
        set_runner(None)


@pytest.fixture(autouse=True)
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from noust.core.config import Config

    backups = tmp_path / "backups"
    monkeypatch.setattr(Config, "backup_directory", property(lambda self: backups), raising=False)
    return backups


@pytest.fixture
def engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: RcloneRunner
) -> type[BaseDatabaseManager]:
    (tmp_path / "dumps").mkdir()
    cls = make_engine(tmp_path / "dumps", suffix=".sql", content=SQL, databases=("shop",))
    monkeypatch.setattr(db_cli, "get_db_manager", lambda engine, verbose=False: cls())
    return cls


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    buffer = io.StringIO()

    def factory(**kwargs: Any) -> Logger:
        return Logger(stream=buffer, **kwargs)

    monkeypatch.setattr(cli_app, "Logger", factory)
    monkeypatch.setattr(db_cli, "Logger", factory)
    return buffer


def invoke(*args: str) -> Any:
    return CliRunner().invoke(cli_app.cli, ["db", "import", *args])


def dump(tmp_path: Path, name: str = "export.sql", data: bytes = SQL) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


class TestImportCommand:
    def test_a_new_database_is_created_from_the_file(
        self, engine: type[BaseDatabaseManager], tmp_path: Path, logged: io.StringIO
    ) -> None:
        result = invoke("fresh", str(dump(tmp_path)), "-e", "postgresql", "--force")

        assert result.exit_code == 0, result.output + logged.getvalue()
        assert "fresh" in engine.state["dbs"]
        assert "created from export.sql" in logged.getvalue()

    def test_json_is_the_outcome(self, engine: type[BaseDatabaseManager], tmp_path: Path) -> None:
        result = invoke("fresh", str(dump(tmp_path)), "-e", "postgresql", "--force", "--json")

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["created"] is True
        assert data["format"]["kind"] == "sql"

    def test_a_compressed_dump_is_read_by_what_it_is(
        self, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        path = dump(tmp_path, "export.sql.bz2", bz2.compress(SQL))

        result = invoke("fresh", str(path), "-e", "postgresql", "--force")

        assert result.exit_code == 0, result.output
        # bzip2 is decompressed for the engine, which reads plain SQL.
        assert ("load", "fresh", "import.sql") in engine.state["calls"]

    def test_replace_takes_the_safety_copy_first(
        self, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        result = invoke(
            "shop", str(dump(tmp_path)), "-e", "postgresql", "--replace", "--force", "--json"
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["replaced"] is True
        assert data["safety_copy"]
        assert [call[0] for call in engine.state["calls"]] == ["backup", "drop", "create", "load"]

    def test_a_file_that_is_not_a_dump_changes_nothing(
        self, engine: type[BaseDatabaseManager], tmp_path: Path, logged: io.StringIO
    ) -> None:
        path = dump(tmp_path, "x.sql", b"root:$6$salt:19000:0:99999:7:::\n")

        result = invoke("shop", str(path), "-e", "postgresql", "--force")

        assert result.exit_code == 1
        assert engine.state["calls"] == []
        assert "root:" not in logged.getvalue() + result.output

    def test_a_name_that_is_not_a_dump_name_is_refused_with_what_is_allowed(
        self, engine: type[BaseDatabaseManager], tmp_path: Path, logged: io.StringIO
    ) -> None:
        result = invoke("shop", str(dump(tmp_path, "export.bak")), "-e", "postgresql", "--force")

        assert result.exit_code == 1
        assert ".sql" in logged.getvalue()

    def test_inspect_reads_and_changes_nothing(
        self, engine: type[BaseDatabaseManager], tmp_path: Path, logged: io.StringIO
    ) -> None:
        result = invoke("shop", str(dump(tmp_path)), "-e", "postgresql", "--inspect")

        assert result.exit_code == 0, result.output
        assert "plain SQL" in logged.getvalue()
        assert engine.state["calls"] == []

    def test_declining_the_question_does_nothing(
        self, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        result = CliRunner().invoke(
            cli_app.cli,
            ["db", "import", "shop", str(dump(tmp_path)), "-e", "postgresql"],
            input="n\n",
        )

        assert result.exit_code == 0
        assert engine.state["calls"] == []

    def test_keep_owners_reaches_the_loader(
        self, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        original = engine._load_backup
        seen: list[dict[str, Any]] = []

        def spy(self: Any, database: str, backup_path: Path, **kwargs: Any) -> None:
            seen.append(kwargs)
            original(self, database, backup_path, **kwargs)

        engine._load_backup = spy  # type: ignore[method-assign]

        invoke("shop", str(dump(tmp_path)), "-e", "postgresql", "--force", "--keep-owners")

        assert seen[0]["keep_owners"] is True

    def test_the_superuser_is_not_what_an_import_runs_as_by_default(
        self, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        seen: list[dict[str, Any]] = []
        original = engine._load_backup

        def spy(self: Any, database: str, backup_path: Path, **kwargs: Any) -> None:
            seen.append(kwargs)
            original(self, database, backup_path, **kwargs)

        engine._load_backup = spy  # type: ignore[method-assign]

        invoke("shop", str(dump(tmp_path)), "-e", "postgresql", "--force")

        assert "as_superuser" not in seen[0]

    def test_as_superuser_asks_a_second_question_and_a_no_loads_nothing(
        self, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        result = CliRunner().invoke(
            cli_app.cli,
            ["db", "import", "shop", str(dump(tmp_path)), "-e", "postgresql", "--as-superuser"],
            input="y\nn\n",
        )

        assert result.exit_code == 0
        assert "superuser" in result.output
        assert engine.state["calls"] == []

    def test_as_superuser_confirmed_reaches_the_loader(
        self, engine: type[BaseDatabaseManager], tmp_path: Path
    ) -> None:
        seen: list[dict[str, Any]] = []
        original = engine._load_backup

        def spy(self: Any, database: str, backup_path: Path, **kwargs: Any) -> None:
            seen.append(kwargs)
            original(self, database, backup_path, **kwargs)

        engine._load_backup = spy  # type: ignore[method-assign]

        invoke("shop", str(dump(tmp_path)), "-e", "postgresql", "--force", "--as-superuser")

        assert seen[0]["as_superuser"] is True

    def test_only_inspecting_is_read_only_for_the_audit_policy(self) -> None:
        command = db_cli.cli.commands["import"]

        assert is_read_only(command, "db import", {"inspect_only": True}) is True
        assert is_read_only(command, "db import", {"inspect_only": False}) is False
