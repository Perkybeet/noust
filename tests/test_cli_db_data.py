# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db tables/describe/rows/row/keys/key/explain/export/history/saved/metrics``.

The CLI is the other client of the explorer, the console and the metrics:
these tests pin that the commands reach the same classes the API does, that
reads print NULL apart from an empty string, that edits ask first, and that
errors end at the CLI's boundary with the engine's own words.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.commands import db as db_cli
from noust.managers.database.postgres import NULL_MARKER, PostgresManager
from tests.database_data_support import SqlRunner, StubConfig, installed_sql_runner


@pytest.fixture
def sql_runner() -> Iterator[SqlRunner]:
    """
    Yields:
        The SQL runner, installed over a fresh store.
    """
    yield from installed_sql_runner()


CATALOG = json.dumps(
    {"schemas": ["public"], "relations": [{"schema": "public", "name": "users", "kind": "table"}]}
)
STRUCTURE = json.dumps(
    {
        "columns": [
            {"name": "id", "type": "integer", "base": "int4", "category": "N", "nullable": False},
            {"name": "email", "type": "text", "base": "text", "category": "S"},
        ],
        "primary_key": ["id"],
    }
)
PAGE = json.dumps({"rows": [[[1, None], [False], ["1"]], [[2, ""], [False], ["2"]]]})


@pytest.fixture
def cli(sql_runner: SqlRunner, monkeypatch: pytest.MonkeyPatch) -> SqlRunner:
    """
    Resolve ``postgresql`` to a real manager over the SQL runner.

    Args:
        sql_runner: The runner.
        monkeypatch: Patching helper.

    Returns:
        The runner.
    """
    sql_runner.answer("'relations'", CATALOG)
    sql_runner.answer("'primary_key'", STRUCTURE)
    sql_runner.answer("'rows', coalesce", PAGE)

    def get(name: str, verbose: bool = False) -> Any:
        if name in ("postgresql", "pg"):
            manager = PostgresManager()
            manager.config = StubConfig()
            return manager
        return None

    monkeypatch.setattr(db_cli, "get_db_manager", get)
    return sql_runner


def run(*args: str, input: str | None = None) -> Any:
    """
    Args:
        *args: ``noust db`` arguments.
        input: What the operator types.

    Returns:
        The Click result.
    """
    return CliRunner().invoke(cli_app.cli, ["db", *args], input=input)


def test_tables_lists_the_catalog(cli: SqlRunner) -> None:
    result = run("tables", "shop", "-e", "postgresql", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["name"] == "users"


def test_rows_print_null_apart_from_an_empty_string(cli: SqlRunner) -> None:
    result = run("rows", "shop", "users", "-e", "postgresql", "--filter", "id:gt:0")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["id", "email"]
    assert lines[1].split() == ["1", "NULL"]
    assert lines[2].split() == ["2"]


def test_a_bad_filter_ends_at_the_boundary(cli: SqlRunner) -> None:
    result = run("rows", "shop", "users", "-e", "postgresql", "--filter", "id:drop:1")

    assert result.exit_code == 1
    assert "Unknown filter operator" in result.output


def test_an_edit_asks_first(cli: SqlRunner) -> None:
    result = run(
        "row",
        "update",
        "shop",
        "users",
        "-e",
        "postgresql",
        "--key",
        "id=1",
        "--set",
        "email=x",
        input="n\n",
    )

    assert result.exit_code == 1
    assert not cli.sent("UPDATE")


def test_an_edit_with_force_changes_one_row(cli: SqlRunner) -> None:
    cli.answer(
        "UPDATE", json.dumps({"before": {"id": 1, "email": None}, "after": {"id": 1, "email": "x"}})
    )

    result = run(
        "row",
        "update",
        "shop",
        "users",
        "-e",
        "postgresql",
        "--key",
        "id=1",
        "--set",
        "email=x",
        "--force",
        "--json",
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["after"]["email"] == "x"


def test_typed_values_use_colon_equals(cli: SqlRunner) -> None:
    cli.answer("INSERT INTO", json.dumps({"before": None, "after": {"id": 3, "email": None}}))

    result = run(
        "row",
        "insert",
        "shop",
        "users",
        "-e",
        "postgresql",
        "--set",
        "email:=null",
        "--force",
    )

    assert result.exit_code == 0, result.output
    assert '("email") VALUES (NULL)' in cli.sent("INSERT INTO")[-1]


def test_explain_prints_the_plan(cli: SqlRunner) -> None:
    cli.answer("EXPLAIN (FORMAT JSON)", '[{"Plan": {"Node Type": "Seq Scan"}}]')

    result = run("explain", "shop", "SELECT 1", "-e", "postgresql")

    assert result.exit_code == 0, result.output
    assert "Seq Scan" in result.output


def test_export_prints_csv_and_lands_in_history(cli: SqlRunner) -> None:
    cli.answer("SELECT id", f"id,email\n1,{NULL_MARKER}\n")

    exported = run("export", "shop", "SELECT id, email FROM users", "-e", "postgresql")
    listed = run("history", "--json")

    assert exported.exit_code == 0, exported.output
    assert exported.output == "id,email\n1,\n"
    assert json.loads(listed.output)[0]["kind"] == "export"


def test_saved_queries_round_trip(cli: SqlRunner) -> None:
    added = run("saved", "add", "top", "SELECT 1", "-e", "postgresql", "-d", "shop")
    listed = run("saved", "list", "--json")
    saved_id = json.loads(listed.output)[0]["id"]
    removed = run("saved", "remove", str(saved_id))

    assert added.exit_code == 0, added.output
    assert json.loads(listed.output)[0]["name"] == "top"
    assert removed.exit_code == 0, removed.output
    assert json.loads(run("saved", "list", "--json").output) == []


def test_slow_queries_explain_how_to_enable_them(cli: SqlRunner) -> None:
    cli.answer("'preloaded'", json.dumps({"schema": None, "preloaded": False}))

    result = run("slow-queries", "shop", "-e", "postgresql")

    assert result.exit_code == 0, result.output
    assert "shared_preload_libraries" in result.output
