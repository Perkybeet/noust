# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The data explorer and the row editor: the one implementation behind the CLI and the API.

Reading is **read-only by the server's own rules**: every catalog, structure
and page query signs in as the database's least-privilege account
(``wasm_ro_<database>`` on PostgreSQL and MySQL/MariaDB, never
``pg_read_all_data``) inside a read-only transaction. Nothing here decides
what a statement may do by looking at it.

Names from a request are never trusted: a schema, a table or a column is
looked up in what that read-only session itself listed (the catalog, the
structure) and only then quoted into a statement (see
:mod:`noust.managers.database.dialects`). The catalog and each structure are
kept for :data:`CATALOG_SECONDS`, so a page of rows is one process: the
engine is asked again when the time is up, when a name is not found, and
before any edit.

Editing changes **one row, identified by its whole primary key**, in a
transaction that rolls back unless exactly one row matched. A table without
a primary key is read-only here. Every edit is audited with the row before
and after; values under secret-looking column names (``password_hash``,
``api_token``) are redacted by the audit trail's own rules.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import (
    DatabaseEngineError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    ValidationError,
)
from noust.managers.database.base import BaseDatabaseManager
from noust.managers.database.dialects import (
    EDIT_GUARD,
    MYSQL_EDIT_GUARD,
    Catalog,
    Dialect,
    Filter,
    Order,
    PageQuery,
    Relation,
    RelationDetail,
    RowsPage,
    decode_cursor,
    dialect_for,
)
from noust.managers.database.service import DatabaseService

#: Seconds a catalog or a structure is reused before the engine is asked again.
CATALOG_SECONDS = 30.0

#: Seconds the server may spend on one explorer read, by default.
READ_TIMEOUT = 30

#: Seconds the server may spend on one row edit.
EDIT_TIMEOUT = 30

#: Kinds of relation a request may ask the list for.
RELATION_KINDS = ("table", "view", "materialized_view", "foreign_table")

_cache_lock = threading.Lock()
_catalogs: dict[tuple[str, str], tuple[float, Catalog]] = {}
_structures: dict[tuple[str, str, str, str], tuple[float, RelationDetail]] = {}


def forget_cached(engine: str | None = None, database: str | None = None) -> None:
    """
    Drop cached catalogs and structures.

    Args:
        engine: Only this engine's; every engine's when None.
        database: Only this database's (with ``engine``).
    """

    def matches(key: tuple[str, ...]) -> bool:
        return (engine is None or key[0] == engine) and (database is None or key[1] == database)

    with _cache_lock:
        for catalog_key in [key for key in _catalogs if matches(key)]:
            del _catalogs[catalog_key]
        for structure_key in [key for key in _structures if matches(key)]:
            del _structures[structure_key]


