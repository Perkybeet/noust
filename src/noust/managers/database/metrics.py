# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database metrics: size, biggest tables, connections, cache hits, slow queries.

One call per engine answers each question: a single ``json_build_object``
over ``pg_stat_database``, ``pg_stat_activity`` and ``pg_class`` on
PostgreSQL; ``SHOW GLOBAL STATUS``/``VARIABLES`` and ``information_schema``
in one client run on MySQL and MariaDB; ``INFO`` on Redis. They run as the
engine's administrative identity (other sessions' activity and the
statement statistics are not visible to a least-privilege account), inside a
read-only transaction where the engine has one, and are statements Noust
wrote: nothing from a request reaches them but a database name, as a literal.

History lives in the metrics store that already charts the machine and the
applications (:class:`~noust.monitor.timeseries.MetricsStore`), under
``db.<engine>.<database>.<metric>`` and ``db.<engine>.<metric>``.
:class:`DatabaseSampler` produces those samples once a minute - rates such
as transactions per second come from the difference between two samples -
and is meant to be driven by the metrics collector's tick; see its docstring.

Slow queries come from ``pg_stat_statements`` and from
``performance_schema``'s statement digests. When they are not available the
answer says why and how to turn them on, instead of an empty list.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from noust.central import RoleError, is_hub
from noust.core.audit.sanitize import scrub_statement
from noust.core.exceptions import DatabaseError, DatabaseQueryError, ValidationError
from noust.managers.database.base import NAME_PATTERN, BaseDatabaseManager
from noust.managers.database.dialects import MySQLDialect, PostgresDialect, load_json
from noust.managers.database.keys import parse_csv_reply
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.redis import RedisManager
from noust.managers.database.service import DatabaseService

log = logging.getLogger(__name__)

#: Seconds between two samples of the same engine.
SAMPLE_SECONDS = 60.0

#: Seconds the server may spend on one metrics query.
METRICS_TIMEOUT = 30

#: Biggest tables listed.
TOP_TABLES = 10

#: Slow queries listed, by default and at most.
DEFAULT_SLOW_QUERIES = 20
MAX_SLOW_QUERIES = 100

#: MySQL's schemas that are the server's own.
_MYSQL_SYSTEM = ("information_schema", "mysql", "performance_schema", "sys")

#: What MySQL's status and variables are read for.
_MYSQL_STATUS = (
    "Threads_connected",
    "Threads_running",
    "Slow_queries",
    "Questions",
    "Innodb_buffer_pool_reads",
    "Innodb_buffer_pool_read_requests",
    "Uptime",
)
_MYSQL_VARIABLES = ("max_connections", "performance_schema")

#: How to turn slow-query statistics on, per engine and reason.
PG_STATEMENTS_NOT_LOADED = (
    "pg_stat_statements is not loaded. Add it to shared_preload_libraries in postgresql.conf "
    "(shared_preload_libraries = 'pg_stat_statements'), restart PostgreSQL (systemctl restart "
    "postgresql), then create the extension in this database: noust db query {database} "
    "'CREATE EXTENSION pg_stat_statements' --engine postgresql --write"
)
PG_STATEMENTS_NOT_CREATED = (
    "pg_stat_statements is loaded but not created in this database. Create it: noust db query "
    "{database} 'CREATE EXTENSION pg_stat_statements' --engine postgresql --write"
)
MYSQL_PERFORMANCE_SCHEMA_OFF = (
    "performance_schema is off. Set performance_schema = ON in the [mysqld] section of the "
    "server's configuration (/etc/mysql/mariadb.conf.d/50-server.cnf on Debian and Ubuntu), "
    "then restart the server (systemctl restart {service})."
)


def series(engine: str, metric: str, database: str | None = None) -> str:
    """
    Name a database metric's series in the metrics store.

    Args:
        engine: The engine.
        metric: ``size``, ``connections``, ``cache_hit``, ``tps``...
        database: The database, for a per-database series.

    Returns:
        ``db.<engine>.<database>.<metric>`` or ``db.<engine>.<metric>``.
    """
    return f"db.{engine}.{database}.{metric}" if database else f"db.{engine}.{metric}"


def _ratio(hits: float | None, misses: float | None) -> float | None:
    """
    Args:
        hits: Reads served from memory.
        misses: Reads that went to disk.

    Returns:
        The hit ratio as a percentage, or None when there were no reads.
    """
    if hits is None or misses is None or hits + misses <= 0:
        return None
    return round(100.0 * hits / (hits + misses), 2)


