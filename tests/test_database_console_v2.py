# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The SQL console, second version (noust.managers.database.console).

What is defended: the statement timeout reaches the server (PGOPTIONS, SET
SESSION) and never argv; a NULL and an empty string stay apart; EXPLAIN is
read-only unless ANALYZE, which runs as the superuser and is rolled back;
history and saved queries belong to one operator and are scrubbed before
they are kept; an export is a read, never a write run twice; a write is
audited with its text, scrubbed.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator

import pytest

from noust.core import audit
from noust.core.audit import Actor
from noust.core.config import REDACTED
from noust.core.exceptions import (
    DatabaseExistsError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    ValidationError,
)
from noust.managers.database.console import (
    HISTORY_LIMIT,
    QueryConsole,
    QueryRecords,
    history_owner,
)
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import NULL_MARKER, PostgresManager
from tests.database_data_support import SqlRunner, StubConfig, installed_sql_runner, make_service


@pytest.fixture
def sql_runner() -> Iterator[SqlRunner]:
    """
    Yields:
        The SQL runner, installed over a fresh store.
    """
    yield from installed_sql_runner()


@pytest.fixture
def postgres(sql_runner: SqlRunner) -> PostgresManager:
    """
    Args:
        sql_runner: The runner.

    Returns:
        A running PostgreSQL manager.
    """
    manager = PostgresManager()
    manager.config = StubConfig()
    return manager


@pytest.fixture
def console(postgres: PostgresManager) -> QueryConsole:
    """
    Args:
        postgres: The manager.

    Returns:
        A console for the operator ``user:7``.
    """
    service = make_service(postgres)
    service.actor = Actor(kind="user", id="7", name="ada")
    return QueryConsole(service)


def test_the_operator_is_the_account_else_the_credential() -> None:
    assert history_owner(Actor(kind="user", id="7", name="ada")) == "user:7"
    assert history_owner(Actor(kind="token", id="ci", name="token:ci")) == "token:ci"
    assert history_owner(Actor(kind="fleet", id="central", name="bob")) == "fleet:bob"
    assert history_owner(Actor(kind="master", name="master")) == "master:master"
    assert history_owner(None) == "system"


# ============================================================ running


