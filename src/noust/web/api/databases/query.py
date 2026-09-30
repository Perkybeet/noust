# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/query`` and ``/api/databases/console``: the SQL console.

A database console in a server panel is a legitimate feature - it is why an
operator opens the panel instead of ssh - so it is kept, and made explicit.
Every endpoint is a client of
:class:`~noust.managers.database.console.QueryConsole`, which the CLI uses
too:

- **Read-only by default, enforced by the server.** ``mode`` defaults to
  ``read``. A read runs signed in as the database's least-privilege account
  (``wasm_ro_<database>``: SELECT on that database and nothing else) inside a
  read-only transaction. There is no keyword allow-list: a leading keyword
  does not tell what a statement does (``WITH x AS (DELETE ... RETURNING *)
  SELECT * FROM x`` begins with WITH). Read mode is offered only for engines
  whose server can hold a session read-only (the ``read_only`` capability).
  A read needs ``databases.read``.
- **One statement at a time** in read mode.
- **Writes are the sudo-mode, ``databases.write`` opt-in**, asked here
  because only the body says whether a statement writes. Under the
  ``ens-medium`` security profile a write also needs ``root_equivalent``: it
  runs as the engine's superuser. ``EXPLAIN ANALYZE`` executes the statement,
  so it is treated as a write (and rolled back).
- **A statement timeout** the server enforces, and bounded output.
- **Audited, remembered, exportable**: reads by digest, writes with their
  scrubbed text; each operator's history and saved queries; a read's full
  result as CSV or JSON.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from noust.core.accounts.policy import load_policy
from noust.core.exceptions import DatabaseNotFoundError
from noust.managers.database.base import DEFAULT_STRUCTURED_ROW_CAP
from noust.managers.database.console import (
    EXPORT_ROW_CAP,
    HISTORY_LIMIT,
    QueryConsole,
)
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import ActionResponse, service
from noust.web.api.deps import NoustErrorRoute, ensure_elevated
from noust.web.permissions import Permission
from noust.web.permissions.enforce import check_permission
from noust.web.pydantic_compat import iso_offset_validator

router = APIRouter(route_class=NoustErrorRoute)

#: The statement timeouts offered, in seconds.
TimeoutSeconds = Literal[5, 30, 120]


def ensure_may_write(http_request: Request, session: dict[str, Any]) -> None:
    """
    Hold a statement that may change data to what a write needs.

    ``databases.write`` always; ``root_equivalent`` too under the
    ``ens-medium`` profile, because the statement runs as the engine's
    superuser; and sudo mode. The permission is checked first, so a
    principal that may never write is not asked to confirm who it is.

    Args:
        http_request: The request, for the elevation's audit record.
        session: The authenticated session.

    Raises:
        PermissionDenied: 403 ``permission_denied`` for a missing permission.
        HTTPException: 403 ``elevation_required`` without sudo mode.
    """
    check_permission(session, Permission.DATABASES_WRITE)
    if load_policy().ens:
        check_permission(session, Permission.ROOT_EQUIVALENT)
    ensure_elevated(http_request, session)


def console(session: dict[str, Any]) -> QueryConsole:
    """
    Args:
        session: The authenticated session.

    Returns:
        The console of the operator behind it.
    """
    return QueryConsole(service(session))


class QueryRequest(BaseModel):
    """
    Request to run a statement.

    Attributes:
        database: Database to run against.
        engine: Engine that owns it.
        query: The statement. One statement only in read mode.
        mode: ``read`` runs it as the database's read-only account; ``write``
            is the explicit, sudo-mode opt-in for statements that change data.
        max_rows: Most lines of ``output`` to return.
        row_limit: Most rows of ``rows`` to return.
        timeout_s: Seconds the server may spend on it: 5, 30 or 120.
    """

    database: str = Field(..., description="Database name")
    engine: str = Field(..., description="Database engine")
    query: str = Field(..., description="Statement to run")
    mode: Literal["read", "write"] = Field(default="read", description="Read-only unless 'write'")
    max_rows: int = Field(default=200, ge=1, le=10_000, description="Output lines to return")
    row_limit: int = Field(
        default=DEFAULT_STRUCTURED_ROW_CAP, ge=1, le=10_000, description="Rows to return"
    )
    timeout_s: TimeoutSeconds = Field(default=30, description="Statement timeout")


