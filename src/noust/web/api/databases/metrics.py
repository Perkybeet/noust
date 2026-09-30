# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database metrics over HTTP: an engine's and a database's state, and slow queries.

Every endpoint is a client of
:class:`~noust.managers.database.metrics.DatabaseMetricsReader`, which the
CLI uses too. They answer with the state now; the history is in the metrics
store, under the series each answer names in ``series``, and is read with
``GET /api/metrics/query?metric=...`` like the machine's and the
applications' charts.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.managers.database.metrics import (
    DEFAULT_SLOW_QUERIES,
    MAX_SLOW_QUERIES,
    DatabaseMetricsReader,
)
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import service
from noust.web.api.deps import NoustErrorRoute

router = APIRouter(route_class=NoustErrorRoute)


class EngineMetricsResponse(BaseModel):
    """
    An engine's state now.

    Attributes:
        cache_hit_ratio: Reads served from memory since the statistics
            began, as a percentage.
        databases: Per database (per slot on Redis): ``name`` and what the
            engine reports of it (``size_bytes``, ``connections``, ``keys``).
        details: What only this engine reports: MySQL's ``slow_queries`` and
            ``questions``, Redis's ``used_memory`` and ``evicted_keys``...
        series: Chart name to the metrics store's series.
    """

    engine: str
    connections: int | None = None
    max_connections: int | None = None
    cache_hit_ratio: float | None = None
    databases: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    series: dict[str, str] = Field(default_factory=dict)


class TableSizeResponse(BaseModel):
    """One of the biggest tables."""

    schema_: str = Field(..., alias="schema")
    name: str
    size_bytes: int | None = None
    rows_estimate: int | None = None


class DatabaseMetricsResponse(BaseModel):
    """
    A database's state now.

    Attributes:
        connections: Connections to this database.
        server_connections: Connections to the whole server.
        cache_hit_ratio: Reads served from memory, as a percentage
            (server-wide on MySQL/MariaDB and Redis).
        transactions: Committed and rolled back since the statistics began
            (PostgreSQL).
        keys: Keys in the slot (Redis).
        tables: The ten biggest tables.
        series: Chart name to the metrics store's series: ``size``,
            ``connections``, and on PostgreSQL ``cache_hit`` and ``tps``.
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
    tables: list[TableSizeResponse] = Field(default_factory=list)
    series: dict[str, str] = Field(default_factory=dict)


class SlowQueryResponse(BaseModel):
    """
    One statement's statistics.

    Attributes:
        query: The statement as the engine normalises it (constants replaced),
            with quoted secrets replaced.
        mean_ms: Average time per call.
    """

    query: str
    calls: int | None = None
    total_ms: float | None = None
    mean_ms: float | None = None
    rows: int | None = None


class SlowQueriesResponse(BaseModel):
    """
    The statements that take longest on average.

    Attributes:
        available: Whether the engine keeps statement statistics.
        source: ``pg_stat_statements`` or ``performance_schema``.
        reason: Why not: ``not_loaded``, ``not_created``,
            ``performance_schema_off`` or ``not_supported``.
        how_to_enable: The steps, in English, commands included.
    """

    available: bool
    source: str | None = None
    reason: str | None = None
    how_to_enable: str | None = None
    queries: list[SlowQueryResponse] = Field(default_factory=list)


@router.get("/engines/{engine}/metrics", response_model=EngineMetricsResponse)
def engine_metrics(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> EngineMetricsResponse:
    """
    Read an engine's connections, cache hits and per-database sizes.

    Args:
        engine: The engine.
        session: The authenticated session.

    Returns:
        The metrics.
    """
    metrics = DatabaseMetricsReader(service(session)).engine_metrics(engine)
    return EngineMetricsResponse(**metrics.to_dict())


@router.get("/databases/{engine}/{name}/metrics", response_model=DatabaseMetricsResponse)
def database_metrics(
    engine: str, name: str, session: Annotated[dict, Depends(get_current_session)]
) -> DatabaseMetricsResponse:
    """
    Read a database's size, connections, cache hits and biggest tables.

    Args:
        engine: The engine.
        name: The database.
        session: The authenticated session.

    Returns:
        The metrics.
    """
    metrics = DatabaseMetricsReader(service(session)).database_metrics(engine, name)
    return DatabaseMetricsResponse(**metrics.to_dict())


@router.get("/databases/{engine}/{name}/slow-queries", response_model=SlowQueriesResponse)
def slow_queries(
    engine: str,
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    limit: Annotated[int, Query(ge=1, le=MAX_SLOW_QUERIES)] = DEFAULT_SLOW_QUERIES,
) -> SlowQueriesResponse:
    """
    List a database's slowest statements, or say how to turn their statistics on.

    Args:
        engine: The engine.
        name: The database.
        session: The authenticated session.
        limit: Statements listed.

    Returns:
        The statements, or why there are none.
    """
    answer = DatabaseMetricsReader(service(session)).slow_queries(engine, name, limit=limit)
    return SlowQueriesResponse(**answer.to_dict())