def _number(value: Any) -> float | None:
    """
    Args:
        value: A value the engine printed.

    Returns:
        It as a float, or None.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    """
    Args:
        value: A value the engine printed.

    Returns:
        It as an int, or None.
    """
    number = _number(value)
    return int(number) if number is not None else None


# ============================================================ model


@dataclass
class TableSize:
    """
    One of a database's biggest tables.

    Attributes:
        schema: Its schema.
        name: Its name.
        size_bytes: Data and indexes on disk.
        rows_estimate: The planner's estimate.
    """

    schema: str
    name: str
    size_bytes: int | None = None
    rows_estimate: int | None = None


@dataclass
class SlowQueries:
    """
    The statements that take longest on average.

    Attributes:
        available: Whether the engine keeps statement statistics.
        source: ``pg_stat_statements`` or ``performance_schema``.
        reason: Why they are not available: ``not_loaded``,
            ``not_created``, ``performance_schema_off``, ``not_supported``.
        how_to_enable: The steps, when they are not.
        queries: Each with ``query`` (normalised by the engine, scrubbed),
            ``calls``, ``total_ms``, ``mean_ms`` and ``rows``.
    """

    available: bool
    source: str | None = None
    reason: str | None = None
    how_to_enable: str | None = None
    queries: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The answer as plain data.
        """
        return asdict(self)


@dataclass
class EngineMetrics:
    """
    An engine's state now.

    Attributes:
        engine: The engine.
        connections: Client connections open.
        max_connections: The most it accepts.
        cache_hit_ratio: Reads served from memory since the statistics
            began, as a percentage.
        databases: Per database: ``name``, ``size_bytes``, ``connections``.
        details: What only this engine reports (Redis memory, MySQL slow
            query count...).
        series: The metrics store's series for the engine's charts.
    """

    engine: str
    connections: int | None = None
    max_connections: int | None = None
    cache_hit_ratio: float | None = None
    databases: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    series: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The metrics as plain data.
        """
        return asdict(self)


@dataclass
class DatabaseMetrics:
    """
    One database's state now.

    Attributes:
        engine: The engine.
        database: The database.
        size_bytes: Its size on disk.
        connections: Connections to it.
        server_connections: Connections to the whole server.
        max_connections: The most the server accepts.
        cache_hit_ratio: Its reads served from memory since the statistics
            began, as a percentage (server-wide on MySQL, whose buffer pool
            is shared).
        transactions: Transactions committed and rolled back since the
            statistics began, when the engine counts them per database.
        deadlocks: Deadlocks since the statistics began, when counted.
        keys: Keys in it (Redis).
        tables: The biggest tables.
        series: The metrics store's series for its charts.
    """

    engine: str
    database: str
    size_bytes: int | None = None
    connections: int | None = None
    server_connections: int | None = None
    max_connections: int | None = None
    cache_hit_ratio: float | None = None
    transactions: int | None = None
    deadlocks: int | None = None
    keys: int | None = None
    tables: list[TableSize] = field(default_factory=list)
    series: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The metrics as plain data.
        """
        return asdict(self)


def database_series(engine: str, database: str) -> dict[str, str]:
    """
    Args:
        engine: The engine.
        database: The database.

    Returns:
        Chart name to series: ``size``, ``connections``, ``cache_hit`` and,
        on PostgreSQL, ``tps``.
    """
    names = {
        "postgresql": ["size", "connections", "cache_hit", "tps"],
        "mysql": ["size", "connections"],
        "redis": ["keys"],
    }.get(engine, [])
    return {name: series(engine, name, database) for name in names}


def engine_series(engine: str) -> dict[str, str]:
    """
    Args:
        engine: The engine.

    Returns:
        Chart name to series for the engine as a whole.
    """
    names = {
        "postgresql": ["connections"],
        "mysql": ["connections", "cache_hit", "qps"],
        "redis": ["connections", "memory", "cache_hit", "ops"],
    }.get(engine, [])
    return {name: series(engine, name) for name in names}


# ============================================================ SQL


_PG = PostgresDialect()
_MY = MySQLDialect()

