# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The data explorer, the row editor and the Redis key browser, over HTTP.

Every endpoint is a client of
:class:`~noust.managers.database.browse.DataBrowser` or
:class:`~noust.managers.database.keys.KeyBrowser`, the same classes
``noust db`` uses. Reads need ``databases.read`` and run as the database's
read-only account; they are GETs, so a central's proxy forwards them like any
other read. Row edits need ``databases.write`` and sudo mode, change one row
found by its whole primary key, and are audited with the row before and
after.

A relation is named by ``schema`` and ``relation`` query parameters (or body
fields), not path segments: a table may be called anything, a ``/``
included, and a path cannot carry that.

Rows come back typed, cell by cell, in the order of ``columns``: ``numeric``
and ``boolean`` cells are JSON numbers and booleans (an integer beyond 2^53
or a decimal a float would change is a string); ``binary`` cells are
``{"bytes": n, "hex": "<first 32 bytes>"}``; every other kind is a string,
cut at 2000 characters with the cell's index listed in the row's
``truncated``. A NULL is ``null``, never ``""``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.managers.database.browse import DataBrowser, RowChange
from noust.managers.database.dialects import (
    DEFAULT_PAGE_SIZE,
    MAX_OFFSET,
    MAX_PAGE_SIZE,
    Filter,
    Order,
    Relation,
    RelationDetail,
)
from noust.managers.database.keys import DEFAULT_SCAN_COUNT, MAX_SCAN_COUNT, KeyBrowser
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import service
from noust.web.api.databases.query import TimeoutSeconds
from noust.web.api.deps import NoustErrorRoute, require_elevated

router = APIRouter(route_class=NoustErrorRoute)


# ============================================================ models


class RelationResponse(BaseModel):
    """
    One table or view.

    Attributes:
        kind: ``table``, ``view``, ``materialized_view`` or ``foreign_table``.
        rows_estimate: The planner's estimate, never a count.
        size_bytes: Data and indexes on disk; null for a view.
    """

    schema_: str = Field(..., alias="schema")
    name: str
    kind: str
    rows_estimate: int | None = None
    size_bytes: int | None = None


class SchemaResponse(BaseModel):
    """
    One schema.

    Attributes:
        relations: How many tables and views it holds.
    """

    name: str
    relations: int = 0


class SchemasResponse(BaseModel):
    """A database's schemas (on MySQL/MariaDB, the database itself)."""

    schemas: list[SchemaResponse]


class RelationListResponse(BaseModel):
    """A database's tables and views, by schema then name."""

    relations: list[RelationResponse]


class ColumnResponse(BaseModel):
    """
    One column.

    Attributes:
        type: The type as the engine spells it.
        kind: ``numeric``, ``boolean``, ``text``, ``datetime``, ``json``,
            ``binary``, ``uuid``, ``array`` or ``other``: how its cells are
            encoded and drawn.
        primary_key: Its position in the primary key, from 1.
        generated: Computed by the engine (identity, generated,
            auto-increment); an insert may leave it out.
    """

    name: str
    type: str
    kind: str
    nullable: bool = True
    default: str | None = None
    primary_key: int | None = None
    generated: bool = False


class IndexResponse(BaseModel):
    """One index."""

    name: str
    columns: list[str] = Field(default_factory=list)
    unique: bool = False
    primary: bool = False
    definition: str | None = None


class ConstraintResponse(BaseModel):
    """
    One constraint.

    Attributes:
        type: ``primary_key``, ``foreign_key``, ``unique``, ``check``,
            ``exclusion``, ``trigger`` or ``other``.
    """

    name: str
    type: str
    definition: str | None = None


class RelationDetailResponse(BaseModel):
    """
    A relation's structure.

    Attributes:
        editable: A table with a primary key: its rows can be edited.
    """

    schema_: str = Field(..., alias="schema")
    name: str
    kind: str
    columns: list[ColumnResponse]
    primary_key: list[str] = Field(default_factory=list)
    indexes: list[IndexResponse] = Field(default_factory=list)
    constraints: list[ConstraintResponse] = Field(default_factory=list)
    rows_estimate: int | None = None
    size_bytes: int | None = None
    editable: bool = False


class RowResponse(BaseModel):
    """
    One row.

    Attributes:
        cells: Its values, in the order of the page's ``columns``.
        truncated: Indexes of the cells cut to 2000 characters.
        key: Its primary key as text, by column: what an edit sends back.
            Empty when the relation has no primary key.
    """

    cells: list[Any]
    truncated: list[int] = Field(default_factory=list)
    key: dict[str, str] = Field(default_factory=dict)


