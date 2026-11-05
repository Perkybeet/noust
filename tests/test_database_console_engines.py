# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which engines have a query console, and what stops a statement that executes.

What is defended: the SQL console exists for the engines that speak SQL and
for no other (a Redis or MongoDB database that was sent a statement would run
it as the engine's administrator, with nothing in the way), and a statement
that runs what it explains (``EXPLAIN ANALYZE``) is stopped by the engine,
not by a word the console looks for: in read mode it is one more statement of
the read-only account, in a session the server itself holds read-only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import DatabaseQueryError, ValidationError
from noust.managers.database.console import QueryConsole
from noust.managers.database.mongodb import MongoDBManager
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.redis import RedisManager
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.support.database_data_support import (
    SqlRunner,
    StubConfig,
    installed_sql_runner,
    make_service,
)


@pytest.fixture
def sql_runner() -> Iterator[SqlRunner]:
    """
    Yields:
        The SQL runner, installed over a fresh store.
    """
    yield from installed_sql_runner()


def postgres() -> PostgresManager:
    """
    Returns:
        A PostgreSQL manager over the runner.
    """
    manager = PostgresManager()
    manager.config = StubConfig()
    return manager


# ============================================================ which engines have a console


@pytest.mark.parametrize(
    "manager_class",
    [RedisManager, MongoDBManager],
    ids=["redis", "mongodb"],
)
@pytest.mark.parametrize("mode", ["read", "write"])
def test_an_engine_without_sql_has_no_console(
    manager_class: type, mode: str, sql_runner: SqlRunner
) -> None:
    manager = manager_class()
    console = QueryConsole(make_service(manager))

    with pytest.raises(ValidationError, match="no query console") as caught:
        console.run(manager.ENGINE_NAME, "0", "FLUSHALL", mode=mode)

    # The way to what the engine does have is in the refusal, not left to be guessed.
    assert caught.value.details
    assert not sql_runner.calls_carrying("FLUSHALL")


@pytest.mark.parametrize("manager_class", [RedisManager, MongoDBManager], ids=["redis", "mongodb"])
def test_a_plan_and_an_export_need_the_console_too(
    manager_class: type, sql_runner: SqlRunner
) -> None:
    manager = manager_class()
    console = QueryConsole(make_service(manager))

    with pytest.raises(ValidationError, match="no query console"):
        console.explain(manager.ENGINE_NAME, "0", "GET a")
    with pytest.raises(ValidationError, match="no query console"):
        console.export(manager.ENGINE_NAME, "0", "GET a", fmt="csv")


def test_the_sql_engines_keep_theirs(sql_runner: SqlRunner) -> None:
    sql_runner.answer("SELECT n FROM t", "n\n1\n")

    result = QueryConsole(make_service(postgres())).run("postgresql", "shop", "SELECT n FROM t")

    assert result.rows == [["1"]]


# ============================================================ the same, over HTTP


@pytest.fixture
def wired(sql_runner: SqlRunner, monkeypatch: pytest.MonkeyPatch) -> SqlRunner:
    """
    Stand PostgreSQL and Redis behind the service.

    Args:
        sql_runner: The runner.
        monkeypatch: Patching helper.

    Returns:
        The runner.
    """
    import noust.managers.database.service as db_service

    sql_runner.only_knows("psql", "redis-cli")

    def get(name: str, verbose: bool = False) -> Any:
        if name in ("postgresql", "pg", "postgres"):
            return postgres()
        if name == "redis":
            return RedisManager()
        return None

    monkeypatch.setattr(db_service, "get_db_manager", get)
    monkeypatch.setattr(
        db_service,
        "DatabaseRegistry",
        SimpleNamespace(list_engines=lambda: ["postgresql", "redis"]),
    )
    return sql_runner


@pytest.fixture
def client(tmp_path: Path, wired: SqlRunner) -> TestClient:
    """
    Args:
        tmp_path: Per-test temporary directory.
        wired: The wired engines.

    Returns:
        A master-token session in sudo mode, carrying the CSRF header.
    """
    app: FastAPI = create_app(
        SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000)
    )
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    master = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": master})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    assert signed_in.post("/api/auth/elevate", json={"token": master}).status_code == 200
    return signed_in


