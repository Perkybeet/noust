# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The data explorer and the row editor, end to end over the fake runner.

What is defended (research/3.1/databases.md, M4 and S1):

- **Read-only is the server's**: every read signs in as ``wasm_ro_<db>`` in a
  read-only transaction, and the statement travels on stdin, never in argv.
- **Names come from the catalog**: a relation the read-only session did not
  list is refused before any statement names it.
- **One process per page** once the catalog, the structure and the role are
  fresh.
- **An edit changes one row by its whole primary key**, as the superuser, in
  a guarded transaction; a missing row is a 404 and nothing changes; a table
  without a primary key is refused; the audit trail has the row before and
  after, with secret-looking columns redacted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from noust.core import audit
from noust.core.exceptions import (
    DatabaseEngineError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    ValidationError,
)
from noust.managers.database.browse import DataBrowser
from noust.managers.database.dialects import Filter, Order, decode_cursor
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from tests.database_data_support import SqlRunner, StubConfig, installed_sql_runner, make_service


@pytest.fixture
def sql_runner() -> Iterator[SqlRunner]:
    """
    Yields:
        The SQL runner, installed over a fresh store.
    """
    yield from installed_sql_runner()


CATALOG = json.dumps(
    {
        "schemas": ["public"],
        "relations": [
            {
                "schema": "public",
                "name": "users",
                "kind": "table",
                "rows_estimate": 3,
                "size_bytes": 16384,
            },
            {"schema": "public", "name": "report", "kind": "view"},
            {"schema": "public", "name": "log", "kind": "table"},
        ],
    }
)

STRUCTURE = json.dumps(
    {
        "columns": [
            {"name": "id", "type": "integer", "base": "int4", "category": "N", "nullable": False},
            {"name": "email", "type": "text", "base": "text", "category": "S"},
            {"name": "password_hash", "type": "text", "base": "text", "category": "S"},
        ],
        "primary_key": ["id"],
        "indexes": [],
        "constraints": [],
    }
)

NO_KEY = json.dumps(
    {
        "columns": [{"name": "line", "type": "text", "base": "text", "category": "S"}],
        "primary_key": [],
    }
)

PAGE = json.dumps(
    {
        "rows": [
            [[1, "a@x", "h1"], [False, False], ["1"]],
            [[2, "", None], [False, False], ["2"]],
        ]
    }
)


@pytest.fixture
def postgres(sql_runner: SqlRunner) -> PostgresManager:
    """
    Args:
        sql_runner: The runner.

    Returns:
        A PostgreSQL manager answering the catalog, the structure and a page.
    """
    sql_runner.answer("'relations'", CATALOG)
    sql_runner.answer("'primary_key'", STRUCTURE)
    sql_runner.answer("'rows', coalesce", PAGE)
    manager = PostgresManager()
    manager.config = StubConfig()
    return manager


@pytest.fixture
def browser(postgres: PostgresManager) -> DataBrowser:
    """
    Args:
        postgres: The manager.

    Returns:
        A browser over it.
    """
    return DataBrowser(make_service(postgres))


def read_only_calls(runner: SqlRunner) -> list[tuple[str, ...]]:
    """
    Args:
        runner: The runner.

    Returns:
        The psql calls signed in as the read-only role.
    """
    return [call for call in runner.calls if call[0] == "psql" and "-U" in call]


# ============================================================ reading


def test_the_catalog_is_read_as_the_read_only_role_on_stdin(
    browser: DataBrowser, sql_runner: SqlRunner
) -> None:
    relations = browser.relations("postgresql", "shop")

    assert [relation.name for relation in relations] == ["users", "report", "log"]
    call = read_only_calls(sql_runner)[-1]
    assert call[call.index("-U") + 1] == "wasm_ro_shop"
    assert ("-f", "-") == call[-2:]
    script = sql_runner.sent("'relations'")[-1]
    assert script.startswith("BEGIN READ ONLY;\n")
    assert "pg_read_all_data" not in "".join(text or "" for text in sql_runner.inputs)


def test_relations_are_narrowed_by_kind_and_search(browser: DataBrowser) -> None:
    assert [r.name for r in browser.relations("postgresql", "shop", kind="view")] == ["report"]
    assert [r.name for r in browser.relations("postgresql", "shop", search="US")] == ["users"]


def test_an_unknown_relation_kind_is_refused(browser: DataBrowser) -> None:
    with pytest.raises(ValidationError):
        browser.relations("postgresql", "shop", kind="sequence")


def test_a_missing_database_is_not_found(browser: DataBrowser, sql_runner: SqlRunner) -> None:
    sql_runner.answer("SELECT 1 FROM pg_database", "")

    with pytest.raises(DatabaseNotFoundError):
        browser.relations("postgresql", "gone")