class RowsResponse(BaseModel):
    """
    One page of rows.

    Attributes:
        pagination: ``keyset`` (sorted by the primary key: follow
            ``next_cursor``) or ``offset`` (follow ``next_offset``, up to
            100000).
        has_more: Whether another page follows.
        count: The exact number of matching rows, when asked with
            ``count=true``.
        rows_estimate: The planner's estimate of the relation's rows.
    """

    schema_: str = Field(..., alias="schema")
    relation: str
    columns: list[ColumnResponse]
    primary_key: list[str] = Field(default_factory=list)
    editable: bool = False
    rows: list[RowResponse]
    limit: int
    offset: int = 0
    pagination: str
    has_more: bool = False
    next_cursor: str | None = None
    next_offset: int | None = None
    count: int | None = None
    rows_estimate: int | None = None


class RowTarget(BaseModel):
    """The relation an edit is for."""

    schema_: str = Field(..., alias="schema", description="Schema (the database on MySQL)")
    relation: str = Field(..., description="Table")


class InsertRowRequest(RowTarget):
    """
    A row to insert.

    Attributes:
        values: Column to value; columns left out take their defaults. A
            binary value is ``{"hex": "..."}``; a JSON column takes an object,
            an array or the document's text.
    """

    values: dict[str, Any] = Field(default_factory=dict)


class UpdateRowRequest(RowTarget):
    """
    A row to change.

    Attributes:
        key: The row's whole primary key, as the page returned it.
        values: Column to new value.
    """

    key: dict[str, Any]
    values: dict[str, Any]


class DeleteRowRequest(RowTarget):
    """
    A row to delete.

    Attributes:
        key: The row's whole primary key, as the page returned it.
    """

    key: dict[str, Any]


class RowChangeResponse(BaseModel):
    """
    What an edit did.

    Attributes:
        action: ``insert``, ``update`` or ``delete``.
        key: The row's primary key (the new row's, for an insert).
        before: The row before, as the engine rendered it; null for an insert.
        after: The row after; null for a delete.
    """

    action: str
    engine: str
    database: str
    schema_: str = Field(..., alias="schema")
    relation: str
    key: dict[str, str]
    before: Any = None
    after: Any = None


class KeyResponse(BaseModel):
    """
    One Redis key.

    Attributes:
        key: The key as text (``\\xNN`` where it is not UTF-8).
        hex: Its exact bytes, when it is not UTF-8; pass it back as ``hex``.
        ttl: Seconds until it expires; null when it never does.
        memory: Bytes it takes, when the server says.
    """

    key: str
    hex: str | None = None
    type: str
    ttl: int | None = None
    memory: int | None = None


class KeysResponse(BaseModel):
    """
    One page of a key scan.

    Attributes:
        cursor: Where the next page starts; ``"0"`` when the scan is done.
        done: Whether the scan is complete.
        read_only_enforced: Whether the server held the reads read-only (an
            ACL user); false on a server without ACLs (Redis 5).
    """

    keys: list[KeyResponse]
    cursor: str
    done: bool
    read_only_enforced: bool


class KeyValueResponse(BaseModel):
    """
    A bounded preview of one key.

    Attributes:
        length: Bytes of a string, elements of anything else.
        value: A string's text (or ``{"bytes", "hex"}``); a list's or a set's
            elements; a hash's ``[field, value]`` pairs; a sorted set's
            ``[member, score]`` pairs; null for a stream or a module type.
        truncated: Only part of the value is shown.
    """

    key: str
    hex: str | None = None
    type: str
    ttl: int | None = None
    memory: int | None = None
    length: int | None = None
    value: Any = None
    truncated: bool = False
    read_only_enforced: bool = True


# ============================================================ helpers


def _relation(relation: Relation) -> RelationResponse:
    """
    Args:
        relation: A relation from the catalog.

    Returns:
        Its response.
    """
    return RelationResponse(**relation.to_dict())


def _detail(detail: RelationDetail) -> RelationDetailResponse:
    """
    Args:
        detail: A relation's structure.

    Returns:
        Its response.
    """
    return RelationDetailResponse(**detail.to_dict())


