# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The SQL console, second version: one implementation behind the CLI and the API.

What the first version did stays: **read mode is the server's to enforce**
(the database's least-privilege account inside a read-only transaction, no
keyword list), a read runs **one statement**, and every statement is
audited. This module adds what an operator expects of a console:

- **A statement timeout** the server enforces (:data:`STATEMENT_TIMEOUTS`),
  well under the 300 seconds a central's proxy gives a request, and a row
  limit on what comes back.
- **EXPLAIN**, as JSON: read-only, as the read-only account; ``ANALYZE`` only
  in write mode, where it executes the statement - inside a transaction that
  is rolled back.
- **History**, the last :data:`HISTORY_LIMIT` statements per operator and
  database, and **saved queries**, per operator. What is kept is scrubbed
  first: quoted passwords and URL credentials
  (:func:`~noust.core.audit.sanitize.scrub_statement`) and Noust's own
  credentials (:class:`~noust.core.redact.Scrubber`).
- **Export** of a read's full result as CSV or JSON, from the server, not
  from the truncated table on screen. Only reads are exported: exporting
  runs the statement again, and a write must never run twice.

Who "the operator" is comes from the audit actor (:func:`history_owner`):
the account id once accounts exist, else the token's or the session's name.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.audit import Actor
from noust.core.audit.sanitize import scrub_statement, statement_digest
from noust.core.exceptions import (
    DatabaseError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    ValidationError,
)
from noust.core.redact import Scrubber, known_credentials
from noust.core.store import NoustStore
from noust.managers.database.base import (
    DEFAULT_STRUCTURED_ROW_CAP,
    STATEMENT_TIMEOUTS,
    BaseDatabaseManager,
)
from noust.managers.database.service import DatabaseService, console_request

#: Statements kept per operator and database.
HISTORY_LIMIT = 200

#: Longest name a saved query may have.
MAX_SAVED_NAME = 120

#: Saved queries one operator may keep.
MAX_SAVED_QUERIES = 500

#: Rows an export may carry.
EXPORT_ROW_CAP = 50_000

#: The formats a result exports to.
EXPORT_FORMATS = ("csv", "json")

#: Seconds a statement may run when the caller does not say.
DEFAULT_TIMEOUT = 30

#: Characters of an engine's error kept in the history.
_ERROR_KEPT = 500

#: What the console's modes are called.
MODES = ("read", "write")


def history_owner(actor: Actor | None) -> str:
    """
    Name whose history and saved queries a request reads and writes.

    Args:
        actor: The request's or the command's actor.

    Returns:
        ``user:<account id>`` for an account; ``fleet:<operator>`` for a
        central acting for one of its operators; ``token:<name>``,
        ``master:master`` or ``cli:<login>`` otherwise.
    """
    if actor is None:
        return "system"
    if actor.kind == "user":
        return f"user:{actor.id or actor.name}"
    if actor.kind in ("fleet", "cli"):
        return f"{actor.kind}:{actor.name or actor.id}"
    return f"{actor.kind}:{actor.id or actor.name or 'unknown'}"


def check_timeout(timeout_s: int) -> int:
    """
    Args:
        timeout_s: Seconds asked for.

    Returns:
        The timeout.

    Raises:
        ValidationError: When it is not one of :data:`STATEMENT_TIMEOUTS`.
    """
    if timeout_s not in STATEMENT_TIMEOUTS:
        raise ValidationError(
            f"Unsupported statement timeout: {timeout_s} s",
            details=f"Use one of: {', '.join(str(value) for value in STATEMENT_TIMEOUTS)} seconds.",
        )
    return timeout_s


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 with its UTC offset.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _scrub(text: str) -> str:
    """
    Args:
        text: A statement or an error about to be kept.

    Returns:
        It with quoted secrets, URL passwords and Noust's credentials replaced.
    """
    return Scrubber(known_credentials()).scrub(scrub_statement(text))


# ============================================================ records


