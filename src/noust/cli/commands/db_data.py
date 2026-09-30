# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db tables``, ``rows``, ``row``, ``keys``, ``explain``, ``export``,
``history``, ``saved``, ``metrics``: a database's data from the terminal.

The other client of the data explorer
(:class:`~noust.managers.database.browse.DataBrowser`), the key browser
(:class:`~noust.managers.database.keys.KeyBrowser`), the console
(:class:`~noust.managers.database.console.QueryConsole`) and the metrics
(:class:`~noust.managers.database.metrics.DatabaseMetricsReader`), after
``/api/databases``: same reads as the database's read-only account, same
one-row edits, same history. This module parses arguments and prints.

``noust db`` (:mod:`noust.cli.commands.db`) calls :func:`register`, so the
commands join its group without this module importing that one at load time.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustCommand, NoustGroup, json_option, pass_context
from noust.core.audit.actor import cli_actor
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.managers.database.base import STATEMENT_TIMEOUTS
from noust.managers.database.browse import DataBrowser
from noust.managers.database.console import (
    EXPORT_FORMATS,
    EXPORT_ROW_CAP,
    HISTORY_LIMIT,
    QueryConsole,
    history_owner,
)
from noust.managers.database.dialects import DEFAULT_PAGE_SIZE, Filter, Order
from noust.managers.database.keys import DEFAULT_SCAN_COUNT, KeyBrowser
from noust.managers.database.metrics import DEFAULT_SLOW_QUERIES, DatabaseMetricsReader
from noust.managers.database.service import DatabaseService


class _Engine(click.ParamType):
    """An engine name, checked by ``noust db``'s own engine type at call time."""

    name = "engine"

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> str:
        """
        Args:
            value: What the operator typed.
            param: The parameter being converted.
            ctx: The Click context.

        Returns:
            The name, once the registry knows it.
        """
        from noust.cli.commands.db import ENGINE

        return str(ENGINE.convert(value, param, ctx))


ENGINE = _Engine()

#: The statement timeouts offered, in seconds.
TIMEOUT = click.Choice([str(value) for value in STATEMENT_TIMEOUTS])


def _service(logger: Logger) -> DatabaseService:
    """
    Args:
        logger: Where progress is reported.

    Returns:
        ``noust db``'s service, so engines resolve the way every command does.
    """
    from noust.cli.commands.db import _service as db_service

    return db_service(logger)


def _console(logger: Logger) -> QueryConsole:
    """
    Args:
        logger: Where progress is reported.

    Returns:
        The console of whoever runs this command (``cli:<login>``).
    """
    return QueryConsole(_service(logger), owner=history_owner(cli_actor()))


def _done(ctx: Context, run: Any) -> None:
    """
    Run a command body at the CLI's error boundary.

    Args:
        ctx: The command's context.
        run: The body; returns the exit code.
    """
    try:
        code = run()
    except NoustError as exc:
        ctx.logger.error(str(exc))
        if exc.output:
            click.echo(exc.output, err=True)
        code = 1
    click.get_current_context().exit(code or 0)


def _json(data: Any) -> None:
    """
    Args:
        data: JSON-serialisable data.
    """
    click.echo(json.dumps(data, indent=2, default=str))


def _default_schema(logger: Logger, engine: str, database: str, schema: str | None) -> str:
    """
    Args:
        logger: The logger.
        engine: The engine.
        database: The database.
        schema: The schema the operator named.

    Returns:
        It, or the engine's default: ``public`` on PostgreSQL, the database
        itself on MySQL/MariaDB.
    """
    if schema:
        return schema
    return database if _service(logger).manager(engine).ENGINE_NAME == "mysql" else "public"


def _pairs(values: tuple[str, ...], option: str) -> dict[str, Any]:
    """
    Read ``column=value`` options; ``column:=json`` takes a JSON value.

    Args:
        values: The options.
        option: Its name, for the error.

    Returns:
        Column to value.

    Raises:
        ValidationError: When one has no ``=`` or its JSON does not parse.
    """
    parsed: dict[str, Any] = {}
    for item in values:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise ValidationError(
                f"Invalid {option} {item!r}", details=f"Write {option} column=value."
            )
        if name.endswith(":"):
            try:
                parsed[name[:-1]] = json.loads(value)
            except ValueError as exc:
                raise ValidationError(f"Invalid JSON for {name[:-1]!r}", details=str(exc)) from exc
        else:
            parsed[name] = value
    return parsed