def _change(change: RowChange) -> RowChangeResponse:
    """
    Args:
        change: What an edit did.

    Returns:
        Its response.
    """
    return RowChangeResponse(**change.to_dict())


# ============================================================ reading


@router.get("/databases/{engine}/{name}/schemas", response_model=SchemasResponse)
def list_schemas(
    engine: str, name: str, session: Annotated[dict, Depends(get_current_session)]
) -> SchemasResponse:
    """
    List a database's schemas, as its read-only account sees them.

    Args:
        engine: The engine.
        name: The database.
        session: The authenticated session.

    Returns:
        The schemas, with how many relations each holds.
    """
    catalog = DataBrowser(service(session)).catalog(engine, name)
    counts: dict[str, int] = {}
    for relation in catalog.relations:
        counts[relation.schema] = counts.get(relation.schema, 0) + 1
    return SchemasResponse(
        schemas=[
            SchemaResponse(name=schema, relations=counts.get(schema, 0))
            for schema in catalog.schemas
        ]
    )


@router.get("/databases/{engine}/{name}/relations", response_model=RelationListResponse)
def list_relations(
    engine: str,
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    schema: Annotated[str | None, Query(description="Only this schema's")] = None,
    q: Annotated[str | None, Query(max_length=200, description="Name contains")] = None,
    kind: Annotated[str | None, Query(description="table, view, materialized_view...")] = None,
) -> RelationListResponse:
    """
    List a database's tables and views, with estimated rows and sizes.

    Args:
        engine: The engine.
        name: The database.
        session: The authenticated session.
        schema: Only this schema's.
        q: Only those whose name contains this, ignoring case.
        kind: Only this kind.

    Returns:
        The relations.
    """
    relations = DataBrowser(service(session)).relations(
        engine, name, schema=schema, search=q, kind=kind
    )
    return RelationListResponse(relations=[_relation(relation) for relation in relations])


@router.get("/databases/{engine}/{name}/relation", response_model=RelationDetailResponse)
def describe_relation(
    engine: str,
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    schema: Annotated[str, Query(description="Schema (the database on MySQL)")],
    relation: Annotated[str, Query(description="Table or view")],
) -> RelationDetailResponse:
    """
    Describe a relation: columns and types, primary key, indexes, constraints.

    Args:
        engine: The engine.
        name: The database.
        session: The authenticated session.
        schema: The schema.
        relation: The relation.

    Returns:
        Its structure.
    """
    return _detail(DataBrowser(service(session)).describe(engine, name, schema, relation))


@router.get("/databases/{engine}/{name}/rows", response_model=RowsResponse)
def read_rows(
    engine: str,
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    schema: Annotated[str, Query(description="Schema (the database on MySQL)")],
    relation: Annotated[str, Query(description="Table or view")],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
    cursor: Annotated[str | None, Query(max_length=4096, description="next_cursor")] = None,
    order: Annotated[
        list[str] | None, Query(description="column or column:asc / column:desc, repeatable")
    ] = None,
    filter: Annotated[
        list[str] | None,
        Query(description="column:op:value (eq neq lt lte gt gte like ilike in null notnull)"),
    ] = None,
    count: Annotated[bool, Query(description="Also count matching rows exactly")] = False,
    timeout_s: Annotated[TimeoutSeconds, Query(description="Statement timeout")] = 30,
) -> RowsResponse:
    """
    Read one page of a relation's rows, typed, as the read-only account.

    Args:
        engine: The engine.
        name: The database.
        session: The authenticated session.
        schema: The schema.
        relation: The relation.
        limit: Rows per page.
        offset: Rows to skip (offset pagination).
        cursor: A previous page's ``next_cursor`` (keyset pagination).
        order: Sort columns; the primary key when none.
        filter: Conditions, all of which a row must meet.
        count: Also count every matching row (bounded by the timeout).
        timeout_s: Seconds the server may spend.

    Returns:
        The page.
    """
    browser = DataBrowser(service(session))
    page = browser.rows(
        engine,
        name,
        schema,
        relation,
        filters=[Filter.parse(text) for text in filter or []],
        order=[Order.parse(text) for text in order or []],
        limit=limit,
        offset=offset,
        cursor=cursor,
        count=count,
        timeout_s=timeout_s,
    )
    detail = browser.describe(engine, name, schema, relation)
    return RowsResponse(
        schema=detail.schema,
        relation=detail.name,
        columns=[ColumnResponse(**column.to_dict()) for column in page.columns],
        primary_key=list(detail.primary_key),
        editable=detail.editable,
        rows=[
            RowResponse(cells=cells, truncated=truncated, key=key)
            for cells, truncated, key in zip(page.rows, page.truncated, page.keys, strict=False)
        ],
        limit=page.limit,
        offset=page.offset,
        pagination=page.pagination,
        has_more=page.has_more,
        next_cursor=page.next_cursor,
        next_offset=page.next_offset,
        count=page.count,
        rows_estimate=detail.rows_estimate,
    )