class QueryResponse(BaseModel):
    """
    Result of a statement.

    Attributes:
        success: Whether the engine accepted the statement.
        output: The engine's output, truncated to ``max_rows`` lines.
        mode: The mode the statement ran in.
        truncated: Whether ``output`` or ``rows`` was cut.
        returned_rows: How many lines ``output`` carries.
        columns: Column names, in the order the engine returned them. Empty
            for an engine with no tabular client output to parse (Redis,
            MongoDB) or a statement with no result set.
        rows: Data rows, each cell a string as the client printed it, or
            null for a NULL (exact on PostgreSQL; on MySQL/MariaDB a text
            value that is the four letters ``NULL`` also reads as null).
        row_count: Number of rows in ``rows``, after truncation.
        duration_ms: Wall-clock time the query's own client invocation took.
        timeout_s: The statement timeout it ran under.
    """

    success: bool
    output: str
    mode: str
    truncated: bool = False
    returned_rows: int = 0
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str | None]] = Field(default_factory=list)
    row_count: int = 0
    duration_ms: float = 0.0
    timeout_s: int | None = None


class ExplainRequest(BaseModel):
    """
    Request for a plan.

    Attributes:
        analyze: Execute the statement to report real timings. It runs as the
            superuser inside a transaction that is rolled back, and needs
            what a write needs (``databases.write``, sudo mode).
    """

    database: str
    engine: str
    query: str
    analyze: bool = False
    timeout_s: TimeoutSeconds = 30


class ExplainResponse(BaseModel):
    """
    A plan.

    Attributes:
        format: ``json`` when ``plan`` holds the parsed plan (PostgreSQL's
            ``FORMAT JSON``, MySQL's and MariaDB's ``FORMAT=JSON``), ``text``
            for MySQL's analyzed tree.
        plan: The parsed plan, or null.
        text: The plan as the engine printed it.
        analyze: Whether the statement was executed.
    """

    format: str
    plan: Any = None
    text: str
    analyze: bool


class ExportRequest(BaseModel):
    """
    Request to export a read's result.

    Attributes:
        format: ``csv`` or ``json`` (an array of objects, a repeated column
            name suffixed ``_2``).
        row_limit: Most rows exported.
    """

    database: str
    engine: str
    query: str
    format: Literal["csv", "json"] = "csv"
    row_limit: int = Field(default=EXPORT_ROW_CAP, ge=1, le=EXPORT_ROW_CAP)
    timeout_s: TimeoutSeconds = 30


class HistoryEntryResponse(BaseModel):
    """
    One statement the operator ran.

    Attributes:
        statement: The statement, with quoted secrets and Noust's
            credentials replaced.
        kind: ``query``, ``explain``, ``explain_analyze`` or ``export``.
        outcome: ``ok`` or ``failure``.
        error: The engine's error, scrubbed and shortened.
    """

    id: int
    engine: str
    database: str
    statement: str
    mode: str
    kind: str
    outcome: str
    row_count: int | None = None
    duration_ms: float | None = None
    error: str | None = None
    created_at: str | None = None

    _iso_timestamps = iso_offset_validator("created_at")


class HistoryResponse(BaseModel):
    """The operator's statements, newest first."""

    entries: list[HistoryEntryResponse]


class SavedQueryRequest(BaseModel):
    """
    A statement to keep under a name.

    Attributes:
        database: The database it is for; null for any database of the engine.
    """

    name: str = Field(..., min_length=1, max_length=120)
    engine: str
    database: str | None = None
    query: str


class SavedQueryResponse(BaseModel):
    """
    A saved query.

    Attributes:
        database: The database it is for; empty for any.
        statement: The statement, scrubbed.
    """

    id: int
    name: str
    engine: str
    database: str
    statement: str
    created_at: str | None = None
    updated_at: str | None = None

    _iso_timestamps = iso_offset_validator("created_at", "updated_at")


class SavedQueryListResponse(BaseModel):
    """The operator's saved queries, by name."""

    queries: list[SavedQueryResponse]


class ConnectionStringRequest(BaseModel):
    """Request for a connection string."""

    database: str
    username: str
    password: str
    engine: str
    host: str = "localhost"


class ConnectionStringResponse(BaseModel):
    """
    Response with a connection string.

    The string embeds the password the caller supplied, percent-encoded, on
    the port the engine really listens on.
    """

    connection_string: str