def _cell(value: Any) -> str:
    """
    Args:
        value: A typed cell.

    Returns:
        How the terminal shows it: NULL apart from an empty string.
    """
    if value is None:
        return "NULL"
    if isinstance(value, dict) and "bytes" in value:
        return f"<{value['bytes']} bytes 0x{value.get('hex', '')}>"
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value)
    return str(value)


def _table(headers: list[str], rows: list[list[str]]) -> None:
    """
    Print a plain, aligned table.

    Args:
        headers: Column titles.
        rows: Cells.
    """
    widths = [len(title) for title in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = min(max(widths[index], len(cell)), 60)
    line = "  ".join(title.ljust(widths[i]) for i, title in enumerate(headers))
    click.echo(line.rstrip())
    for row in rows:
        click.echo("  ".join(cell[:60].ljust(widths[i]) for i, cell in enumerate(row)).rstrip())


# ============================================================ explorer


@click.command("tables", cls=NoustCommand, read_only=True)
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--schema", help="Only this schema's.")
@click.option("--search", help="Only names containing this.")
@click.option("--kind", type=click.Choice(["table", "view", "materialized_view", "foreign_table"]))
@json_option("Print the tables as JSON.")
@pass_context
def tables(
    ctx: Context,
    database: str,
    engine: str,
    schema: str | None,
    search: str | None,
    kind: str | None,
) -> None:
    """List a database's tables and views, with estimated rows and sizes."""

    def run() -> int:
        relations = DataBrowser(_service(ctx.logger)).relations(
            engine, database, schema=schema, search=search, kind=kind
        )
        if ctx.json_output:
            _json([relation.to_dict() for relation in relations])
            return 0
        _table(
            ["SCHEMA", "NAME", "KIND", "ROWS (EST.)", "SIZE (BYTES)"],
            [
                [
                    relation.schema,
                    relation.name,
                    relation.kind,
                    "" if relation.rows_estimate is None else str(relation.rows_estimate),
                    "" if relation.size_bytes is None else str(relation.size_bytes),
                ]
                for relation in relations
            ],
        )
        return 0

    _done(ctx, run)


@click.command("describe", cls=NoustCommand, read_only=True)
@click.argument("database")
@click.argument("table")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--schema", help="Schema. Defaults to public, or the database on MySQL.")
@json_option("Print the structure as JSON.")
@pass_context
def describe(ctx: Context, database: str, table: str, engine: str, schema: str | None) -> None:
    """Show a table's columns, primary key, indexes and constraints."""

    def run() -> int:
        detail = DataBrowser(_service(ctx.logger)).describe(
            engine, database, _default_schema(ctx.logger, engine, database, schema), table
        )
        if ctx.json_output:
            _json(detail.to_dict())
            return 0
        _table(
            ["COLUMN", "TYPE", "NULL", "KEY", "DEFAULT"],
            [
                [
                    column.name,
                    column.type,
                    "yes" if column.nullable else "no",
                    f"PK{column.primary_key}" if column.primary_key else "",
                    column.default or "",
                ]
                for column in detail.columns
            ],
        )
        for index in detail.indexes:
            click.echo(f"index {index.name}: {index.definition or ', '.join(index.columns)}")
        if not detail.editable:
            click.echo("Read-only here: the table has no primary key.")
        return 0

    _done(ctx, run)


@click.command("rows", cls=NoustCommand, read_only=True)
@click.argument("database")
@click.argument("table")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--schema", help="Schema. Defaults to public, or the database on MySQL.")
@click.option("--limit", type=int, default=DEFAULT_PAGE_SIZE, show_default=True)
@click.option("--offset", type=int, default=0, help="Rows to skip (sorted by other columns).")
@click.option("--cursor", help="Continue after a previous page (next cursor).")
@click.option("--order", multiple=True, help="column or column:desc; repeatable.")
@click.option(
    "--filter",
    "filters",
    multiple=True,
    help="column:op:value (eq neq lt lte gt gte like ilike in null notnull).",
)
@click.option("--count", is_flag=True, help="Also count every matching row.")
@click.option("--timeout", type=TIMEOUT, default="30", show_default=True, help="Seconds.")
@json_option("Print the page as JSON.")
@pass_context
def rows(
    ctx: Context,
    database: str,
    table: str,
    engine: str,
    schema: str | None,
    limit: int,
    offset: int,
    cursor: str | None,
    order: tuple[str, ...],
    filters: tuple[str, ...],
    count: bool,
    timeout: str,
) -> None:
    """Read a page of a table's rows, as the database's read-only account."""

    def run() -> int:
        page = DataBrowser(_service(ctx.logger)).rows(
            engine,
            database,
            _default_schema(ctx.logger, engine, database, schema),
            table,
            filters=[Filter.parse(text) for text in filters],
            order=[Order.parse(text) for text in order],
            limit=limit,
            offset=offset,
            cursor=cursor,
            count=count,
            timeout_s=int(timeout),
        )
        if ctx.json_output:
            _json(
                {
                    "columns": [column.to_dict() for column in page.columns],
                    "rows": page.rows,
                    "truncated": page.truncated,
                    "keys": page.keys,
                    "next_cursor": page.next_cursor,
                    "next_offset": page.next_offset,
                    "count": page.count,
                }
            )
            return 0
        _table(
            [column.name for column in page.columns],
            [[_cell(value) for value in row] for row in page.rows],
        )
        if page.count is not None:
            click.echo(f"{page.count} matching rows")
        if page.next_cursor:
            click.echo(f"Next page: --cursor {page.next_cursor}")
        elif page.next_offset is not None:
            click.echo(f"Next page: --offset {page.next_offset}")
        return 0

    _done(ctx, run)


@click.group("row", cls=NoustGroup)
def row() -> None:
    """Insert, change or delete one row, found by its whole primary key."""


def _edit_options(command: Any) -> Any:
    """
    Args:
        command: A ``row`` subcommand.

    Returns:
        It with the options every edit takes.
    """
    for option in reversed(
        [
            click.argument("database"),
            click.argument("table"),
            click.option(
                "--engine", "-e", type=ENGINE, required=True, help="Engine the database is on."
            ),
            click.option("--schema", help="Schema. Defaults to public, or the database on MySQL."),
            click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation."),
        ]
    ):
        command = option(command)
    return command


def _print_change(ctx: Context, change: Any) -> None:
    """
    Args:
        ctx: The command's context.
        change: The :class:`~noust.managers.database.browse.RowChange`.
    """
    if ctx.json_output:
        _json(change.to_dict())
        return
    ctx.logger.success(
        f"{change.action.capitalize()}d one row of {change.schema}.{change.relation}"
    )
    if change.before is not None:
        click.echo(f"before: {json.dumps(change.before, default=str)}")
    if change.after is not None:
        click.echo(f"after:  {json.dumps(change.after, default=str)}")


@row.command("insert")
@_edit_options
@click.option("--set", "values", multiple=True, help="column=value, or column:=<json>; repeatable.")
@json_option("Print the change as JSON.")
@pass_context
def row_insert(
    ctx: Context,
    database: str,
    table: str,
    engine: str,
    schema: str | None,
    force: bool,
    values: tuple[str, ...],
) -> None:
    """Insert one row; columns left out take their defaults."""
    from noust.cli.commands.db import _confirm

    def run() -> int:
        if not _confirm(f"Insert a row into {table} of {database}?", force=force):
            ctx.logger.info("Nothing was changed")
            return 1
        change = DataBrowser(_service(ctx.logger)).insert_row(
            engine,
            database,
            _default_schema(ctx.logger, engine, database, schema),
            table,
            _pairs(values, "--set"),
        )
        _print_change(ctx, change)
        return 0

    _done(ctx, run)


@row.command("update")
@_edit_options
@click.option("--key", "key", multiple=True, required=True, help="column=value of the primary key.")
@click.option(
    "--set", "values", multiple=True, required=True, help="column=value, or column:=<json>."
)
@json_option("Print the change as JSON.")
@pass_context
def row_update(
    ctx: Context,
    database: str,
    table: str,
    engine: str,
    schema: str | None,
    force: bool,
    key: tuple[str, ...],
    values: tuple[str, ...],
) -> None:
    """Change one row; nothing changes unless exactly that row exists."""
    from noust.cli.commands.db import _confirm

    def run() -> int:
        identity = _pairs(key, "--key")
        if not _confirm(f"Change the row {identity} of {table} in {database}?", force=force):
            ctx.logger.info("Nothing was changed")
            return 1
        change = DataBrowser(_service(ctx.logger)).update_row(
            engine,
            database,
            _default_schema(ctx.logger, engine, database, schema),
            table,
            identity,
            _pairs(values, "--set"),
        )
        _print_change(ctx, change)
        return 0

    _done(ctx, run)


@row.command("delete")
@_edit_options
@click.option("--key", "key", multiple=True, required=True, help="column=value of the primary key.")
@json_option("Print the change as JSON.")
@pass_context
def row_delete(
    ctx: Context,
    database: str,
    table: str,
    engine: str,
    schema: str | None,
    force: bool,
    key: tuple[str, ...],
) -> None:
    """Delete one row; nothing changes unless exactly that row exists."""
    from noust.cli.commands.db import _confirm

    def run() -> int:
        identity = _pairs(key, "--key")
        if not _confirm(f"Delete the row {identity} of {table} in {database}?", force=force):
            ctx.logger.info("Nothing was changed")
            return 1
        change = DataBrowser(_service(ctx.logger)).delete_row(
            engine,
            database,
            _default_schema(ctx.logger, engine, database, schema),
            table,
            identity,
        )
        _print_change(ctx, change)
        return 0

    _done(ctx, run)


# ============================================================ Redis keys


@click.command("keys", cls=NoustCommand, read_only=True)
@click.argument("slot", default="0")
@click.option("--engine", "-e", type=ENGINE, default="redis", show_default=True)
@click.option("--match", help="Glob pattern, such as 'session:*'.")
@click.option("--cursor", default="0", show_default=True, help="Continue a scan.")
@click.option("--count", type=int, default=DEFAULT_SCAN_COUNT, show_default=True)
@click.option(
    "--type", "key_type", type=click.Choice(["string", "list", "set", "zset", "hash", "stream"])
)
@json_option("Print the keys as JSON.")
@pass_context
def keys(
    ctx: Context,
    slot: str,
    engine: str,
    match: str | None,
    cursor: str,
    count: int,
    key_type: str | None,
) -> None:
    """Scan a Redis database's keys, with their type, TTL and memory."""

    def run() -> int:
        page = KeyBrowser(_service(ctx.logger)).scan(
            engine, slot, match=match, cursor=cursor, count=count, key_type=key_type
        )
        if ctx.json_output:
            _json(
                {
                    "keys": [key.to_dict() for key in page.keys],
                    "cursor": page.cursor,
                    "read_only_enforced": page.read_only_enforced,
                }
            )
            return 0
        _table(
            ["KEY", "TYPE", "TTL", "MEMORY"],
            [
                [
                    key.key,
                    key.type,
                    "" if key.ttl is None else str(key.ttl),
                    "" if key.memory is None else str(key.memory),
                ]
                for key in page.keys
            ],
        )
        if not page.done:
            click.echo(f"More keys: --cursor {page.cursor}")
        return 0

    _done(ctx, run)


@click.command("key", cls=NoustCommand, read_only=True)
@click.argument("slot")
@click.argument("key")
@click.option("--engine", "-e", type=ENGINE, default="redis", show_default=True)
@click.option("--hex", "is_hex", is_flag=True, help="KEY is the key's bytes in hexadecimal.")
@json_option("Print the preview as JSON.")
@pass_context
def key(ctx: Context, slot: str, key: str, engine: str, is_hex: bool) -> None:
    """Show a bounded preview of one Redis key."""

    def run() -> int:
        browser = KeyBrowser(_service(ctx.logger))
        value = (
            browser.preview(engine, slot, key_hex=key)
            if is_hex
            else browser.preview(engine, slot, key)
        )
        if ctx.json_output:
            _json(value.to_dict())
            return 0
        ttl = "never expires" if value.ttl is None else f"expires in {value.ttl} s"
        click.echo(f"{value.key} ({value.type}, length {value.length}, {ttl})")
        click.echo(json.dumps(value.value, indent=2, default=str))
        if value.truncated:
            click.echo("(only part of the value is shown)")
        return 0

    _done(ctx, run)


# ============================================================ console


@click.command("explain", cls=NoustCommand, read_only=True)
@click.argument("database")
@click.argument("query")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option(
    "--analyze",
    is_flag=True,
    help="Run the statement to time it, as the superuser, inside a transaction rolled back.",
)
@click.option("--timeout", type=TIMEOUT, default="30", show_default=True, help="Seconds.")
@json_option("Print the plan as JSON.")
@pass_context
def explain(
    ctx: Context, database: str, query: str, engine: str, analyze: bool, timeout: str
) -> None:
    """Show how the engine would run a statement."""

    def run() -> int:
        result = _console(ctx.logger).explain(
            engine, database, query, analyze=analyze, timeout_s=int(timeout)
        )
        if ctx.json_output:
            _json({"format": result.format, "plan": result.plan, "analyze": result.analyze})
        else:
            click.echo(result.text.rstrip())
        return 0

    _done(ctx, run)


@click.command("export", cls=NoustCommand, read_only=True)
@click.argument("database")
@click.argument("query")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option(
    "--format", "fmt", type=click.Choice(list(EXPORT_FORMATS)), default="csv", show_default=True
)
@click.option("--max-rows", type=int, default=EXPORT_ROW_CAP, show_default=True)
@click.option("--timeout", type=TIMEOUT, default="30", show_default=True, help="Seconds.")
@pass_context
def export(
    ctx: Context, database: str, query: str, engine: str, fmt: str, max_rows: int, timeout: str
) -> None:
    """Print a read's whole result as CSV or JSON, to redirect into a file."""

    def run() -> int:
        result = _console(ctx.logger).export(
            engine, database, query, fmt=fmt, max_rows=max_rows, timeout_s=int(timeout)
        )
        click.echo(result.content, nl=not result.content.endswith("\n"))
        if result.truncated:
            ctx.logger.warning(f"Only the first {result.row_count} rows were exported")
        return 0

    _done(ctx, run)


@click.command("history", cls=NoustCommand, read_only=True)
@click.option("--engine", "-e", type=ENGINE, help="Only this engine's.")
@click.option("--database", "-d", help="Only this database's.")
@click.option("--limit", type=click.IntRange(1, HISTORY_LIMIT), default=20, show_default=True)
@click.option("--clear", is_flag=True, help="Forget them instead of listing them.")
@json_option("Print the history as JSON.")
@pass_context
def history(
    ctx: Context, engine: str | None, database: str | None, limit: int, clear: bool
) -> None:
    """List the statements you ran in the console, newest first."""

    def run() -> int:
        records = _console(ctx.logger).records
        name = _service(ctx.logger).manager(engine).ENGINE_NAME if engine else None
        if clear:
            ctx.logger.success(f"Forgot {records.clear(engine=name, database=database)} statements")
            return 0
        entries = records.history(engine=name, database=database, limit=limit)
        if ctx.json_output:
            _json([entry.to_dict() for entry in entries])
            return 0
        for entry in entries:
            mark = "" if entry.outcome == "ok" else " [failed]"
            click.echo(f"{entry.created_at}  {entry.engine}/{entry.database}  {entry.mode}{mark}")
            click.echo(f"  {entry.statement}")
        return 0

    _done(ctx, run)


@click.group("saved", cls=NoustGroup)
def saved() -> None:
    """Keep statements under a name."""


@saved.command("list", read_only=True)
@click.option("--engine", "-e", type=ENGINE, help="Only this engine's.")
@click.option("--database", "-d", help="Only those for this database or for any.")
@json_option("Print them as JSON.")
@pass_context
def saved_list(ctx: Context, engine: str | None, database: str | None) -> None:
    """List your saved queries."""

    def run() -> int:
        name = _service(ctx.logger).manager(engine).ENGINE_NAME if engine else None
        queries = _console(ctx.logger).records.saved(engine=name, database=database)
        if ctx.json_output:
            _json([item.to_dict() for item in queries])
            return 0
        for item in queries:
            where = f"{item.engine}/{item.database or '*'}"
            click.echo(f"{item.id:>5}  {item.name}  ({where})")
            click.echo(f"       {item.statement}")
        return 0

    _done(ctx, run)


@saved.command("add")
@click.argument("name")
@click.argument("query")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine it is for.")
@click.option("--database", "-d", help="Database it is for; any when omitted.")
@pass_context
def saved_add(ctx: Context, name: str, query: str, engine: str, database: str | None) -> None:
    """Save a statement under a name."""

    def run() -> int:
        item = _console(ctx.logger).records.save(
            name=name,
            engine=_service(ctx.logger).manager(engine).ENGINE_NAME,
            database=database,
            statement=query,
        )
        ctx.logger.success(f"Saved query {item.id}: {item.name}")
        return 0

    _done(ctx, run)


@saved.command("remove")
@click.argument("saved_id", type=int)
@pass_context
def saved_remove(ctx: Context, saved_id: int) -> None:
    """Delete one of your saved queries."""

    def run() -> int:
        if not _console(ctx.logger).records.delete(saved_id):
            ctx.logger.error(f"No saved query {saved_id}")
            return 1
        ctx.logger.success(f"Deleted saved query {saved_id}")
        return 0

    _done(ctx, run)


# ============================================================ metrics


@click.command("metrics", cls=NoustCommand, read_only=True)
@click.argument("engine", type=ENGINE)
@click.argument("database", required=False)
@json_option("Print the metrics as JSON.")
@pass_context
def metrics(ctx: Context, engine: str, database: str | None) -> None:
    """Show an engine's, or one database's, size, connections and cache hits."""

    def run() -> int:
        reader = DatabaseMetricsReader(_service(ctx.logger))
        if database:
            data = reader.database_metrics(engine, database).to_dict()
        else:
            data = reader.engine_metrics(engine).to_dict()
        if ctx.json_output:
            _json(data)
            return 0
        for field_name in (
            "size_bytes",
            "connections",
            "server_connections",
            "max_connections",
            "cache_hit_ratio",
            "transactions",
            "keys",
        ):
            if data.get(field_name) is not None:
                click.echo(f"{field_name}: {data[field_name]}")
        for table in data.get("tables") or []:
            click.echo(f"  {table['schema']}.{table['name']}: {table['size_bytes']} bytes")
        for item in data.get("databases") or []:
            click.echo(f"  {json.dumps(item, default=str)}")
        return 0

    _done(ctx, run)


@click.command("slow-queries", cls=NoustCommand, read_only=True)
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option(
    "--limit", type=click.IntRange(1, 100), default=DEFAULT_SLOW_QUERIES, show_default=True
)
@json_option("Print them as JSON.")
@pass_context
def slow_queries(ctx: Context, database: str, engine: str, limit: int) -> None:
    """List the statements that take longest on average, or how to turn that on."""

    def run() -> int:
        answer = DatabaseMetricsReader(_service(ctx.logger)).slow_queries(
            engine, database, limit=limit
        )
        if ctx.json_output:
            _json(answer.to_dict())
            return 0
        if not answer.available:
            ctx.logger.warning("Statement statistics are not available")
            click.echo(answer.how_to_enable or "")
            return 0
        for item in answer.queries:
            click.echo(f"{item['mean_ms']:.2f} ms x {item['calls']}: {item['query']}")
        return 0

    _done(ctx, run)


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    for command in (
        tables,
        describe,
        rows,
        row,
        keys,
        key,
        explain,
        export,
        history,
        saved,
        metrics,
        slow_queries,
    ):
        group.add_command(command)