# ============================================================ editing


@router.post("/databases/{engine}/{name}/rows", response_model=RowChangeResponse)
def insert_row(
    engine: str,
    name: str,
    request: InsertRowRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> RowChangeResponse:
    """
    Insert one row into a table that has a primary key.

    Args:
        engine: The engine.
        name: The database.
        request: The table and the values.
        session: The elevated session.

    Returns:
        What changed.
    """
    return _change(
        DataBrowser(service(session)).insert_row(
            engine, name, request.schema_, request.relation, request.values
        )
    )


@router.patch("/databases/{engine}/{name}/rows", response_model=RowChangeResponse)
def update_row(
    engine: str,
    name: str,
    request: UpdateRowRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> RowChangeResponse:
    """
    Change one row, found by its whole primary key.

    Args:
        engine: The engine.
        name: The database.
        request: The table, the row's key and the new values.
        session: The elevated session.

    Returns:
        What changed: the row before and after.

    Raises:
        DatabaseNotFoundError: 404 when no row has that key; nothing changes.
    """
    return _change(
        DataBrowser(service(session)).update_row(
            engine, name, request.schema_, request.relation, request.key, request.values
        )
    )


@router.post("/databases/{engine}/{name}/rows/delete", response_model=RowChangeResponse)
def delete_row(
    engine: str,
    name: str,
    request: DeleteRowRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> RowChangeResponse:
    """
    Delete one row, found by its whole primary key.

    Args:
        engine: The engine.
        name: The database.
        request: The table and the row's key.
        session: The elevated session.

    Returns:
        What changed: the row as it was.

    Raises:
        DatabaseNotFoundError: 404 when no row has that key; nothing changes.
    """
    return _change(
        DataBrowser(service(session)).delete_row(
            engine, name, request.schema_, request.relation, request.key
        )
    )


# ============================================================ Redis keys


@router.get("/databases/{engine}/{name}/keys", response_model=KeysResponse)
def scan_keys(
    engine: str,
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    match: Annotated[str | None, Query(max_length=1024, description="Glob pattern")] = None,
    cursor: Annotated[str, Query(max_length=32, description="Scan cursor; 0 to start")] = "0",
    count: Annotated[int, Query(ge=1, le=MAX_SCAN_COUNT)] = DEFAULT_SCAN_COUNT,
    type: Annotated[str | None, Query(description="string, list, set, zset, hash, stream")] = None,
) -> KeysResponse:
    """
    Scan one page of a Redis slot's keys, with each key's type, TTL and memory.

    Args:
        engine: The engine (Redis or Valkey).
        name: The slot number.
        session: The authenticated session.
        match: Only keys matching this glob.
        cursor: Where to continue.
        count: Keys to scan.
        type: Only keys of this type.

    Returns:
        The page.
    """
    page = KeyBrowser(service(session)).scan(
        engine, name, match=match, cursor=cursor, count=count, key_type=type
    )
    return KeysResponse(
        keys=[KeyResponse(**key.to_dict()) for key in page.keys],
        cursor=page.cursor,
        done=page.done,
        read_only_enforced=page.read_only_enforced,
    )


@router.get("/databases/{engine}/{name}/key", response_model=KeyValueResponse)
def preview_key(
    engine: str,
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    key: Annotated[str | None, Query(max_length=65536, description="The key")] = None,
    hex: Annotated[str | None, Query(max_length=131072, description="The key's bytes")] = None,
) -> KeyValueResponse:
    """
    Read a bounded preview of one key.

    Args:
        engine: The engine (Redis or Valkey).
        name: The slot number.
        session: The authenticated session.
        key: The key, as text.
        hex: The key's exact bytes, for a key that is not UTF-8.

    Returns:
        The preview.
    """
    value = KeyBrowser(service(session)).preview(engine, name, key, key_hex=hex)
    return KeyValueResponse(**value.to_dict())