@dataclass
class HistoryEntry:
    """
    One statement an operator ran.

    Attributes:
        id: Row id.
        engine: The engine.
        database: The database.
        statement: The statement, scrubbed.
        mode: ``read`` or ``write``.
        kind: ``query``, ``explain``, ``explain_analyze`` or ``export``.
        outcome: ``ok`` or ``failure``.
        row_count: Rows it returned.
        duration_ms: How long it took.
        error: The engine's error, scrubbed and shortened.
        created_at: When it ran.
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

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The entry as plain data.
        """
        return asdict(self)


@dataclass
class SavedQuery:
    """
    A statement an operator kept under a name.

    Attributes:
        id: Row id.
        name: Its name.
        engine: The engine it is for.
        database: The database it is for; empty for any database.
        statement: The statement, scrubbed.
        created_at: When it was saved.
        updated_at: When it last changed.
    """

    id: int
    name: str
    engine: str
    database: str
    statement: str
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The saved query as plain data.
        """
        return asdict(self)


class QueryRecords:
    """
    The history and saved-query tables, through the store's transactions.

    Every read and write is scoped to one owner: nothing here returns or
    changes another operator's rows.
    """

    def __init__(self, store: NoustStore, owner: str) -> None:
        """
        Args:
            store: The store.
            owner: Whose rows these are (:func:`history_owner`).
        """
        self.store = store
        self.owner = owner

    # ------------------------------------------------------------ history

    def add(
        self,
        *,
        engine: str,
        database: str,
        statement: str,
        mode: str,
        kind: str = "query",
        outcome: str = "ok",
        row_count: int | None = None,
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        """
        Keep one statement, and forget the oldest beyond :data:`HISTORY_LIMIT`.

        Args:
            engine: The engine.
            database: The database.
            statement: The statement; scrubbed here.
            mode: ``read`` or ``write``.
            kind: What ran it.
            outcome: ``ok`` or ``failure``.
            row_count: Rows it returned.
            duration_ms: How long it took.
            error: The engine's error; scrubbed and shortened here.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO database_query_history (owner, engine, db_name, statement, mode, "
                "kind, outcome, row_count, duration_ms, error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.owner,
                    engine,
                    database,
                    _scrub(statement),
                    mode,
                    kind,
                    outcome,
                    row_count,
                    duration_ms,
                    _scrub(error)[:_ERROR_KEPT] if error else None,
                    _now(),
                ),
            )
            cursor.execute(
                "DELETE FROM database_query_history WHERE owner = ? AND engine = ? "
                "AND db_name = ? AND id NOT IN (SELECT id FROM database_query_history "
                "WHERE owner = ? AND engine = ? AND db_name = ? ORDER BY id DESC LIMIT ?)",
                (self.owner, engine, database, self.owner, engine, database, HISTORY_LIMIT),
            )

    def history(
        self, *, engine: str | None = None, database: str | None = None, limit: int = 50
    ) -> list[HistoryEntry]:
        """
        Read the owner's statements, newest first.

        Args:
            engine: Only this engine's.
            database: Only this database's.
            limit: Most entries returned.

        Returns:
            The entries.
        """
        query = "SELECT * FROM database_query_history WHERE owner = ?"
        params: list[object] = [self.owner]
        if engine is not None:
            query += " AND engine = ?"
            params.append(engine)
        if database is not None:
            query += " AND db_name = ?"
            params.append(database)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, HISTORY_LIMIT)))
        with self.store._transaction() as cursor:
            cursor.execute(query, params)
            return [_history_entry(row) for row in cursor.fetchall()]

    def clear(self, *, engine: str | None = None, database: str | None = None) -> int:
        """
        Forget the owner's statements.

        Args:
            engine: Only this engine's.
            database: Only this database's.

        Returns:
            How many were forgotten.
        """
        query = "DELETE FROM database_query_history WHERE owner = ?"
        params: list[object] = [self.owner]
        if engine is not None:
            query += " AND engine = ?"
            params.append(engine)
        if database is not None:
            query += " AND db_name = ?"
            params.append(database)
        with self.store._transaction() as cursor:
            cursor.execute(query, params)
            return int(cursor.rowcount)

    # ------------------------------------------------------------ saved

    def saved(self, *, engine: str | None = None, database: str | None = None) -> list[SavedQuery]:
        """
        List the owner's saved queries.

        Args:
            engine: Only this engine's.
            database: Only those for this database, or for any database.

        Returns:
            The saved queries, by name.
        """
        query = "SELECT * FROM database_saved_queries WHERE owner = ?"
        params: list[object] = [self.owner]
        if engine is not None:
            query += " AND engine = ?"
            params.append(engine)
        if database is not None:
            query += " AND db_name IN (?, '')"
            params.append(database)
        with self.store._transaction() as cursor:
            cursor.execute(query + " ORDER BY name COLLATE NOCASE", params)
            return [_saved_query(row) for row in cursor.fetchall()]

    def get(self, saved_id: int) -> SavedQuery:
        """
        Read one of the owner's saved queries.

        Args:
            saved_id: Its id.

        Returns:
            It.

        Raises:
            DatabaseNotFoundError: When the owner has none with that id.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM database_saved_queries WHERE owner = ? AND id = ?",
                (self.owner, saved_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise DatabaseNotFoundError(
                f"No saved query {saved_id}", details="List them to see their ids."
            )
        return _saved_query(row)

    def save(
        self,
        *,
        name: str,
        engine: str,
        database: str | None,
        statement: str,
        saved_id: int | None = None,
    ) -> SavedQuery:
        """
        Save a query under a name, or change one.

        Args:
            name: Its name.
            engine: The engine it is for.
            database: The database it is for; None for any.
            statement: The statement; scrubbed here.
            saved_id: The saved query to change; a new one when None.

        Returns:
            It, as stored.

        Raises:
            ValidationError: When the name or the statement is empty or too
                long, or the owner has too many.
            DatabaseExistsError: When the owner has another with that name.
            DatabaseNotFoundError: When ``saved_id`` is not the owner's.
        """
        name = name.strip()
        if not name or len(name) > MAX_SAVED_NAME or "\x00" in name:
            raise ValidationError(
                f"A saved query's name has from 1 to {MAX_SAVED_NAME} characters",
                details="Choose a shorter name.",
            )
        statement = console_request(statement, single=False)
        now = _now()
        values = (name, engine, database or "", _scrub(statement), now)
        try:
            with self.store._transaction() as cursor:
                if saved_id is None:
                    cursor.execute(
                        "SELECT COUNT(*) FROM database_saved_queries WHERE owner = ?",
                        (self.owner,),
                    )
                    if cursor.fetchone()[0] >= MAX_SAVED_QUERIES:
                        raise ValidationError(
                            f"At most {MAX_SAVED_QUERIES} saved queries per operator",
                            details="Delete the ones you no longer use.",
                        )
                    cursor.execute(
                        "INSERT INTO database_saved_queries (owner, name, engine, db_name, "
                        "statement, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (self.owner, *values, now),
                    )
                    saved_id = int(cursor.lastrowid or 0)
                else:
                    cursor.execute(
                        "UPDATE database_saved_queries SET name = ?, engine = ?, db_name = ?, "
                        "statement = ?, updated_at = ? WHERE owner = ? AND id = ?",
                        (*values, self.owner, saved_id),
                    )
                    if cursor.rowcount == 0:
                        raise DatabaseNotFoundError(
                            f"No saved query {saved_id}", details="List them to see their ids."
                        )
        except sqlite3.IntegrityError as exc:
            raise DatabaseExistsError(
                f"A saved query called {name!r} already exists",
                details="Choose another name, or change the existing one.",
            ) from exc
        return self.get(saved_id)

    def delete(self, saved_id: int) -> bool:
        """
        Delete one of the owner's saved queries.

        Args:
            saved_id: Its id.

        Returns:
            Whether one was deleted.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM database_saved_queries WHERE owner = ? AND id = ?",
                (self.owner, saved_id),
            )
            return cursor.rowcount > 0