_PG_ENGINE = (
    "SELECT json_build_object("
    "'connections', (SELECT count(*) FROM pg_catalog.pg_stat_activity "
    "WHERE backend_type = 'client backend'), "
    "'max_connections', current_setting('max_connections')::int, "
    "'blks_hit', (SELECT sum(blks_hit) FROM pg_catalog.pg_stat_database), "
    "'blks_read', (SELECT sum(blks_read) FROM pg_catalog.pg_stat_database), "
    "'databases', coalesce((SELECT json_agg(json_build_object("
    "'name', d.datname, 'size_bytes', pg_catalog.pg_database_size(d.oid), "
    "'connections', s.numbackends, 'xact', s.xact_commit + s.xact_rollback, "
    "'blks_hit', s.blks_hit, 'blks_read', s.blks_read) ORDER BY d.datname) "
    "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_stat_database s ON s.datid = d.oid "
    "WHERE NOT d.datistemplate AND d.datallowconn AND d.datname <> 'postgres'), '[]'::json))"
)

_PG_DATABASE = (
    "SELECT json_build_object("  # noqa: S608 - identifiers from the catalog, values as literals
    "'size_bytes', pg_catalog.pg_database_size(current_database()), "
    "'server_connections', (SELECT count(*) FROM pg_catalog.pg_stat_activity "
    "WHERE backend_type = 'client backend'), "
    "'max_connections', current_setting('max_connections')::int, "
    "'stat', (SELECT json_build_object('connections', numbackends, "
    "'xact', xact_commit + xact_rollback, 'blks_hit', blks_hit, 'blks_read', blks_read, "
    "'deadlocks', deadlocks) FROM pg_catalog.pg_stat_database "
    "WHERE datname = current_database()), "
    "'tables', coalesce((SELECT json_agg(t) FROM (SELECT n.nspname AS schema, "
    "c.relname AS name, pg_catalog.pg_total_relation_size(c.oid) AS size_bytes, "
    "CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END AS rows_estimate "
    "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    f"WHERE c.relkind IN ('r', 'p', 'm') AND {PostgresDialect.SYSTEM_SCHEMAS} "
    f"ORDER BY 3 DESC LIMIT {TOP_TABLES}) t), '[]'::json))"
)

_PG_STATEMENTS_STATE = (
    "SELECT json_build_object("
    "'schema', (SELECT n.nspname FROM pg_catalog.pg_extension e "
    "JOIN pg_catalog.pg_namespace n ON n.oid = e.extnamespace "
    "WHERE e.extname = 'pg_stat_statements'), "
    "'preloaded', 'pg_stat_statements' = ANY (string_to_array("
    "replace(current_setting('shared_preload_libraries'), ' ', ''), ',')))"
)


def _pg_statements(schema: str, limit: int) -> str:
    """
    Args:
        schema: The schema pg_stat_statements was created in, from the catalog.
        limit: Statements listed.

    Returns:
        The statement reading the slowest statements of the current database.
        Through ``to_jsonb`` so it reads both column spellings
        (``mean_exec_time`` from PostgreSQL 13, ``mean_time`` before).
    """
    return (
        "SELECT coalesce(json_agg(q), '[]'::json) FROM (SELECT s->>'query' AS query, "  # noqa: S608 - identifiers from the catalog, values as literals
        "(s->>'calls')::bigint AS calls, "
        "coalesce(s->>'total_exec_time', s->>'total_time')::float8 AS total_ms, "
        "coalesce(s->>'mean_exec_time', s->>'mean_time')::float8 AS mean_ms, "
        "(s->>'rows')::bigint AS rows "
        f"FROM (SELECT to_jsonb(p) AS s FROM {_PG.ident(schema)}.pg_stat_statements p "
        "WHERE p.dbid = (SELECT oid FROM pg_catalog.pg_database "
        "WHERE datname = current_database())) x "
        f"ORDER BY 4 DESC NULLS LAST LIMIT {int(limit)}) q"
    )


def _read_only(sql: str) -> str:
    """
    Args:
        sql: One statement.

    Returns:
        It inside a read-only transaction, for the superuser session.
    """
    return f"BEGIN READ ONLY;\n{sql};\nCOMMIT;\n"


def _mysql_state() -> str:
    """
    Returns:
        The statements reading MySQL's status and variables.
    """
    status = ", ".join(f"'{name}'" for name in _MYSQL_STATUS)
    variables = ", ".join(f"'{name}'" for name in _MYSQL_VARIABLES)
    return (
        f"SHOW GLOBAL STATUS WHERE Variable_name IN ({status});\n"
        f"SHOW GLOBAL VARIABLES WHERE Variable_name IN ({variables});\n"
    )


