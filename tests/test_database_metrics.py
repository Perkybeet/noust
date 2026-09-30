# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database metrics (noust.managers.database.metrics).

What is defended: each question is one call per engine, run read-only as the
administrative identity; slow queries say how to turn them on when the
engine does not keep them; statement texts are scrubbed; and the sampler
writes ``db.<engine>...`` series into the metrics store once a minute, with
rates from the difference between two samples.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from noust.core.exceptions import DatabaseQueryError, ValidationError
from noust.managers.database.metrics import (
    DatabaseMetricsReader,
    DatabaseSampler,
    series,
)
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.redis import RedisManager
from noust.monitor.timeseries import MetricsStore
from tests.database_data_support import SqlRunner, StubConfig, installed_sql_runner, make_service


@pytest.fixture
def sql_runner() -> Iterator[SqlRunner]:
    """
    Yields:
        The SQL runner, installed over a fresh store.
    """
    yield from installed_sql_runner()


PG_ENGINE = {
    "connections": 12,
    "max_connections": 100,
    "blks_hit": 990,
    "blks_read": 10,
    "databases": [
        {
            "name": "shop",
            "size_bytes": 8_000_000,
            "connections": 4,
            "xact": 1000,
            "blks_hit": 90,
            "blks_read": 10,
        }
    ],
}

PG_DATABASE = {
    "size_bytes": 8_000_000,
    "server_connections": 12,
    "max_connections": 100,
    "stat": {"connections": 4, "xact": 1000, "blks_hit": 90, "blks_read": 10, "deadlocks": 0},
    "tables": [
        {"schema": "public", "name": "orders", "size_bytes": 4_000_000, "rows_estimate": 50_000}
    ],
}


@pytest.fixture
def postgres(sql_runner: SqlRunner) -> PostgresManager:
    """
    Args:
        sql_runner: The runner.

    Returns:
        A PostgreSQL manager answering the metrics queries.
    """
    sql_runner.answer("'databases', coalesce", json.dumps(PG_ENGINE))
    sql_runner.answer("'tables', coalesce", json.dumps(PG_DATABASE))
    manager = PostgresManager()
    manager.config = StubConfig()
    return manager


def test_postgres_engine_metrics_are_one_read_only_superuser_query(
    postgres: PostgresManager, sql_runner: SqlRunner
) -> None:
    metrics = DatabaseMetricsReader(make_service(postgres)).engine_metrics("postgresql")

    assert (metrics.connections, metrics.max_connections, metrics.cache_hit_ratio) == (
        12,
        100,
        99.0,
    )
    assert metrics.databases == [{"name": "shop", "size_bytes": 8_000_000, "connections": 4}]
    assert metrics.series["connections"] == "db.postgresql.connections"
    call = sql_runner.calls_carrying("'databases', coalesce")[-1]
    assert call[:5] == ("runuser", "-u", "postgres", "--", "psql")
    assert sql_runner.sent("'databases', coalesce")[-1].startswith("BEGIN READ ONLY;")


def test_postgres_database_metrics_list_the_biggest_tables(
    postgres: PostgresManager, sql_runner: SqlRunner
) -> None:
    metrics = DatabaseMetricsReader(make_service(postgres)).database_metrics("postgresql", "shop")

    assert metrics.size_bytes == 8_000_000
    assert metrics.connections == 4
    assert metrics.cache_hit_ratio == 90.0
    assert metrics.tables[0].name == "orders"
    assert metrics.series["tps"] == "db.postgresql.shop.tps"