def test_a_relation_the_catalog_lacks_is_refused_before_any_statement_names_it(
    browser: DataBrowser, sql_runner: SqlRunner
) -> None:
    browser.relations("postgresql", "shop")

    with pytest.raises(DatabaseNotFoundError):
        browser.describe("postgresql", "shop", "public", 'users"; DROP TABLE users; --')

    assert not sql_runner.sent("DROP TABLE")


def test_a_page_is_typed_and_says_where_the_next_starts(browser: DataBrowser) -> None:
    page = browser.rows("postgresql", "shop", "public", "users", limit=1)

    assert page.rows == [[1, "a@x", "h1"]]
    assert page.keys == [{"id": "1"}]
    assert page.has_more
    assert decode_cursor(page.next_cursor or "", 1) == ("1",)
    assert page.pagination == "keyset"


def test_null_and_empty_string_stay_apart(browser: DataBrowser) -> None:
    page = browser.rows("postgresql", "shop", "public", "users", limit=5)

    assert page.rows[1][1:] == ["", None]


def test_a_warm_page_is_one_process(browser: DataBrowser, sql_runner: SqlRunner) -> None:
    browser.rows("postgresql", "shop", "public", "users")
    before = len(sql_runner.calls)

    browser.rows("postgresql", "shop", "public", "users", order=[Order("email")])

    # SHOW port is the manager's own lookup (a fresh manager per request in
    # the API); the page itself is one psql, with no re-provisioning.
    new = sql_runner.calls[before:]
    assert len([call for call in new if "-U" in call]) == 1
    assert not [text for text in sql_runner.inputs[before:] if text and "CREATE ROLE" in text]


def test_filters_reach_the_engine_as_literals(browser: DataBrowser, sql_runner: SqlRunner) -> None:
    browser.rows(
        "postgresql",
        "shop",
        "public",
        "users",
        filters=[Filter.parse("email:eq:x' OR '1'='1")],
    )

    script = sql_runner.sent("'rows', coalesce")[-1]
    assert "\"email\" = 'x'' OR ''1''=''1'" in script
    assert not any("OR '1'='1" in arg for call in sql_runner.calls for arg in call)


def test_a_filter_on_an_unknown_column_is_refused(browser: DataBrowser) -> None:
    with pytest.raises(ValidationError):
        browser.rows("postgresql", "shop", "public", "users", filters=[Filter.parse("secret:eq:1")])


def test_a_page_read_is_audited(browser: DataBrowser) -> None:
    browser.rows("postgresql", "shop", "public", "users", filters=[Filter.parse("id:gt:0")])

    entry = audit.get_log().read(action="db.browse")[0]
    assert entry["resource"] == "db:postgresql/shop"
    assert entry["details"]["relation"] == "public.users"


def test_a_stale_grant_is_provisioned_again_and_retried(
    browser: DataBrowser, sql_runner: SqlRunner
) -> None:
    browser.relations("postgresql", "shop")
    sql_runner.answer(
        "'primary_key'",
        stderr="ERROR:  permission denied for table users\n",
        exit_code=3,
        times=1,
    )

    detail = browser.describe("postgresql", "shop", "public", "users")

    assert detail.primary_key == ("id",)
    assert len(sql_runner.sent("CREATE ROLE")) == 2


def test_a_stopped_engine_is_said_plainly(browser: DataBrowser, sql_runner: SqlRunner) -> None:
    sql_runner.answer("'relations'", stderr="psql: error: connection refused\n", exit_code=2)
    sql_runner.answer("is-active", "inactive\n")

    with pytest.raises(DatabaseEngineError, match="not running"):
        browser.relations("postgresql", "shop")


def test_engines_without_tables_are_refused(sql_runner: SqlRunner) -> None:
    from noust.managers.database.redis import RedisManager

    browser = DataBrowser(make_service(RedisManager()))

    with pytest.raises(DatabaseQueryError, match="no tables"):
        browser.relations("redis", "0")


# ============================================================ editing


CHANGED = json.dumps(
    {
        "before": {"id": 1, "email": "a@x", "password_hash": "old"},
        "after": {"id": 1, "email": "b@x", "password_hash": "old"},
    }
)