def _tagged_lines(output: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """
    Read ``name<TAB>value`` lines.

    Args:
        output: The client's output.

    Returns:
        The status and variable values by name, and every ``(tag, value)``
        line in order (the JSON ones included).
    """
    values: dict[str, str] = {}
    lines: list[tuple[str, str]] = []
    for line in output.splitlines():
        name, _, value = line.partition("\t")
        if not name:
            continue
        values[name] = value
        lines.append((name, value))
    return values, lines


# ============================================================ reader


class DatabaseMetricsReader:
    """
    Answer the metrics questions for the API, the CLI and the sampler.

    Construct one per request or command over the
    :class:`~noust.managers.database.service.DatabaseService`.
    """

    def __init__(self, service: DatabaseService) -> None:
        """
        Args:
            service: The database service.
        """
        self.service = service

    def _manager(self, engine: str) -> BaseDatabaseManager:
        """
        Args:
            engine: The engine.

        Returns:
            Its manager, installed and running.

        Raises:
            DatabaseQueryError: When the engine reports no metrics.
        """
        manager = self.service.running(engine)
        if manager.ENGINE_NAME not in ("postgresql", "mysql", "redis"):
            raise DatabaseQueryError(
                f"Metrics are not available for {manager.DISPLAY_NAME}",
                details="PostgreSQL, MySQL/MariaDB and Redis report metrics.",
            )
        return manager

    # ------------------------------------------------------------ engines

    def engine_metrics(self, engine: str) -> EngineMetrics:
        """
        Read an engine's state.

        Args:
            engine: The engine.

        Returns:
            The metrics.
        """
        manager = self._manager(engine)
        name = manager.ENGINE_NAME
        if name == "postgresql":
            data = load_json(
                manager.run_sql(
                    "postgres", _read_only(_PG_ENGINE), read_only=False, timeout_s=METRICS_TIMEOUT
                ),
                what="the engine's statistics",
            )
            databases = data.get("databases") or []
            return EngineMetrics(
                engine=name,
                connections=_int(data.get("connections")),
                max_connections=_int(data.get("max_connections")),
                cache_hit_ratio=_ratio(
                    _number(data.get("blks_hit")), _number(data.get("blks_read"))
                ),
                databases=[
                    {
                        "name": item.get("name"),
                        "size_bytes": _int(item.get("size_bytes")),
                        "connections": _int(item.get("connections")),
                    }
                    for item in databases
                ],
                series=engine_series(name),
            )
        if name == "mysql":
            return self._mysql_engine(manager)
        return self._redis_engine(manager)

    def _mysql_engine(self, manager: BaseDatabaseManager) -> EngineMetrics:
        """
        Args:
            manager: The MySQL manager.

        Returns:
            Its metrics.

        Raises:
            DatabaseQueryError: When the manager is not MySQL's.
        """
        if not isinstance(manager, MySQLManager):
            raise DatabaseQueryError("Not a MySQL engine", details="Ask a MySQL engine.")
        system = ", ".join(_MY.literal(name) for name in _MYSQL_SYSTEM)
        output = manager.run_sql(
            None,
            _mysql_state() + "SELECT 'size', JSON_OBJECT('name', TABLE_SCHEMA, 'size_bytes', "  # noqa: S608 - identifiers from the catalog, values as literals
            "SUM(DATA_LENGTH + INDEX_LENGTH)) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA NOT IN ({system}) GROUP BY TABLE_SCHEMA;\n"
            "SELECT 'connections', JSON_OBJECT('name', DB, 'count', COUNT(*)) "
            "FROM information_schema.PROCESSLIST WHERE DB IS NOT NULL GROUP BY DB;\n",
            read_only=False,
            timeout_s=METRICS_TIMEOUT,
        )
        values, lines = _tagged_lines(output)
        sizes: dict[str, dict[str, Any]] = {}
        for tag, value in lines:
            if tag in ("size", "connections"):
                item = load_json(value, what="the server's statistics")
                entry = sizes.setdefault(
                    str(item.get("name")),
                    {"name": item.get("name"), "size_bytes": None, "connections": 0},
                )
                if tag == "size":
                    entry["size_bytes"] = _int(item.get("size_bytes"))
                else:
                    entry["connections"] = _int(item.get("count"))
        return EngineMetrics(
            engine=manager.ENGINE_NAME,
            connections=_int(values.get("Threads_connected")),
            max_connections=_int(values.get("max_connections")),
            cache_hit_ratio=_innodb_hit_ratio(values),
            databases=sorted(sizes.values(), key=lambda entry: str(entry["name"])),
            details={
                "threads_running": _int(values.get("Threads_running")),
                "slow_queries": _int(values.get("Slow_queries")),
                "questions": _int(values.get("Questions")),
                "buffer_pool_read_requests": _int(values.get("Innodb_buffer_pool_read_requests")),
                "buffer_pool_reads": _int(values.get("Innodb_buffer_pool_reads")),
                "uptime_s": _int(values.get("Uptime")),
                "performance_schema": values.get("performance_schema"),
            },
            series=engine_series(manager.ENGINE_NAME),
        )

    def _redis_info(self, manager: BaseDatabaseManager) -> dict[str, str]:
        """
        Args:
            manager: The Redis manager.

        Returns:
            ``INFO``'s fields.

        Raises:
            DatabaseQueryError: When the answer is not what INFO prints.
        """
        if not isinstance(manager, RedisManager):
            raise DatabaseQueryError("Not a Redis engine", details="Ask a Redis engine for INFO.")
        tokens = parse_csv_reply(manager.run_commands([["INFO"]]).strip())
        text = (
            tokens[0].decode("utf-8", "replace") if tokens and isinstance(tokens[0], bytes) else ""
        )
        info: dict[str, str] = {}
        for line in text.splitlines():
            key, _, value = line.strip().partition(":")
            if key and not key.startswith("#"):
                info[key] = value
        return info

    def _redis_engine(self, manager: BaseDatabaseManager) -> EngineMetrics:
        """
        Args:
            manager: The Redis manager.

        Returns:
            Its metrics.
        """
        info = self._redis_info(manager)
        slots = []
        for key, value in info.items():
            if key.startswith("db") and key[2:].isdigit():
                fields = dict(part.split("=", 1) for part in value.split(",") if "=" in part)
                slots.append(
                    {
                        "name": key[2:],
                        "keys": _int(fields.get("keys")),
                        "expires": _int(fields.get("expires")),
                    }
                )
        return EngineMetrics(
            engine=manager.ENGINE_NAME,
            connections=_int(info.get("connected_clients")),
            max_connections=_int(info.get("maxclients")),
            cache_hit_ratio=_ratio(
                _number(info.get("keyspace_hits")), _number(info.get("keyspace_misses"))
            ),
            databases=slots,
            details={
                "used_memory": _int(info.get("used_memory")),
                "maxmemory": _int(info.get("maxmemory")),
                "evicted_keys": _int(info.get("evicted_keys")),
                "keyspace_hits": _int(info.get("keyspace_hits")),
                "keyspace_misses": _int(info.get("keyspace_misses")),
                "commands_processed": _int(info.get("total_commands_processed")),
                "ops_per_sec": _number(info.get("instantaneous_ops_per_sec")),
                "uptime_s": _int(info.get("uptime_in_seconds")),
            },
            series=engine_series(manager.ENGINE_NAME),
        )

    # ------------------------------------------------------------ databases

    def database_metrics(self, engine: str, database: str) -> DatabaseMetrics:
        """
        Read one database's state: size, connections, cache hits, biggest tables.

        Args:
            engine: The engine.
            database: The database (a slot number on Redis).

        Returns:
            The metrics.
        """
        manager = self._manager(engine)
        name = manager.ENGINE_NAME
        database = manager.validate_database_name(database)
        if name == "postgresql":
            data = load_json(
                manager.run_sql(
                    database, _read_only(_PG_DATABASE), read_only=False, timeout_s=METRICS_TIMEOUT
                ),
                what="the database's statistics",
            )
            stat = data.get("stat") or {}
            return DatabaseMetrics(
                engine=name,
                database=database,
                size_bytes=_int(data.get("size_bytes")),
                connections=_int(stat.get("connections")),
                server_connections=_int(data.get("server_connections")),
                max_connections=_int(data.get("max_connections")),
                cache_hit_ratio=_ratio(
                    _number(stat.get("blks_hit")), _number(stat.get("blks_read"))
                ),
                transactions=_int(stat.get("xact")),
                deadlocks=_int(stat.get("deadlocks")),
                tables=[_table(item) for item in data.get("tables") or []],
                series=database_series(name, database),
            )
        if name == "mysql":
            schema = _MY.literal(database)
            output = manager.run_sql(
                database,
                _mysql_state() + "SELECT 'db_size', COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0) "  # noqa: S608 - identifiers from the catalog, values as literals
                f"FROM information_schema.TABLES WHERE TABLE_SCHEMA = {schema};\n"
                "SELECT 'table', JSON_OBJECT('schema', TABLE_SCHEMA, 'name', TABLE_NAME, "
                "'size_bytes', DATA_LENGTH + INDEX_LENGTH, 'rows_estimate', TABLE_ROWS) "
                f"FROM information_schema.TABLES WHERE TABLE_SCHEMA = {schema} "
                "AND TABLE_TYPE = 'BASE TABLE' ORDER BY DATA_LENGTH + INDEX_LENGTH DESC "
                f"LIMIT {TOP_TABLES};\n"
                "SELECT 'db_connections', COUNT(*) FROM information_schema.PROCESSLIST "
                f"WHERE DB = {schema};\n",
                read_only=False,
                timeout_s=METRICS_TIMEOUT,
            )
            values, lines = _tagged_lines(output)
            return DatabaseMetrics(
                engine=name,
                database=database,
                size_bytes=_int(values.get("db_size")),
                connections=_int(values.get("db_connections")),
                server_connections=_int(values.get("Threads_connected")),
                max_connections=_int(values.get("max_connections")),
                cache_hit_ratio=_innodb_hit_ratio(values),
                tables=[
                    _table(load_json(value, what="the database's statistics"))
                    for tag, value in lines
                    if tag == "table"
                ],
                series=database_series(name, database),
            )
        info = self._redis_info(manager)
        fields = dict(
            part.split("=", 1) for part in info.get(f"db{database}", "").split(",") if "=" in part
        )
        # Clients, memory and hits are the instance's: a slot is not a
        # separate database to Redis, only a keyspace.
        return DatabaseMetrics(
            engine=name,
            database=database,
            server_connections=_int(info.get("connected_clients")),
            max_connections=_int(info.get("maxclients")),
            cache_hit_ratio=_ratio(
                _number(info.get("keyspace_hits")), _number(info.get("keyspace_misses"))
            ),
            keys=_int(fields.get("keys")) or 0,
            series=database_series(name, database),
        )

    # ------------------------------------------------------------ slow queries

    def slow_queries(
        self, engine: str, database: str, *, limit: int = DEFAULT_SLOW_QUERIES
    ) -> SlowQueries:
        """
        List the statements that take longest on average, where the engine keeps them.

        Args:
            engine: The engine.
            database: The database.
            limit: Statements listed, at most :data:`MAX_SLOW_QUERIES`.

        Returns:
            The statements, or why there are none and how to turn them on.

        Raises:
            ValidationError: When the limit is out of range.
        """
        if limit < 1 or limit > MAX_SLOW_QUERIES:
            raise ValidationError(
                f"From 1 to {MAX_SLOW_QUERIES} slow queries can be listed",
                details="Ask for fewer.",
            )
        manager = self._manager(engine)
        database = manager.validate_database_name(database)
        if manager.ENGINE_NAME == "postgresql":
            state = load_json(
                manager.run_sql(
                    database,
                    _read_only(_PG_STATEMENTS_STATE),
                    read_only=False,
                    timeout_s=METRICS_TIMEOUT,
                ),
                what="pg_stat_statements' state",
            )
            if not state.get("preloaded"):
                return SlowQueries(
                    available=False,
                    source="pg_stat_statements",
                    reason="not_loaded",
                    how_to_enable=PG_STATEMENTS_NOT_LOADED.format(database=database),
                )
            schema = state.get("schema")
            if not schema:
                return SlowQueries(
                    available=False,
                    source="pg_stat_statements",
                    reason="not_created",
                    how_to_enable=PG_STATEMENTS_NOT_CREATED.format(database=database),
                )
            rows = load_json(
                manager.run_sql(
                    database,
                    _read_only(_pg_statements(str(schema), limit)),
                    read_only=False,
                    timeout_s=METRICS_TIMEOUT,
                ),
                what="pg_stat_statements",
            )
            return SlowQueries(
                available=True,
                source="pg_stat_statements",
                queries=[_statement(item) for item in rows or []],
            )
        if manager.ENGINE_NAME == "mysql":
            output = manager.run_sql(
                database,
                "SHOW GLOBAL VARIABLES WHERE Variable_name = 'performance_schema';\n"
                "SELECT 'query', JSON_OBJECT('query', DIGEST_TEXT, 'calls', COUNT_STAR, "
                "'total_ms', SUM_TIMER_WAIT / 1000000000, 'mean_ms', AVG_TIMER_WAIT / 1000000000, "
                "'rows', SUM_ROWS_SENT) "
                "FROM performance_schema.events_statements_summary_by_digest "
                f"WHERE SCHEMA_NAME = {_MY.literal(database)} "
                f"ORDER BY AVG_TIMER_WAIT DESC LIMIT {int(limit)};\n",
                read_only=False,
                timeout_s=METRICS_TIMEOUT,
            )
            values, lines = _tagged_lines(output)
            if values.get("performance_schema", "").upper() != "ON":
                return SlowQueries(
                    available=False,
                    source="performance_schema",
                    reason="performance_schema_off",
                    how_to_enable=MYSQL_PERFORMANCE_SCHEMA_OFF.format(
                        service=manager.service_unit()
                    ),
                )
            return SlowQueries(
                available=True,
                source="performance_schema",
                queries=[
                    _statement(load_json(value, what="the statement digests"))
                    for tag, value in lines
                    if tag == "query"
                ],
            )
        return SlowQueries(
            available=False,
            reason="not_supported",
            how_to_enable=(
                "Redis keeps its slow log in SLOWLOG; read it with: noust db query "
                f"{database} 'SLOWLOG GET 20' --engine redis --write"
            ),
        )


def _innodb_hit_ratio(values: dict[str, str]) -> float | None:
    """
    Args:
        values: MySQL's status by name.

    Returns:
        ``1 - reads from disk / read requests``, as a percentage.
    """
    requests = _number(values.get("Innodb_buffer_pool_read_requests"))
    reads = _number(values.get("Innodb_buffer_pool_reads"))
    if requests is None or reads is None or requests <= 0:
        return None
    return round(100.0 * (1 - reads / requests), 2)


def _table(item: Any) -> TableSize:
    """
    Args:
        item: A table as the engine rendered it.

    Returns:
        The table.
    """
    return TableSize(
        schema=str(item.get("schema")),
        name=str(item.get("name")),
        size_bytes=_int(item.get("size_bytes")),
        rows_estimate=_int(item.get("rows_estimate")),
    )


def _statement(item: Any) -> dict[str, Any]:
    """
    Args:
        item: A statement's statistics as the engine rendered them.

    Returns:
        Them, with the text scrubbed of quoted secrets.
    """
    return {
        "query": scrub_statement(str(item.get("query") or "")),
        "calls": _int(item.get("calls")),
        "total_ms": _number(item.get("total_ms")),
        "mean_ms": _number(item.get("mean_ms")),
        "rows": _int(item.get("rows")),
    }


# ============================================================ sampling


class DatabaseSampler:
    """
    Sample every running engine into the metrics store, once a minute.

    The metrics collector (:class:`~noust.monitor.collector.MetricsCollector`)
    ticks every five seconds; a database query per tick would be wasteful,
    so :meth:`sample` does nothing until :data:`SAMPLE_SECONDS` have passed
    since the last sample. Drive it from the collector's tick:
    ``pairs.extend(self._databases.sample(now))`` - the pairs are then
    stamped and written with the tick's own. Each engine is one query; an
    engine that fails is logged and skipped, never the tick.

    Rates (``tps``, ``qps``, ``ops``) and the interval's cache hit ratio come
    from the difference between two samples, so the first sample of a
    process has none.
    """

    def __init__(
        self,
        *,
        service: DatabaseService | None = None,
        clock: Callable[[], float] = time.monotonic,
        every: float = SAMPLE_SECONDS,
    ) -> None:
        """
        Args:
            service: The database service; one without an actor by default.
            clock: Monotonic time source, injected for tests.
            every: Seconds between samples.
        """
        self._service = service
        self._clock = clock
        self._every = every
        self._last: float | None = None
        self._counters: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def due(self, now: float | None = None) -> bool:
        """
        Args:
            now: The monotonic time; the clock's when None.

        Returns:
            Whether a sample is due.
        """
        now = self._clock() if now is None else now
        return self._last is None or now - self._last >= self._every

    def sample(self, now: float | None = None) -> list[tuple[str, float]]:
        """
        Sample every running engine, when a sample is due.

        Args:
            now: The monotonic time; the clock's when None.

        Returns:
            ``(series, value)`` pairs; empty when no sample was due.
        """
        now = self._clock() if now is None else now
        with self._lock:
            if not self.due(now):
                return []
            self._last = now
        if self._service is None and is_hub():
            # A hub has no databases of its own (central.role), and saying so
            # once a minute would only fill the journal.
            return []
        service = self._service or DatabaseService()
        pairs: list[tuple[str, float]] = []
        for manager in service.all_managers():
            if manager.ENGINE_NAME not in ("postgresql", "mysql", "redis"):
                continue
            try:
                if not manager.is_installed() or not manager.is_running():
                    continue
                pairs.extend(self._engine_pairs(DatabaseMetricsReader(service), manager, now))
            except (DatabaseError, RoleError, OSError) as exc:
                log.warning("%s metrics could not be sampled: %s", manager.DISPLAY_NAME, exc)
        return pairs

    def record(self, store: Any, now: float | None = None) -> int:
        """
        Sample and write the samples, for a caller that is not the collector.

        Args:
            store: The metrics store (``record_many``).
            now: The monotonic time.

        Returns:
            How many samples were written.
        """
        pairs = self.sample(now)
        if pairs:
            store.record_many(pairs)
        return len(pairs)

    def _rate(self, name: str, value: float | None, now: float) -> float | None:
        """
        Turn a counter into a per-second rate against the previous sample.

        Args:
            name: The counter's key.
            value: Its value now.
            now: The monotonic time.

        Returns:
            The rate, or None for the first sample or a counter that reset.
        """
        if value is None:
            return None
        previous = self._counters.get(name)
        self._counters[name] = (value, now)
        if previous is None or now <= previous[1] or value < previous[0]:
            return None
        return (value - previous[0]) / (now - previous[1])

    def _interval_ratio(
        self, name: str, hits: float | None, reads: float | None, now: float
    ) -> float | None:
        """
        The hit ratio over the interval since the previous sample.

        Args:
            name: The counters' key.
            hits: Hits so far.
            reads: Misses so far.
            now: The monotonic time.

        Returns:
            The percentage, or None when there were no reads in between.
        """
        hit_rate = self._rate(f"{name}.hits", hits, now)
        miss_rate = self._rate(f"{name}.misses", reads, now)
        return _ratio(hit_rate, miss_rate)

    def _engine_pairs(
        self, reader: DatabaseMetricsReader, manager: BaseDatabaseManager, now: float
    ) -> list[tuple[str, float]]:
        """
        Args:
            reader: The metrics reader.
            manager: A running engine's manager.
            now: The monotonic time.

        Returns:
            The engine's samples.
        """
        engine = manager.ENGINE_NAME
        pairs: list[tuple[str, float]] = []

        def add(metric: str, value: float | None, database: str | None = None) -> None:
            if value is not None:
                pairs.append((series(engine, metric, database), float(value)))

        if engine == "postgresql":
            data = load_json(
                manager.run_sql(
                    "postgres", _read_only(_PG_ENGINE), read_only=False, timeout_s=METRICS_TIMEOUT
                ),
                what="the engine's statistics",
            )
            add("connections", _number(data.get("connections")))
            for item in data.get("databases") or []:
                name = str(item.get("name") or "")
                if not NAME_PATTERN.match(name):
                    continue
                add("size", _number(item.get("size_bytes")), name)
                add("connections", _number(item.get("connections")), name)
                add(
                    "tps", self._rate(f"{engine}.{name}.xact", _number(item.get("xact")), now), name
                )
                add(
                    "cache_hit",
                    self._interval_ratio(
                        f"{engine}.{name}",
                        _number(item.get("blks_hit")),
                        _number(item.get("blks_read")),
                        now,
                    ),
                    name,
                )
            return pairs
        metrics = (
            reader._mysql_engine(manager) if engine == "mysql" else reader._redis_engine(manager)
        )
        details = metrics.details
        add("connections", metrics.connections)
        for item in metrics.databases:
            name = str(item.get("name") or "")
            if not NAME_PATTERN.match(name):
                continue
            if engine == "mysql":
                add("size", _number(item.get("size_bytes")), name)
                add("connections", _number(item.get("connections")), name)
            else:
                add("keys", _number(item.get("keys")), name)
        if engine == "mysql":
            add("qps", self._rate(f"{engine}.questions", _number(details.get("questions")), now))
            requests = _number(details.get("buffer_pool_read_requests"))
            reads = _number(details.get("buffer_pool_reads"))
            add(
                "cache_hit",
                self._interval_ratio(
                    engine,
                    None if requests is None or reads is None else requests - reads,
                    reads,
                    now,
                ),
            )
        else:
            add("memory", _number(details.get("used_memory")))
            add(
                "ops",
                self._rate(f"{engine}.commands", _number(details.get("commands_processed")), now),
            )
            add(
                "cache_hit",
                self._interval_ratio(
                    engine,
                    _number(details.get("keyspace_hits")),
                    _number(details.get("keyspace_misses")),
                    now,
                ),
            )
        return pairs