def test_a_read_carries_its_timeout_in_the_environment(
    console: QueryConsole, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("SELECT n", "n\n1\n")

    result = console.run("postgresql", "shop", "SELECT n FROM t", timeout_s=5)

    assert result.rows == [["1"]]
    index = next(i for i, call in enumerate(sql_runner.calls) if "SELECT n FROM t" in call)
    env = sql_runner.envs[index] or {}
    assert env["PGOPTIONS"] == "-c default_transaction_read_only=on -c statement_timeout=5000"
    assert not any("statement_timeout" in arg for arg in sql_runner.calls[index])


def test_a_write_carries_its_timeout_too(console: QueryConsole, sql_runner: SqlRunner) -> None:
    console.run("postgresql", "shop", "DELETE FROM t", mode="write", timeout_s=120)

    index = next(i for i, call in enumerate(sql_runner.calls) if "DELETE FROM t" in call)
    assert (sql_runner.envs[index] or {})["PGOPTIONS"] == "-c statement_timeout=120000"


def test_null_and_empty_string_stay_apart(console: QueryConsole, sql_runner: SqlRunner) -> None:
    sql_runner.answer("SELECT a", f'a,b\n{NULL_MARKER},""\n')

    result = console.run("postgresql", "shop", "SELECT a, b FROM t")

    assert result.rows == [[None, ""]]
    assert NULL_MARKER not in result.output


def test_an_unsupported_timeout_is_refused(console: QueryConsole) -> None:
    with pytest.raises(ValidationError, match="timeout"):
        console.run("postgresql", "shop", "SELECT 1", timeout_s=3600)


def test_read_mode_still_takes_one_statement(console: QueryConsole, sql_runner: SqlRunner) -> None:
    with pytest.raises(DatabaseQueryError, match="one statement"):
        console.run("postgresql", "shop", "SELECT 1; COMMIT; DROP TABLE t")

    assert not sql_runner.calls_carrying("DROP TABLE")


def test_mysql_bounds_the_statement_in_the_session(sql_runner: SqlRunner) -> None:
    sql_runner.only_knows("mysql")
    sql_runner.answer("SCHEMA_NAME", "shop\n")
    sql_runner.answer("SELECT a", "a\tb\nNULL\t\n")
    manager = MySQLManager()
    manager.config = StubConfig()

    result = QueryConsole(make_service(manager), owner="cli:root").run(
        "mysql", "shop", "SELECT a, b FROM t", timeout_s=30
    )

    assert result.rows == [[None, ""]]
    script = sql_runner.sent("SELECT a, b FROM t")[-1]
    assert script.startswith(
        "SET SESSION max_execution_time = 30000;\nSTART TRANSACTION READ ONLY;"
    )


def test_mariadb_bounds_every_statement_in_seconds(sql_runner: SqlRunner) -> None:
    sql_runner.only_knows("mysql", "mariadb")
    manager = MySQLManager()

    assert manager.statement_timeout_sql(5) == "SET SESSION max_statement_time = 5;\n"


# ============================================================ audit


def test_a_read_is_audited_by_digest(console: QueryConsole, sql_runner: SqlRunner) -> None:
    console.run("postgresql", "shop", "SELECT secret FROM t")

    entry = audit.get_log().read(action="db.query.read")[0]
    assert "statement" not in entry["details"]
    assert len(entry["details"]["statement_sha256"]) == 64


def test_a_write_is_audited_with_its_text_scrubbed(
    console: QueryConsole, sql_runner: SqlRunner
) -> None:
    console.run(
        "postgresql", "shop", "ALTER ROLE app PASSWORD 'hunter22'", mode="write", timeout_s=30
    )

    entry = audit.get_log().read(action="db.query")[0]
    assert entry["details"]["statement"].startswith("ALTER ROLE app PASSWORD")
    assert "hunter22" not in json.dumps(entry)


# ============================================================ history


def test_every_statement_lands_in_the_operators_history_scrubbed(
    console: QueryConsole, sql_runner: SqlRunner
) -> None:
    console.run("postgresql", "shop", "SELECT 1")
    console.run("postgresql", "shop", "ALTER ROLE x PASSWORD 's3cr3t-pass'", mode="write")
    sql_runner.answer(
        "SELECT broken", stderr='ERROR:  column "broken" does not exist\n', exit_code=1
    )
    with pytest.raises(DatabaseQueryError):
        console.run("postgresql", "shop", "SELECT broken")

    entries = console.records.history(engine="postgresql", database="shop")

    assert [entry.statement for entry in entries][1:] == [
        f"ALTER ROLE x PASSWORD '{REDACTED}'",
        "SELECT 1",
    ]
    assert entries[0].outcome == "failure"
    assert "does not exist" in (entries[0].error or "")
    assert entries[1].mode == "write"


def test_history_belongs_to_one_operator(console: QueryConsole, sql_runner: SqlRunner) -> None:
    console.run("postgresql", "shop", "SELECT 1")

    other = QueryRecords(console.service.store, "user:8")

    assert other.history() == []
    assert other.clear() == 0
    assert len(console.records.history()) == 1


def test_history_keeps_the_newest_only(console: QueryConsole) -> None:
    records = console.records
    for number in range(HISTORY_LIMIT + 5):
        records.add(engine="postgresql", database="shop", statement=f"SELECT {number}", mode="read")

    entries = records.history(limit=HISTORY_LIMIT + 5)

    assert len(entries) == HISTORY_LIMIT
    assert entries[0].statement == f"SELECT {HISTORY_LIMIT + 4}"


# ============================================================ saved queries


def test_saved_queries_are_per_operator_and_named_once(console: QueryConsole) -> None:
    records = console.records
    saved = records.save(
        name="Top users", engine="postgresql", database="shop", statement="SELECT 1"
    )

    assert records.saved(engine="postgresql", database="shop")[0].name == "Top users"
    with pytest.raises(DatabaseExistsError):
        records.save(name="Top users", engine="postgresql", database="shop", statement="SELECT 2")
    changed = records.save(
        name="Top users v2",
        engine="postgresql",
        database=None,
        statement="SELECT 2",
        saved_id=saved.id,
    )
    assert changed.database == ""
    assert records.saved(database="other")[0].id == saved.id

    other = QueryRecords(console.service.store, "user:8")
    with pytest.raises(DatabaseNotFoundError):
        other.get(saved.id)
    assert not other.delete(saved.id)
    assert records.delete(saved.id)


def test_a_saved_query_is_scrubbed(console: QueryConsole) -> None:
    saved = console.records.save(
        name="rotate",
        engine="postgresql",
        database="shop",
        statement="ALTER USER app IDENTIFIED BY 'letmein99'",
    )

    assert "letmein99" not in saved.statement


def test_a_saved_query_needs_a_name(console: QueryConsole) -> None:
    with pytest.raises(ValidationError):
        console.records.save(name="  ", engine="postgresql", database="shop", statement="SELECT 1")


# ============================================================ EXPLAIN


PLAN = '[{"Plan": {"Node Type": "Seq Scan", "Relation Name": "t"}}]'


def test_explain_is_read_only_and_parsed(console: QueryConsole, sql_runner: SqlRunner) -> None:
    sql_runner.answer("EXPLAIN (FORMAT JSON)", PLAN)

    result = console.explain("postgresql", "shop", "SELECT * FROM t")

    assert result.format == "json"
    assert result.plan[0]["Plan"]["Node Type"] == "Seq Scan"
    call = sql_runner.calls_carrying("EXPLAIN (FORMAT JSON)")[-1]
    assert call[0] == "psql" and "-U" in call
    assert ["BEGIN READ ONLY", "COMMIT"] == [call[i + 1] for i, a in enumerate(call) if a == "-c"][
        ::2
    ]


def test_explain_analyze_runs_as_the_superuser_and_rolls_back(
    console: QueryConsole, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("EXPLAIN (ANALYZE", PLAN)

    result = console.explain("postgresql", "shop", "DELETE FROM t", analyze=True)

    assert result.analyze
    call = sql_runner.calls_carrying("EXPLAIN (ANALYZE")[-1]
    assert call[:5] == ("runuser", "-u", "postgres", "--", "psql")
    commands = [call[i + 1] for i, arg in enumerate(call) if arg == "-c"]
    assert commands[0] == "BEGIN" and commands[-1] == "ROLLBACK"
    assert audit.get_log().read(action="db.query")[0]["details"]["kind"] == "explain_analyze"


def test_explain_refuses_a_meta_command(console: QueryConsole) -> None:
    with pytest.raises(DatabaseQueryError):
        console.explain("postgresql", "shop", "\\! id")


# ============================================================ export


def test_an_export_is_a_read_rendered_as_csv(console: QueryConsole, sql_runner: SqlRunner) -> None:
    sql_runner.answer("SELECT id", f'id,note\n1,"a,b"\n2,{NULL_MARKER}\n')

    export = console.export("postgresql", "shop", "SELECT id, note FROM t", fmt="csv")

    rows = list(csv.reader(io.StringIO(export.content)))
    assert rows == [["id", "note"], ["1", "a,b"], ["2", ""]]
    assert export.filename.startswith("postgresql-shop-") and export.filename.endswith(".csv")
    call = sql_runner.calls_carrying("SELECT id, note FROM t")[-1]
    assert "-U" in call
    assert audit.get_log().read(action="db.query.export")


def test_an_export_as_json_keeps_nulls_and_repeated_names(
    console: QueryConsole, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("SELECT id", f"id,id\n1,{NULL_MARKER}\n")

    export = console.export("postgresql", "shop", "SELECT id, id FROM t", fmt="json")

    assert json.loads(export.content) == [{"id": "1", "id_2": None}]


def test_an_export_cannot_be_a_second_statement(console: QueryConsole) -> None:
    with pytest.raises(DatabaseQueryError):
        console.export("postgresql", "shop", "SELECT 1; DELETE FROM t", fmt="csv")


def test_an_unknown_export_format_is_refused(console: QueryConsole) -> None:
    with pytest.raises(ValidationError):
        console.export("postgresql", "shop", "SELECT 1", fmt="xlsx")
