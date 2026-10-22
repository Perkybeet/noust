# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db globals``: the roles of a PostgreSQL server for a dump restored by hand (checklist H-28).

The command parses and prints; the manager decides. What is pinned: dumping writes where it is told
and says the file is private, restoring asks first and says exactly what it created, what it left
out and what the server refused (a refusal exits non-zero), and ``--json`` is JSON alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.commands import db_globals
from noust.core.exceptions import DatabaseBackupError
from noust.managers.database.postgres import PostgresManager


class FakeManager(PostgresManager):
    """A PostgreSQL manager that does nothing to a server."""

    def __init__(self) -> None:
        self.dumped: list[Path | None] = []
        self.restored: list[Path] = []
        self.refuse: Exception | None = None
        self.errors: tuple[str, ...] = ()

    def backup_globals(self, output_path: Path | None = None, compress: bool = True) -> Any:
        if self.refuse:
            raise self.refuse
        self.dumped.append(output_path)
        return SimpleNamespace(path=output_path or Path("/var/backups/g.sql.gz"), size=321)

    def list_globals(self) -> list[Path]:
        return [Path("/var/backups/noust/databases/globals/postgresql-globals-1.sql.gz")]

    def restore_globals(self, source: Path) -> Any:
        if self.refuse:
            raise self.refuse
        self.restored.append(source)
        return SimpleNamespace(
            created=("shopper",),
            present=("postgres",),
            left_out=("tablespace fast: it needs its directory",),
            errors=self.errors,
        )


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> FakeManager:
    fake = FakeManager()
    monkeypatch.setattr(db_globals, "_manager", lambda ctx: fake)
    return fake


def invoke(*args: str, input: str | None = None) -> Any:
    return CliRunner().invoke(cli_app.cli, ["db", "globals", *args], input=input)


def test_dump_writes_where_it_is_told_and_says_the_file_is_private(
    manager: FakeManager, tmp_path: Path
) -> None:
    target = tmp_path / "roles.sql"

    result = invoke("dump", "--output", str(target))

    assert result.exit_code == 0, result.output
    assert manager.dumped == [target]
    assert "password hashes" in result.output


def test_dump_without_a_path_uses_the_backup_directory(manager: FakeManager) -> None:
    result = invoke("dump", "--json")

    assert json.loads(result.output) == {"path": "/var/backups/g.sql.gz", "size": 321}
    assert manager.dumped == [None]


def test_a_failing_pg_dumpall_exits_1_with_its_words(manager: FakeManager) -> None:
    manager.refuse = DatabaseBackupError("Failed to back up", details="connection refused")

    result = invoke("dump")

    assert result.exit_code == 1 and "connection refused" in result.output


def test_list_names_the_files(manager: FakeManager) -> None:
    result = invoke("list")

    assert "postgresql-globals-1.sql.gz" in result.output


def test_restore_asks_first_and_does_nothing_when_the_answer_is_no(
    manager: FakeManager, tmp_path: Path
) -> None:
    source = tmp_path / "roles.sql"
    source.write_text("CREATE ROLE shopper;\n")

    result = invoke("restore", str(source), input="n\n")

    assert result.exit_code == 0 and manager.restored == []


def test_restore_says_what_it_created_left_out_and_found_there(
    manager: FakeManager, tmp_path: Path
) -> None:
    source = tmp_path / "roles.sql"
    source.write_text("CREATE ROLE shopper;\n")

    result = invoke("restore", str(source), "--yes")

    assert result.exit_code == 0, result.output
    assert manager.restored == [source]
    assert "Created: shopper" in result.output
    assert "Left out: tablespace fast" in result.output


def test_a_statement_the_server_refused_is_a_warning_and_a_failed_exit(
    manager: FakeManager, tmp_path: Path
) -> None:
    source = tmp_path / "roles.sql"
    source.write_text("CREATE ROLE shopper;\n")
    manager.errors = ('role "pg_read_all_data" does not exist',)

    result = invoke("restore", str(source), "--yes")

    assert result.exit_code == 1
    assert 'role "pg_read_all_data" does not exist' in result.output


def test_restore_as_json_is_json_alone(manager: FakeManager, tmp_path: Path) -> None:
    source = tmp_path / "roles.sql"
    source.write_text("CREATE ROLE shopper;\n")

    result = invoke("restore", str(source), "--yes", "--json")

    data = json.loads(result.output)
    assert data["created"] == ["shopper"] and data["present"] == ["postgres"]