def test_an_update_runs_as_the_superuser_in_one_guarded_transaction(
    browser: DataBrowser, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("UPDATE", CHANGED)

    change = browser.update_row(
        "postgresql", "shop", "public", "users", {"id": "1"}, {"email": "b@x"}
    )

    assert change.before["email"] == "a@x"
    assert change.after["email"] == "b@x"
    call = sql_runner.calls_carrying("UPDATE")[-1]
    assert call[:5] == ("runuser", "-u", "postgres", "--", "psql")
    script = sql_runner.sent("UPDATE")[-1]
    assert script.startswith("BEGIN;")
    assert "noust: expected exactly one row" in script
    assert "b@x" not in " ".join(call)


def test_an_edit_is_audited_with_secret_columns_redacted(
    browser: DataBrowser, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("UPDATE", CHANGED)

    browser.update_row("postgresql", "shop", "public", "users", {"id": "1"}, {"email": "b@x"})

    entry = audit.get_log().read(action="db.row.update")[0]
    assert entry["details"]["before"]["email"] == "a@x"
    assert entry["details"]["after"]["email"] == "b@x"
    assert entry["details"]["before"]["password_hash"] != "old"
    assert entry["details"]["key"] == {"id": "1"}


def test_a_row_that_is_gone_is_not_found_and_nothing_changes(
    browser: DataBrowser, sql_runner: SqlRunner
) -> None:
    sql_runner.answer(
        "UPDATE",
        stderr="ERROR:  noust: expected exactly one row, found 0\n",
        exit_code=3,
    )

    with pytest.raises(DatabaseNotFoundError, match="nothing was changed"):
        browser.update_row("postgresql", "shop", "public", "users", {"id": "9"}, {"email": "x"})

    entry = audit.get_log().read(action="db.row.update")[0]
    assert entry["result"] == "failure"


def test_a_table_without_a_primary_key_is_read_only(
    browser: DataBrowser, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("'primary_key'", NO_KEY)

    with pytest.raises(ValidationError, match="no primary key"):
        browser.insert_row("postgresql", "shop", "public", "log", {"line": "x"})
    with pytest.raises(ValidationError, match="no primary key"):
        browser.delete_row("postgresql", "shop", "public", "log", {"line": "x"})


def test_an_edit_needs_the_whole_key(browser: DataBrowser) -> None:
    with pytest.raises(ValidationError, match="whole primary key"):
        browser.delete_row("postgresql", "shop", "public", "users", {"email": "a@x"})


def test_an_engine_refusal_is_carried_verbatim(browser: DataBrowser, sql_runner: SqlRunner) -> None:
    refusal = 'ERROR:  duplicate key value violates unique constraint "users_pkey"\n'
    sql_runner.answer('INSERT INTO "public"', stderr=refusal, exit_code=3)

    with pytest.raises(DatabaseQueryError) as caught:
        browser.insert_row("postgresql", "shop", "public", "users", {"id": 1})

    assert "users_pkey" in (caught.value.output or "")


def test_an_insert_returns_the_new_rows_key(browser: DataBrowser, sql_runner: SqlRunner) -> None:
    sql_runner.answer(
        'INSERT INTO "public"', json.dumps({"before": None, "after": {"id": 7, "email": "n@x"}})
    )

    change = browser.insert_row("postgresql", "shop", "public", "users", {"email": "n@x"})

    assert change.key == {"id": "7"}
    assert change.before is None


# ============================================================ MySQL


def test_mysql_reads_as_its_read_only_account_with_raw_output(sql_runner: SqlRunner) -> None:
    sql_runner.only_knows("mysql")
    sql_runner.answer("SCHEMA_NAME", "shop\n")
    sql_runner.answer(
        "'relation', TABLE_SCHEMA", '["relation", "shop", "users", "table", 3, 16384]\n'
    )
    manager = MySQLManager()
    manager.config = StubConfig()
    browser = DataBrowser(make_service(manager))

    relations = browser.relations("mysql", "shop")

    assert [relation.name for relation in relations] == ["users"]
    call = sql_runner.calls_carrying("'relation', TABLE_SCHEMA")[-1]
    assert "--raw" in call and "--binary-mode" in call
    assert any(arg.startswith("--user=wasm_ro_shop") for arg in call)
    script = sql_runner.sent("'relation', TABLE_SCHEMA")[-1]
    assert script.startswith(
        "SET SESSION max_execution_time = 30000;\nSTART TRANSACTION READ ONLY;\n"
    )


def test_mysql_reuses_its_read_only_account_and_retries_when_it_went_stale(
    sql_runner: SqlRunner,
) -> None:
    sql_runner.only_knows("mysql")
    sql_runner.answer("SCHEMA_NAME", "shop\n")
    manager = MySQLManager()
    manager.config = StubConfig()
    manager.run_sql("shop", "SELECT 1", read_only=True)
    sql_runner.answer(
        "SELECT 1", stderr="ERROR 1045 (28000): Access denied for user\n", exit_code=1, times=1
    )

    manager.run_sql("shop", "SELECT 1", read_only=True)

    assert len(sql_runner.sent("CREATE USER IF NOT EXISTS")) == 2