def truncate_output(output: str, max_rows: int) -> tuple[str, bool, int]:
    """
    Cut the engine's output to a bounded number of lines.

    Args:
        output: Raw output.
        max_rows: Maximum number of lines to keep.

    Returns:
        The kept text, whether anything was dropped, and how many lines
        were kept.
    """
    lines = output.splitlines()
    if len(lines) <= max_rows:
        return output, False, len(lines)
    return "\n".join(lines[:max_rows]), True, max_rows


@router.post("/query", response_model=QueryResponse)
def execute_query(
    request: QueryRequest,
    http_request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> QueryResponse:
    """
    Run one statement against a database.

    Args:
        request: The query request.
        http_request: The incoming request, for the audit record of a write
            refused for want of elevation.
        session: The authenticated session.

    Returns:
        The result: the engine's output, truncated to ``max_rows`` lines,
        and its rows, at most ``row_limit``.

    Raises:
        DatabaseQueryError: When the statement is empty, too long, more than
            one statement in read mode, or asks for read mode on an engine
            whose server cannot hold a session read-only.
        HTTPException: 403 ``permission_denied`` for a write without
            ``databases.write`` (and ``root_equivalent`` under the ENS
            profile); 403 ``elevation_required`` for a write from a session
            that has not confirmed recently.
    """
    if request.mode == "write":
        ensure_may_write(http_request, session)
    # One execution, through the structured path alone: running the
    # plain-text path as well would run a write statement twice.
    result = console(session).run(
        request.engine,
        request.database,
        request.query,
        mode=request.mode,
        max_rows=request.row_limit,
        timeout_s=request.timeout_s,
    )
    text, truncated, lines = truncate_output(result.output, request.max_rows)
    return QueryResponse(
        success=True,
        output=text,
        mode=request.mode,
        truncated=truncated or result.truncated,
        returned_rows=lines,
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        duration_ms=result.duration_ms,
        timeout_s=result.timeout_s,
    )


@router.post("/query/explain", response_model=ExplainResponse)
def explain_query(
    request: ExplainRequest,
    http_request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> ExplainResponse:
    """
    Show how the engine would run a statement.

    Args:
        request: The statement and whether to analyze it.
        http_request: The incoming request.
        session: The authenticated session.

    Returns:
        The plan.

    Raises:
        HTTPException: 403 when ``analyze`` lacks what a write needs.
    """
    if request.analyze:
        ensure_may_write(http_request, session)
    result = console(session).explain(
        request.engine,
        request.database,
        request.query,
        analyze=request.analyze,
        timeout_s=request.timeout_s,
    )
    return ExplainResponse(
        format=result.format, plan=result.plan, text=result.text, analyze=result.analyze
    )


@router.post(
    "/query/export",
    response_class=Response,
    responses={
        200: {
            "content": {
                "text/csv": {"schema": {"type": "string"}},
                # One object per row, keyed by column name.
                "application/json": {"schema": {"type": "array", "items": {"type": "object"}}},
            },
            "description": "The result as a file; X-Noust-Rows and X-Noust-Truncated describe it.",
        }
    },
)
def export_query(
    request: ExportRequest, session: Annotated[dict, Depends(get_current_session)]
) -> Response:
    """
    Run a read again and download its whole result, up to ``row_limit`` rows.

    Only reads are exported: exporting runs the statement a second time, and
    a write must never run twice.

    Args:
        request: The statement and the format.
        session: The authenticated session.

    Returns:
        The file, as an attachment.
    """
    export = console(session).export(
        request.engine,
        request.database,
        request.query,
        fmt=request.format,
        max_rows=request.row_limit,
        timeout_s=request.timeout_s,
    )
    return Response(
        content=export.content,
        media_type=export.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename}"',
            "X-Noust-Rows": str(export.row_count),
            "X-Noust-Truncated": "true" if export.truncated else "false",
            "Cache-Control": "no-store",
        },
    )


@router.get("/console/history", response_model=HistoryResponse)
def console_history(
    session: Annotated[dict, Depends(get_current_session)],
    engine: Annotated[str | None, Query(description="Only this engine's")] = None,
    database: Annotated[str | None, Query(description="Only this database's")] = None,
    limit: Annotated[int, Query(ge=1, le=HISTORY_LIMIT)] = 50,
) -> HistoryResponse:
    """
    List the statements this operator ran, newest first.

    Args:
        session: The authenticated session; its operator's history only.
        engine: Only this engine's.
        database: Only this database's.
        limit: Most entries returned.

    Returns:
        The history.
    """
    entries = console(session).records.history(engine=engine, database=database, limit=limit)
    return HistoryResponse(entries=[HistoryEntryResponse(**entry.to_dict()) for entry in entries])