@dataclass
class RowChange:
    """
    What a row edit did.

    Attributes:
        action: ``insert``, ``update`` or ``delete``.
        engine: The engine.
        database: The database.
        schema: The relation's schema.
        relation: The relation.
        key: The row's primary key, as text (the new row's, for an insert,
            when the engine could read it back).
        before: The row before, as the engine rendered it; None for an insert.
        after: The row after; None for a delete.
    """

    action: str
    engine: str
    database: str
    schema: str
    relation: str
    key: dict[str, str]
    before: Any = None
    after: Any = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The change as plain data.
        """
        return {
            "action": self.action,
            "engine": self.engine,
            "database": self.database,
            "schema": self.schema,
            "relation": self.relation,
            "key": dict(self.key),
            "before": self.before,
            "after": self.after,
        }


class DataBrowser:
    """
    Read a database's relations and rows, and edit one row at a time.

    Construct one per request or command, over the
    :class:`~noust.managers.database.service.DatabaseService` that names the
    actor and resolves engines.
    """

    def __init__(self, service: DatabaseService) -> None:
        """
        Args:
            service: The database service: engines, the actor, the audit trail.
        """
        self.service = service

    # ------------------------------------------------------------ plumbing

    def _open(self, engine: str, database: str) -> tuple[BaseDatabaseManager, Dialect, str]:
        """
        Resolve an engine that has tables, and check the database's name.

        Args:
            engine: The engine, as the caller named it.
            database: The database.

        Returns:
            The manager, its dialect and the validated database name.

        Raises:
            DatabaseEngineError: When the engine is unknown.
            DatabaseQueryError: When the engine has no tables (Redis, MongoDB).
        """
        manager = self.service.manager(engine)
        dialect = dialect_for(
            manager.ENGINE_NAME, mariadb=bool(getattr(manager, "is_mariadb", False))
        )
        if dialect is None or "tables" not in manager.CAPABILITIES:
            raise DatabaseQueryError(
                f"{manager.DISPLAY_NAME} has no tables to browse",
                details=(
                    "The data explorer works on PostgreSQL and MySQL/MariaDB; Redis has "
                    "its key browser."
                ),
            )
        return manager, dialect, manager.validate_database_name(database)

    @staticmethod
    def _run(
        manager: BaseDatabaseManager,
        database: str,
        sql: str,
        *,
        read_only: bool,
        timeout_s: int,
    ) -> str:
        """
        Run a statement, saying so plainly when the engine is simply down.

        The engine's state is not checked before every read (one process
        per page is the goal); only a failure asks whether it is running.

        Args:
            manager: The engine's manager.
            database: The database.
            sql: The statement.
            read_only: As the read-only account.
            timeout_s: The statement timeout.

        Returns:
            The engine's output.

        Raises:
            DatabaseEngineError: When the engine is not running.
            DatabaseQueryError: When the statement failed.
        """
        try:
            return manager.run_sql(database, sql, read_only=read_only, timeout_s=timeout_s)
        except DatabaseQueryError as exc:
            if not manager.is_running():
                raise DatabaseEngineError(
                    f"{manager.DISPLAY_NAME} is not running",
                    details=f"Start it with: noust db start {manager.ENGINE_NAME}",
                ) from exc
            raise

    # ------------------------------------------------------------ reading

    def catalog(self, engine: str, database: str, *, refresh: bool = False) -> Catalog:
        """
        List a database's schemas and relations.

        Args:
            engine: The engine.
            database: The database.
            refresh: Ask the engine even when a recent answer is kept.

        Returns:
            The catalog, as the read-only session sees it.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the engine refuses.
        """
        manager, dialect, database = self._open(engine, database)
        key = (manager.ENGINE_NAME, database)
        now = time.monotonic()
        with _cache_lock:
            cached = _catalogs.get(key)
        if cached and not refresh and now - cached[0] <= CATALOG_SECONDS:
            return cached[1]
        if not manager.database_exists(database):
            raise DatabaseNotFoundError(
                f"Database '{database}' does not exist",
                details=f"List them with: noust db list --engine {manager.ENGINE_NAME}",
            )
        catalog = dialect.parse_catalog(
            self._run(
                manager,
                database,
                dialect.catalog_sql(database),
                read_only=True,
                timeout_s=READ_TIMEOUT,
            )
        )
        with _cache_lock:
            _catalogs[key] = (now, catalog)
        return catalog

    def relations(
        self,
        engine: str,
        database: str,
        *,
        schema: str | None = None,
        search: str | None = None,
        kind: str | None = None,
    ) -> list[Relation]:
        """
        List a database's relations, narrowed.

        Args:
            engine: The engine.
            database: The database.
            schema: Only this schema's.
            search: Only those whose name contains this, ignoring case.
            kind: Only this kind (see :data:`RELATION_KINDS`).

        Returns:
            The relations.

        Raises:
            ValidationError: When the kind is unknown.
        """
        if kind is not None and kind not in RELATION_KINDS:
            raise ValidationError(
                f"Unknown relation kind: {kind!r}",
                details=f"Use one of: {', '.join(RELATION_KINDS)}.",
            )
        needle = (search or "").lower()
        return [
            relation
            for relation in self.catalog(engine, database).relations
            if (schema is None or relation.schema == schema)
            and (kind is None or relation.kind == kind)
            and needle in relation.name.lower()
        ]

    def _relation(self, engine: str, database: str, schema: str, name: str) -> Relation:
        """
        Find a relation in the catalog, asking the engine again once if needed.

        Args:
            engine: The engine.
            database: The database.
            schema: The schema, as a request named it.
            name: The relation, as a request named it.

        Returns:
            The relation.

        Raises:
            DatabaseNotFoundError: When the read-only session sees no such
                relation.
        """
        found = self.catalog(engine, database).find(schema, name)
        if found is None:
            found = self.catalog(engine, database, refresh=True).find(schema, name)
        if found is None:
            raise DatabaseNotFoundError(
                f"{schema}.{name} is not a table or view of '{database}'",
                details=(
                    "Names are matched exactly, case included. The explorer lists what the "
                    "database's read-only account can read."
                ),
            )
        return found

    def describe(
        self, engine: str, database: str, schema: str, name: str, *, refresh: bool = False
    ) -> RelationDetail:
        """
        Read a relation's structure: columns, keys, indexes, constraints.

        Args:
            engine: The engine.
            database: The database.
            schema: The schema.
            name: The relation.
            refresh: Ask the engine even when a recent answer is kept.

        Returns:
            The structure.

        Raises:
            DatabaseNotFoundError: When there is no such relation.
        """
        manager, dialect, database = self._open(engine, database)
        relation = self._relation(engine, database, schema, name)
        key = (manager.ENGINE_NAME, database, relation.schema, relation.name)
        now = time.monotonic()
        with _cache_lock:
            cached = _structures.get(key)
        if cached and not refresh and now - cached[0] <= CATALOG_SECONDS:
            return cached[1]
        detail = dialect.parse_describe(
            self._run(
                manager,
                database,
                dialect.describe_sql(relation.schema, relation.name),
                read_only=True,
                timeout_s=READ_TIMEOUT,
            ),
            relation,
        )
        with _cache_lock:
            _structures[key] = (now, detail)
        return detail

    def rows(
        self,
        engine: str,
        database: str,
        schema: str,
        name: str,
        *,
        filters: list[Filter] | None = None,
        order: list[Order] | None = None,
        limit: int = 100,
        offset: int = 0,
        cursor: str | None = None,
        count: bool = False,
        timeout_s: int = READ_TIMEOUT,
    ) -> RowsPage:
        """
        Read one page of a relation's rows.

        Sorted by the primary key (the default), pages follow a cursor
        (keyset pagination: each page is as cheap as the first); any other
        sort pages by a bounded offset. Filters and sorts are checked against
        the structure; values become literals.

        Args:
            engine: The engine.
            database: The database.
            schema: The schema.
            name: The relation.
            filters: Conditions every row must meet.
            order: Sort columns; the primary key when empty.
            limit: Rows per page.
            offset: Rows to skip, for offset pagination.
            cursor: Where a keyset page starts (a previous page's
                ``next_cursor``).
            count: Also count every matching row exactly (bounded by the
                timeout).
            timeout_s: Seconds the server may spend.

        Returns:
            The page.

        Raises:
            ValidationError: When a filter, a sort, the limit or the cursor
                is not acceptable.
            DatabaseNotFoundError: When there is no such relation.
            DatabaseQueryError: When the engine refuses.
        """
        manager, dialect, database = self._open(engine, database)
        detail = self.describe(engine, database, schema, name)
        after = decode_cursor(cursor, len(detail.primary_key)) if cursor else None
        page = dialect.check_page(
            detail,
            PageQuery(
                filters=tuple(filters or ()),
                order=tuple(order or ()),
                limit=limit,
                offset=offset,
                after=after,
                count=count,
            ),
        )
        result = dialect.parse_rows(
            self._run(
                manager,
                database,
                dialect.rows_sql(detail, page),
                read_only=True,
                timeout_s=timeout_s,
            ),
            detail,
            page,
        )
        self.service.audit(
            "db.browse",
            f"{manager.ENGINE_NAME}/{database}",
            relation=f"{detail.schema}.{detail.name}",
            rows=len(result.rows),
            filters=[f"{item.column}:{item.op}" for item in page.filters] or None,
        )
        return result

    # ------------------------------------------------------------ editing

    def insert_row(
        self, engine: str, database: str, schema: str, name: str, values: Mapping[str, Any]
    ) -> RowChange:
        """
        Insert one row.

        Args:
            engine: The engine.
            database: The database.
            schema: The schema.
            name: The table, which must have a primary key.
            values: Column to value; columns left out take their defaults.

        Returns:
            The change, with the new row when the engine could read it back.
        """
        return self._edit("insert", engine, database, schema, name, key=None, values=values)

    def update_row(
        self,
        engine: str,
        database: str,
        schema: str,
        name: str,
        key: Mapping[str, Any],
        values: Mapping[str, Any],
    ) -> RowChange:
        """
        Change one row, found by its whole primary key.

        Args:
            engine: The engine.
            database: The database.
            schema: The schema.
            name: The table.
            key: The row's primary key, as a page returned it.
            values: Column to new value.

        Returns:
            The change, with the row before and after.
        """
        return self._edit("update", engine, database, schema, name, key=key, values=values)

    def delete_row(
        self, engine: str, database: str, schema: str, name: str, key: Mapping[str, Any]
    ) -> RowChange:
        """
        Delete one row, found by its whole primary key.

        Args:
            engine: The engine.
            database: The database.
            schema: The schema.
            name: The table.
            key: The row's primary key, as a page returned it.

        Returns:
            The change, with the row as it was.
        """
        return self._edit("delete", engine, database, schema, name, key=key, values=None)

    def _edit(
        self,
        action: str,
        engine: str,
        database: str,
        schema: str,
        name: str,
        *,
        key: Mapping[str, Any] | None,
        values: Mapping[str, Any] | None,
    ) -> RowChange:
        """
        Make one guarded, audited row change.

        The structure is read afresh, not from the cache: an edit is checked
        against the table as it is now.

        Args:
            action: ``insert``, ``update`` or ``delete``.
            engine: The engine.
            database: The database.
            schema: The schema.
            name: The table.
            key: The row's primary key (update, delete).
            values: The values (insert, update).

        Returns:
            The change.

        Raises:
            ValidationError: When the table has no primary key, the key is
                not whole, or a value names a column the table lacks.
            DatabaseNotFoundError: When no row has that key.
            DatabaseQueryError: When the engine refuses the change (a
                constraint, a type), with its own message.
        """
        manager, dialect, database = self._open(engine, database)
        detail = self.describe(engine, database, schema, name, refresh=True)
        target = f"{manager.ENGINE_NAME}/{database}"
        relation = f"{detail.schema}.{detail.name}"
        if not detail.editable:
            dialect.check_key(detail, key or {})
        checked_key = dialect.check_key(detail, key) if key is not None else {}
        if action == "insert":
            checked = dialect.check_values(detail, values or {}, allow_empty=True)
            script = dialect.insert_sql(detail, checked)
        elif action == "update":
            checked = dialect.check_values(detail, values or {}, allow_empty=False)
            script = dialect.update_sql(detail, checked_key, checked)
        else:
            checked = {}
            script = dialect.delete_sql(detail, checked_key)

        event = f"db.row.{action}"
        try:
            output = self._run(manager, database, script, read_only=False, timeout_s=EDIT_TIMEOUT)
        except DatabaseQueryError as exc:
            said = f"{exc.output or ''}\n{exc.details}"
            if EDIT_GUARD in said or MYSQL_EDIT_GUARD in said:
                self.service.audit(
                    event,
                    target,
                    outcome="failure",
                    relation=relation,
                    key=checked_key,
                    reason="no such row",
                )
                raise DatabaseNotFoundError(
                    f"No row of {relation} has that primary key; nothing was changed",
                    details="It was deleted or its key changed since the page was read. Reload it.",
                ) from exc
            self.service.audit(
                event,
                target,
                outcome="failure",
                relation=relation,
                key=checked_key,
                error=exc.output or exc.message,
            )
            raise
        before, after = dialect.parse_change(output)
        new_key = checked_key
        if isinstance(after, dict) and all(column in after for column in detail.primary_key):
            new_key = {column: _text(after[column]) for column in detail.primary_key}
        change = RowChange(
            action=action,
            engine=manager.ENGINE_NAME,
            database=database,
            schema=detail.schema,
            relation=detail.name,
            key=new_key,
            before=before,
            after=after,
        )
        self.service.audit(
            event,
            target,
            relation=relation,
            key=new_key,
            columns=sorted(checked) or None,
            before=before,
            after=after,
        )
        return change


def _text(value: Any) -> str:
    """
    Args:
        value: A key value from a row image.

    Returns:
        It as the text a page would return.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