def _history_entry(row: sqlite3.Row) -> HistoryEntry:
    """
    Args:
        row: A ``database_query_history`` row.

    Returns:
        The entry.
    """
    data = dict(row)
    return HistoryEntry(
        id=data["id"],
        engine=data["engine"],
        database=data["db_name"],
        statement=data["statement"],
        mode=data["mode"],
        kind=data["kind"],
        outcome=data["outcome"],
        row_count=data["row_count"],
        duration_ms=data["duration_ms"],
        error=data["error"],
        created_at=data["created_at"],
    )


def _saved_query(row: sqlite3.Row) -> SavedQuery:
    """
    Args:
        row: A ``database_saved_queries`` row.

    Returns:
        The saved query.
    """
    data = dict(row)
    return SavedQuery(
        id=data["id"],
        name=data["name"],
        engine=data["engine"],
        database=data["db_name"],
        statement=data["statement"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
    )


# ============================================================ running


@dataclass
class ConsoleResult:
    """
    What one console statement returned.

    Attributes:
        mode: ``read`` or ``write``.
        output: The client's own output, verbatim.
        columns: Column names, in order; empty for an engine with no tabular
            output or a statement with no result set.
        rows: Rows, each cell a string or None for a NULL.
        row_count: Rows in ``rows``.
        truncated: Whether rows beyond the limit were dropped.
        duration_ms: How long the client took.
        timeout_s: The statement timeout it ran under.
    """

    mode: str
    output: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[str | None]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: float = 0.0
    timeout_s: int | None = None


@dataclass
class ExplainResult:
    """
    A plan.

    Attributes:
        format: ``json`` when ``plan`` holds the parsed plan, ``text``
            otherwise (MySQL's analyzed tree).
        plan: The parsed JSON plan, or None.
        text: The plan as the engine printed it.
        analyze: Whether the statement was executed to time it.
    """

    format: str
    plan: Any
    text: str
    analyze: bool


@dataclass
class Export:
    """
    A result rendered as a file.

    Attributes:
        content: The file's text.
        media_type: Its MIME type.
        filename: A name for it.
        row_count: Rows it holds.
        truncated: Whether rows beyond :data:`EXPORT_ROW_CAP` were left out.
    """

    content: str
    media_type: str
    filename: str
    row_count: int
    truncated: bool


class QueryConsole:
    """
    Run, explain and export console statements, and keep their history.

    Construct one per request or command.
    """

    def __init__(self, service: DatabaseService, *, owner: str | None = None) -> None:
        """
        Args:
            service: The database service: engines, the actor, the audit trail.
            owner: Whose history the statements go to; derived from the
                service's actor when None.
        """
        self.service = service
        self.owner = owner or history_owner(service.actor)

    @property
    def records(self) -> QueryRecords:
        """This operator's history and saved queries."""
        return QueryRecords(self.service.store, self.owner)

    def _open(self, engine: str, database: str, *, mode: str) -> tuple[BaseDatabaseManager, str]:
        """
        Resolve a running engine and check a read can be held read-only.

        Args:
            engine: The engine.
            database: The database.
            mode: ``read`` or ``write``.

        Returns:
            The manager and the validated database name.

        Raises:
            ValidationError: When the mode is unknown.
            DatabaseQueryError: When read mode is asked of an engine whose
                server cannot hold a session read-only.
        """
        if mode not in MODES:
            raise ValidationError(f"Unknown mode: {mode!r}", details="Use 'read' or 'write'.")
        manager = self.service.running(engine)
        if mode == "read" and "read_only" not in manager.CAPABILITIES:
            raise DatabaseQueryError(
                f"Read-only mode is not available for {manager.DISPLAY_NAME}",
                details=(
                    "Noust only runs a statement read-only where the database server itself can "
                    "hold the session read-only (PostgreSQL and MySQL/MariaDB). Send "
                    "mode='write' to run this statement, knowing it may change data."
                ),
            )
        return manager, manager.validate_database_name(database)

    def _audit(
        self, manager: BaseDatabaseManager, database: str, statement: str, *, mode: str, kind: str
    ) -> None:
        """
        Record a statement before it runs.

        A read is recorded by its digest (length and SHA-256): reads are
        many, and a statement can carry a value the operator would not want
        kept. A write, which is sudo-mode and may be root-equivalent, is
        recorded with its text, scrubbed of quoted secrets.

        Args:
            manager: The engine's manager.
            database: The database.
            statement: The statement.
            mode: ``read`` or ``write``.
            kind: ``query``, ``explain``, ``explain_analyze`` or ``export``.
        """
        target = f"{manager.ENGINE_NAME}/{database}"
        if mode == "read":
            digest = statement_digest(statement)
            self.service.audit(
                "db.query.export" if kind == "export" else "db.query.read",
                target,
                mode=mode,
                kind=kind,
                statement_sha256=digest["sha256"],
                length=digest["length"],
            )
            return
        self.service.audit(
            "db.query",
            target,
            mode=mode,
            kind=kind,
            statement=scrub_statement(statement),
            statement_sha256=statement_digest(statement)["sha256"],
            length=len(statement),
        )

    def _remember(
        self,
        engine: str,
        database: str,
        statement: str,
        *,
        mode: str,
        kind: str,
        error: DatabaseError | None = None,
        row_count: int | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """
        Keep a statement in the operator's history, never failing the statement for it.

        Args:
            engine: The engine.
            database: The database.
            statement: The statement.
            mode: ``read`` or ``write``.
            kind: What ran it.
            error: The failure, when it failed.
            row_count: Rows it returned.
            duration_ms: How long it took.
        """
        try:
            self.records.add(
                engine=engine,
                database=database,
                statement=statement,
                mode=mode,
                kind=kind,
                outcome="failure" if error else "ok",
                row_count=row_count,
                duration_ms=duration_ms,
                error=(error.output or error.details or error.message) if error else None,
            )
        except sqlite3.Error as exc:
            self.service.logger.warning(f"Could not keep the statement in the history: {exc}")

    def run(
        self,
        engine: str,
        database: str,
        query: str,
        *,
        mode: str = "read",
        max_rows: int = DEFAULT_STRUCTURED_ROW_CAP,
        timeout_s: int = DEFAULT_TIMEOUT,
    ) -> ConsoleResult:
        """
        Run one console statement.

        Args:
            engine: The engine.
            database: The database.
            query: The statement. One statement in read mode.
            mode: ``read`` (the database's read-only account, a read-only
                transaction) or ``write``. Callers check sudo mode and the
                permission first: the API does, the CLI runs as root.
            max_rows: Rows returned at most.
            timeout_s: Seconds the server may spend.

        Returns:
            The result.

        Raises:
            DatabaseQueryError: When the statement is refused or fails, with
                the engine's own words.
        """
        manager, database = self._open(engine, database, mode=mode)
        check_timeout(timeout_s)
        read_only = mode == "read"
        statement = console_request(query, single=read_only)
        self._audit(manager, database, statement, mode=mode, kind="query")
        try:
            structured = manager.execute_query_structured(
                database,
                statement,
                read_only=read_only,
                max_rows=max_rows,
                timeout_s=timeout_s,
            )
        except DatabaseError as exc:
            self._remember(
                manager.ENGINE_NAME, database, statement, mode=mode, kind="query", error=exc
            )
            raise
        self._remember(
            manager.ENGINE_NAME,
            database,
            statement,
            mode=mode,
            kind="query",
            row_count=structured.row_count,
            duration_ms=structured.duration_ms,
        )
        return ConsoleResult(
            mode=mode,
            output=structured.output,
            columns=structured.columns,
            rows=structured.rows,
            row_count=structured.row_count,
            truncated=structured.truncated,
            duration_ms=structured.duration_ms,
            timeout_s=timeout_s,
        )

    def explain(
        self,
        engine: str,
        database: str,
        query: str,
        *,
        analyze: bool = False,
        timeout_s: int = DEFAULT_TIMEOUT,
    ) -> ExplainResult:
        """
        Show how the engine would run a statement.

        Args:
            engine: The engine.
            database: The database.
            query: The statement.
            analyze: Execute it, as the superuser, inside a transaction that
                is rolled back, to report real timings. Callers treat it as
                write mode (sudo mode, ``databases.write``).
            timeout_s: Seconds the server may spend.

        Returns:
            The plan.

        Raises:
            DatabaseQueryError: When the engine has no EXPLAIN, or refuses.
        """
        mode = "write" if analyze else "read"
        manager, database = self._open(engine, database, mode=mode)
        check_timeout(timeout_s)
        statement = console_request(query, single=True)
        kind = "explain_analyze" if analyze else "explain"
        self._audit(manager, database, statement, mode=mode, kind=kind)
        try:
            text = manager.explain(database, statement, analyze=analyze, timeout_s=timeout_s)
        except DatabaseError as exc:
            self._remember(
                manager.ENGINE_NAME, database, statement, mode=mode, kind=kind, error=exc
            )
            raise
        self._remember(manager.ENGINE_NAME, database, statement, mode=mode, kind=kind)
        try:
            plan = json.loads(text)
        except ValueError:
            return ExplainResult(format="text", plan=None, text=text, analyze=analyze)
        return ExplainResult(format="json", plan=plan, text=text, analyze=analyze)

    def export(
        self,
        engine: str,
        database: str,
        query: str,
        *,
        fmt: str = "csv",
        max_rows: int = EXPORT_ROW_CAP,
        timeout_s: int = DEFAULT_TIMEOUT,
    ) -> Export:
        """
        Run a read and render its whole result (up to a cap) as a file.

        Args:
            engine: The engine.
            database: The database.
            query: The statement; read mode only.
            fmt: ``csv`` or ``json``.
            max_rows: Rows exported at most, up to :data:`EXPORT_ROW_CAP`.
            timeout_s: Seconds the server may spend.

        Returns:
            The file.

        Raises:
            ValidationError: When the format or the row cap is not acceptable.
            DatabaseQueryError: When the statement is refused or fails.
        """
        if fmt not in EXPORT_FORMATS:
            raise ValidationError(
                f"Unknown export format: {fmt!r}",
                details=f"Use one of: {', '.join(EXPORT_FORMATS)}.",
            )
        if max_rows < 1 or max_rows > EXPORT_ROW_CAP:
            raise ValidationError(
                f"An export holds from 1 to {EXPORT_ROW_CAP} rows",
                details="Narrow the statement, or export it in parts.",
            )
        manager, database = self._open(engine, database, mode="read")
        check_timeout(timeout_s)
        statement = console_request(query, single=True)
        self._audit(manager, database, statement, mode="read", kind="export")
        try:
            result = manager.execute_query_structured(
                database, statement, read_only=True, max_rows=max_rows, timeout_s=timeout_s
            )
        except DatabaseError as exc:
            self._remember(
                manager.ENGINE_NAME, database, statement, mode="read", kind="export", error=exc
            )
            raise
        self._remember(
            manager.ENGINE_NAME,
            database,
            statement,
            mode="read",
            kind="export",
            row_count=result.row_count,
            duration_ms=result.duration_ms,
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"{manager.ENGINE_NAME}-{database}-{stamp}.{fmt}"
        if fmt == "csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(result.columns)
            writer.writerows(["" if cell is None else cell for cell in row] for row in result.rows)
            content, media_type = buffer.getvalue(), "text/csv; charset=utf-8"
        else:
            names = _unique(result.columns)
            content = json.dumps(
                [dict(zip(names, row, strict=False)) for row in result.rows],
                ensure_ascii=False,
                indent=1,
            )
            media_type = "application/json"
        return Export(
            content=content,
            media_type=media_type,
            filename=filename,
            row_count=result.row_count,
            truncated=result.truncated,
        )


def _unique(columns: list[str]) -> list[str]:
    """
    Make column names usable as JSON keys: a repeated name gets ``_2``, ``_3``.

    Args:
        columns: The names, as the engine returned them.

    Returns:
        Distinct names, in order.
    """
    seen: dict[str, int] = {}
    names: list[str] = []
    for column in columns:
        count = seen.get(column, 0) + 1
        seen[column] = count
        names.append(column if count == 1 else f"{column}_{count}")
    return names