@router.delete("/console/history", response_model=ActionResponse)
def clear_console_history(
    session: Annotated[dict, Depends(get_current_session)],
    engine: Annotated[str | None, Query(description="Only this engine's")] = None,
    database: Annotated[str | None, Query(description="Only this database's")] = None,
) -> ActionResponse:
    """
    Forget statements this operator ran.

    Args:
        session: The authenticated session; its operator's history only.
        engine: Only this engine's.
        database: Only this database's.

    Returns:
        How many were forgotten.
    """
    count = console(session).records.clear(engine=engine, database=database)
    return ActionResponse(success=True, message=f"Forgot {count} statements")


@router.get("/console/saved", response_model=SavedQueryListResponse)
def list_saved_queries(
    session: Annotated[dict, Depends(get_current_session)],
    engine: Annotated[str | None, Query(description="Only this engine's")] = None,
    database: Annotated[
        str | None, Query(description="Only those for this database or for any")
    ] = None,
) -> SavedQueryListResponse:
    """
    List this operator's saved queries.

    Args:
        session: The authenticated session.
        engine: Only this engine's.
        database: Only those for this database, or for any database.

    Returns:
        The saved queries.
    """
    saved = console(session).records.saved(engine=engine, database=database)
    return SavedQueryListResponse(queries=[SavedQueryResponse(**item.to_dict()) for item in saved])


@router.post("/console/saved", response_model=SavedQueryResponse, status_code=201)
def save_query(
    request: SavedQueryRequest, session: Annotated[dict, Depends(get_current_session)]
) -> SavedQueryResponse:
    """
    Keep a statement under a name.

    Args:
        request: The name, the statement and what it is for.
        session: The authenticated session.

    Returns:
        The saved query, its statement scrubbed of quoted secrets.

    Raises:
        DatabaseExistsError: 409 when the operator has one with that name.
    """
    saved = console(session).records.save(
        name=request.name,
        engine=service(session).manager(request.engine).ENGINE_NAME,
        database=request.database,
        statement=request.query,
    )
    return SavedQueryResponse(**saved.to_dict())


@router.put("/console/saved/{saved_id}", response_model=SavedQueryResponse)
def update_saved_query(
    saved_id: int,
    request: SavedQueryRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> SavedQueryResponse:
    """
    Change one of this operator's saved queries.

    Args:
        saved_id: Its id.
        request: Its new name, statement and target.
        session: The authenticated session.

    Returns:
        The saved query.

    Raises:
        DatabaseNotFoundError: 404 when the operator has none with that id.
    """
    saved = console(session).records.save(
        name=request.name,
        engine=service(session).manager(request.engine).ENGINE_NAME,
        database=request.database,
        statement=request.query,
        saved_id=saved_id,
    )
    return SavedQueryResponse(**saved.to_dict())


@router.delete("/console/saved/{saved_id}", response_model=ActionResponse)
def delete_saved_query(
    saved_id: int, session: Annotated[dict, Depends(get_current_session)]
) -> ActionResponse:
    """
    Delete one of this operator's saved queries.

    Args:
        saved_id: Its id.
        session: The authenticated session.

    Returns:
        The outcome.

    Raises:
        DatabaseNotFoundError: 404 when the operator has none with that id.
    """
    if not console(session).records.delete(saved_id):
        raise DatabaseNotFoundError(
            f"No saved query {saved_id}", details="List them to see their ids."
        )
    return ActionResponse(success=True, message="Saved query deleted")


@router.post("/connection-string", response_model=ConnectionStringResponse)
def get_connection_string(
    request: ConnectionStringRequest, session: Annotated[dict, Depends(get_current_session)]
) -> ConnectionStringResponse:
    """
    Build a connection string from credentials the caller already has.

    Args:
        request: The connection details.
        session: The authenticated session.

    Returns:
        The connection string.
    """
    manager = service(session).manager(request.engine)
    return ConnectionStringResponse(
        connection_string=manager.get_connection_string(
            database=manager.validate_database_name(request.database),
            username=manager.validate_user_name(request.username),
            password=request.password,
            host=request.host,
        )
    )