@pytest.mark.parametrize("path", ["/query", "/query/explain", "/query/export"])
@pytest.mark.parametrize("mode", ["read", "write"])
def test_the_api_runs_nothing_on_redis(
    client: TestClient, wired: SqlRunner, path: str, mode: str
) -> None:
    body: dict[str, Any] = {"engine": "redis", "database": "0", "query": "FLUSHALL"}
    if path == "/query":
        body["mode"] = mode

    response = client.post(f"/api/databases{path}", json=body)

    assert response.status_code == 400, response.text
    assert "no query console" in json.dumps(response.json())
    assert not wired.calls_carrying("FLUSHALL")


# ============================================================ what runs what it explains


def test_a_typed_explain_analyze_is_a_read_of_the_read_only_account(
    sql_runner: SqlRunner,
) -> None:
    sql_runner.answer("EXPLAIN ANALYZE", "n\n1\n")
    console = QueryConsole(make_service(postgres()))

    console.run("postgresql", "shop", "EXPLAIN ANALYZE DELETE FROM t")

    call = sql_runner.calls_carrying("EXPLAIN ANALYZE DELETE FROM t")[-1]
    index = sql_runner.calls.index(call)
    # Never the superuser, and the session the server holds read-only: what stops the delete is the engine.
    assert call[0] == "psql" and "-U" in call
    assert call[call.index("-U") + 1].startswith("noust_ro_")
    assert "runuser" not in call
    options = (sql_runner.envs[index] or {}).get("PGOPTIONS", "")
    assert "default_transaction_read_only=on" in options


def test_the_engines_own_refusal_of_a_typed_explain_analyze_reaches_the_operator(
    sql_runner: SqlRunner,
) -> None:
    sql_runner.answer(
        "EXPLAIN ANALYZE DELETE",
        stderr="ERROR:  cannot execute DELETE in a read-only transaction\n",
        exit_code=1,
    )
    console = QueryConsole(make_service(postgres()))

    with pytest.raises(DatabaseQueryError) as caught:
        console.run("postgresql", "shop", "EXPLAIN ANALYZE DELETE FROM t")

    assert "read-only transaction" in (caught.value.details or "")


def test_the_console_has_no_word_list_that_decides_what_a_read_may_start_with(
    sql_runner: SqlRunner,
) -> None:
    sql_runner.answer("EXPLAIN ANALYZE SELECT", "QUERY PLAN\nSeq Scan on t\n")
    console = QueryConsole(make_service(postgres()))

    # A harmless one is allowed, a harmful one is refused by the engine: neither by looking at its first word.
    result = console.run("postgresql", "shop", "EXPLAIN ANALYZE SELECT * FROM t")

    assert result.rows


def test_mysql_stops_a_typed_explain_analyze_in_a_read_only_transaction(
    sql_runner: SqlRunner,
) -> None:
    sql_runner.only_knows("mysql")
    sql_runner.answer("SCHEMA_NAME", "shop\n")
    sql_runner.answer(
        "EXPLAIN ANALYZE DELETE",
        stderr="ERROR 1792 (25006): Cannot execute statement in a READ ONLY transaction.\n",
        exit_code=1,
    )
    manager = MySQLManager()
    manager.config = StubConfig()

    with pytest.raises(DatabaseQueryError) as caught:
        QueryConsole(make_service(manager), owner="cli:root").run(
            "mysql", "shop", "EXPLAIN ANALYZE DELETE FROM t"
        )

    script = sql_runner.sent("EXPLAIN ANALYZE DELETE FROM t")[-1]
    assert "START TRANSACTION READ ONLY" in script
    assert "READ ONLY transaction" in (caught.value.details or "")