def test_slow_queries_say_how_to_load_pg_stat_statements(
    postgres: PostgresManager, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("'preloaded'", json.dumps({"schema": None, "preloaded": False}))

    answer = DatabaseMetricsReader(make_service(postgres)).slow_queries("postgresql", "shop")

    assert not answer.available
    assert answer.reason == "not_loaded"
    assert "shared_preload_libraries" in (answer.how_to_enable or "")
    assert "restart" in (answer.how_to_enable or "")


def test_slow_queries_say_how_to_create_the_extension(
    postgres: PostgresManager, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("'preloaded'", json.dumps({"schema": None, "preloaded": True}))

    answer = DatabaseMetricsReader(make_service(postgres)).slow_queries("postgresql", "shop")

    assert answer.reason == "not_created"
    assert "CREATE EXTENSION pg_stat_statements" in (answer.how_to_enable or "")


def test_slow_queries_are_read_from_the_extensions_schema_and_scrubbed(
    postgres: PostgresManager, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("'preloaded'", json.dumps({"schema": "ext", "preloaded": True}))
    sql_runner.answer(
        "pg_stat_statements p",
        json.dumps(
            [
                {
                    "query": "ALTER ROLE a PASSWORD 'x1y2z3w4'",
                    "calls": 1,
                    "total_ms": 5.0,
                    "mean_ms": 5.0,
                    "rows": 0,
                },
                {
                    "query": "SELECT * FROM t WHERE id = $1",
                    "calls": 40,
                    "total_ms": 80.0,
                    "mean_ms": 2.0,
                    "rows": 40,
                },
            ]
        ),
    )

    answer = DatabaseMetricsReader(make_service(postgres)).slow_queries(
        "postgresql", "shop", limit=5
    )

    assert answer.available
    assert "x1y2z3w4" not in answer.queries[0]["query"]
    assert answer.queries[1]["calls"] == 40
    assert '"ext".pg_stat_statements' in sql_runner.sent("pg_stat_statements p")[-1]
    assert "LIMIT 5" in sql_runner.sent("pg_stat_statements p")[-1]


def test_the_slow_query_limit_is_bounded(postgres: PostgresManager) -> None:
    with pytest.raises(ValidationError):
        DatabaseMetricsReader(make_service(postgres)).slow_queries("postgresql", "shop", limit=1000)


def test_mysql_metrics_read_status_and_the_schemas_size(sql_runner: SqlRunner) -> None:
    sql_runner.only_knows("mysql")
    sql_runner.answer(
        "SHOW GLOBAL STATUS",
        "Threads_connected\t7\nInnodb_buffer_pool_read_requests\t1000\nInnodb_buffer_pool_reads\t50\n"
        "max_connections\t151\nperformance_schema\tOFF\ndb_size\t65536\n"
        'table\t{"schema": "shop", "name": "orders", "size_bytes": 32768, "rows_estimate": 12}\n'
        "db_connections\t2\n",
    )
    manager = MySQLManager()
    manager.config = StubConfig()
    reader = DatabaseMetricsReader(make_service(manager))

    metrics = reader.database_metrics("mysql", "shop")

    assert (metrics.size_bytes, metrics.connections, metrics.server_connections) == (65536, 2, 7)
    assert metrics.cache_hit_ratio == 95.0
    assert metrics.tables[0].rows_estimate == 12
    assert "X'73686f70'" in sql_runner.sent("SHOW GLOBAL STATUS")[-1]


def test_mysql_slow_queries_need_performance_schema(sql_runner: SqlRunner) -> None:
    sql_runner.only_knows("mysql")
    sql_runner.answer("performance_schema'", "performance_schema\tOFF\n")
    manager = MySQLManager()
    manager.config = StubConfig()

    answer = DatabaseMetricsReader(make_service(manager)).slow_queries("mysql", "shop")

    assert answer.reason == "performance_schema_off"
    assert "performance_schema = ON" in (answer.how_to_enable or "")


def test_redis_metrics_come_from_info(sql_runner: SqlRunner) -> None:
    sql_runner.only_knows("redis-cli")
    sql_runner.answer(
        "\\x49\\x4e\\x46\\x4f",
        '"# Clients\\r\\nconnected_clients:3\\r\\nmaxclients:10000\\r\\n# Stats\\r\\n'
        "keyspace_hits:75\\r\\nkeyspace_misses:25\\r\\nused_memory:1048576\\r\\n"
        '# Keyspace\\r\\ndb0:keys=42,expires=1,avg_ttl=0\\r\\n"\n',
    )
    reader = DatabaseMetricsReader(make_service(RedisManager()))

    engine = reader.engine_metrics("redis")
    slot = reader.database_metrics("redis", "0")

    assert (engine.connections, engine.max_connections, engine.cache_hit_ratio) == (3, 10000, 75.0)
    assert engine.details["used_memory"] == 1048576
    assert engine.databases == [{"name": "0", "keys": 42, "expires": 1}]
    assert slot.keys == 42


def test_mongodb_reports_no_metrics_here(sql_runner: SqlRunner) -> None:
    from noust.managers.database.mongodb import MongoDBManager

    with pytest.raises(DatabaseQueryError, match="not available"):
        DatabaseMetricsReader(make_service(MongoDBManager())).engine_metrics("mongodb")


# ============================================================ sampling


def test_the_sampler_writes_db_series_once_a_minute_with_rates(
    postgres: PostgresManager, sql_runner: SqlRunner, tmp_path
) -> None:
    store = MetricsStore(tmp_path / "metrics.db", clock=lambda: 1_000_000.0)
    clock = [100.0]
    sampler = DatabaseSampler(service=make_service(postgres), clock=lambda: clock[0])

    first = sampler.record(store)
    clock[0] = 110.0
    assert sampler.sample() == []
    clock[0] = 160.0
    later = dict(PG_ENGINE)
    later["databases"] = [dict(PG_ENGINE["databases"][0], xact=1600, blks_hit=190, blks_read=10)]
    sql_runner.answer("'databases', coalesce", json.dumps(later))
    pairs = dict(sampler.sample())

    assert first == 3  # server connections, the database's size and connections
    assert pairs[series("postgresql", "tps", "shop")] == 10.0
    assert pairs[series("postgresql", "cache_hit", "shop")] == 100.0
    assert series("postgresql", "size", "shop") in store.list_metrics()


def test_the_sampler_skips_an_engine_that_fails(
    postgres: PostgresManager, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("'databases', coalesce", stderr="psql: error: FATAL\n", exit_code=2)

    assert DatabaseSampler(service=make_service(postgres)).sample() == []


def test_the_sampler_skips_stopped_engines(
    postgres: PostgresManager, sql_runner: SqlRunner
) -> None:
    sql_runner.answer("is-active", "inactive\n")

    assert DatabaseSampler(service=make_service(postgres)).sample() == []
    assert not sql_runner.sent("'databases', coalesce")
