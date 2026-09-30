# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The SQL the data explorer, the row editor and the metrics send, per engine.

Nothing here runs a process: a dialect builds statement text and reads the
engine's answer back. :mod:`noust.managers.database.browse` and
:mod:`noust.managers.database.metrics` hand the text to the engine's manager,
which runs it through the runner.

Three rules keep the text safe, and every builder follows them:

- **Identifiers come from the catalog, never from a request.** A schema,
  table or column name reaches a builder only after the caller found it in
  what the engine itself listed (:class:`RelationDetail`), and is then quoted
  with the engine's own identifier quoting.
- **Values are literals built here.** PostgreSQL gets ``quote_literal``'s
  rules (an ``E''`` string when a backslash is present, so the value survives
  ``standard_conforming_strings`` either way); MySQL gets a hexadecimal
  literal with a character set introducer, ``_utf8mb4 X'...'``, which no
  ``sql_mode`` can reinterpret. Nothing typed by an operator is concatenated
  into a statement any other way.
- **Operators are an enumeration** (:data:`FILTER_OPERATORS`), mapped to SQL
  here; the request only names one.

Results come back as JSON the server builds (``json_agg``/``to_json`` on
PostgreSQL, one ``JSON_ARRAY`` per line on MySQL and MariaDB), so a NULL, a
number and an empty string arrive as three different things. Binary values
arrive as their length and a hexadecimal prefix, and long values cut to
:data:`CELL_TEXT_LIMIT` characters with a flag that says so.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, NoReturn

from noust.core.exceptions import DatabaseNotFoundError, DatabaseQueryError, ValidationError
from noust.managers.database.base import quote_identifier

#: Rows a page may hold.
MAX_PAGE_SIZE = 1000

#: Rows a page holds when the caller does not say.
DEFAULT_PAGE_SIZE = 100

#: Furthest an offset may reach. Past it the engine reads and discards that
#: many rows for every page, which is what keyset pagination (sorting by the
#: primary key) or a filter is for.
MAX_OFFSET = 100_000

#: Characters of one value a page carries before it is cut and flagged.
CELL_TEXT_LIMIT = 2000

#: Bytes of a binary value shown, as hexadecimal.
BINARY_PREVIEW_BYTES = 32

#: Most values an ``in`` filter accepts.
MAX_IN_VALUES = 100

#: Most filters, and most sort columns, one page accepts.
MAX_FILTERS = 20
MAX_ORDER_COLUMNS = 3

#: Operators a filter may use.
FILTER_OPERATORS: tuple[str, ...] = (
    "eq",
    "neq",
    "lt",
    "lte",
    "gt",
    "gte",
    "like",
    "ilike",
    "null",
    "notnull",
    "in",
)

#: Operators that take no value.
_UNARY_OPERATORS = frozenset({"null", "notnull"})

#: The comparison each binary operator stands for.
_COMPARISONS: Mapping[str, str] = {
    "eq": "=",
    "neq": "<>",
    "lt": "<",
    "lte": "<=",
    "gt": ">",
    "gte": ">=",
}

#: What a column's values are, as the console draws them and as a page
#: encodes them: ``numeric`` and ``boolean`` values are JSON numbers and
#: booleans; ``binary`` values are ``{"bytes": n, "hex": "..."}``; every
#: other kind is a string (``json`` is the document's text).
ColumnKind = Literal[
    "numeric", "boolean", "text", "datetime", "json", "binary", "uuid", "array", "other"
]

#: Kinds whose values can be long enough to cut.
_TRUNCATABLE: frozenset[str] = frozenset({"text", "json", "array", "other"})

#: PostgreSQL's type categories (``pg_type.typcategory``), as kinds.
_PG_CATEGORIES: Mapping[str, ColumnKind] = {
    "N": "numeric",
    "B": "boolean",
    "D": "datetime",
    "S": "text",
    "A": "array",
}

#: What a relation is.
RelationKind = Literal["table", "view", "materialized_view", "foreign_table"]

#: Integers beyond this lose precision in a browser's JavaScript, so they
#: travel as strings.
_SAFE_INTEGER = 2**53 - 1


# ============================================================ model


@dataclass(frozen=True)
class Relation:
    """
    One table or view, as the catalog lists it.

    Attributes:
        schema: Its schema (the database itself on MySQL and MariaDB).
        name: Its name.
        kind: ``table``, ``view``, ``materialized_view`` or ``foreign_table``.
        rows_estimate: The planner's estimate, never a ``COUNT(*)``; None
            when the engine has none yet.
        size_bytes: Data and indexes on disk; None for a view.
    """

    schema: str
    name: str
    kind: str
    rows_estimate: int | None = None
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The relation as plain data.
        """
        return asdict(self)


@dataclass(frozen=True)
class Catalog:
    """
    Every schema and relation a database's read-only session can see.

    Attributes:
        schemas: Schema names, sorted.
        relations: Relations, by schema then name.
    """

    schemas: tuple[str, ...]
    relations: tuple[Relation, ...]

    def find(self, schema: str, name: str) -> Relation | None:
        """
        Look a relation up by its exact names.

        Args:
            schema: The schema.
            name: The relation.

        Returns:
            The relation, or None when the catalog has no such one.
        """
        for relation in self.relations:
            if relation.schema == schema and relation.name == name:
                return relation
        return None


@dataclass(frozen=True)
class Column:
    """
    One column of a relation.

    Attributes:
        name: The column's name.
        type: Its type as the engine spells it (``character varying(255)``,
            ``int(11) unsigned``).
        kind: What its values are; see :data:`ColumnKind`.
        nullable: Whether it accepts NULL.
        default: Its default expression, verbatim.
        primary_key: Its position in the primary key, from 1; None when it
            is not part of it.
        generated: Whether the engine computes it (identity, generated or
            auto-increment), so an insert may leave it out.
    """

    name: str
    type: str
    kind: str
    nullable: bool = True
    default: str | None = None
    primary_key: int | None = None
    generated: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The column as plain data.
        """
        return asdict(self)


@dataclass(frozen=True)
class Index:
    """
    One index.

    Attributes:
        name: Its name.
        columns: The columns it covers, in order (empty for an expression
            index, whose ``definition`` says what it covers).
        unique: Whether it enforces uniqueness.
        primary: Whether it is the primary key's.
        definition: The engine's own definition, when it prints one.
    """

    name: str
    columns: tuple[str, ...] = ()
    unique: bool = False
    primary: bool = False
    definition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The index as plain data.
        """
        data = asdict(self)
        data["columns"] = list(self.columns)
        return data


@dataclass(frozen=True)
class Constraint:
    """
    One constraint.

    Attributes:
        name: Its name.
        type: ``primary_key``, ``foreign_key``, ``unique``, ``check`` or
            ``exclusion``.
        definition: The engine's own definition, verbatim.
    """

    name: str
    type: str
    definition: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The constraint as plain data.
        """
        return asdict(self)


@dataclass(frozen=True)
class RelationDetail:
    """
    A relation's structure: what the explorer shows and what every page and
    every edit is checked against.

    Attributes:
        schema: Its schema.
        name: Its name.
        kind: What it is; see :class:`Relation`.
        columns: Its columns, in order.
        primary_key: The primary key's columns, in key order; empty when it
            has none.
        indexes: Its indexes.
        constraints: Its constraints.
        rows_estimate: The planner's estimate.
        size_bytes: Data and indexes on disk.
    """

    schema: str
    name: str
    kind: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    indexes: tuple[Index, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    rows_estimate: int | None = None
    size_bytes: int | None = None

    @property
    def editable(self) -> bool:
        """Whether a row can be told apart from every other: a table with a primary key."""
        return self.kind == "table" and bool(self.primary_key)

    def column(self, name: str) -> Column:
        """
        Find a column this relation really has.

        Args:
            name: The name a request gave.

        Returns:
            The column.

        Raises:
            ValidationError: When the relation has no column of that name.
        """
        for column in self.columns:
            if column.name == name:
                return column
        raise ValidationError(
            f"{self.schema}.{self.name} has no column {name!r}",
            details="Column names are matched exactly, case included; the structure tab lists them.",
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The structure as plain data.
        """
        return {
            "schema": self.schema,
            "name": self.name,
            "kind": self.kind,
            "columns": [column.to_dict() for column in self.columns],
            "primary_key": list(self.primary_key),
            "indexes": [index.to_dict() for index in self.indexes],
            "constraints": [constraint.to_dict() for constraint in self.constraints],
            "rows_estimate": self.rows_estimate,
            "size_bytes": self.size_bytes,
            "editable": self.editable,
        }


@dataclass(frozen=True)
class Filter:
    """
    One condition on a page, as a request states it.

    Attributes:
        column: The column, by name.
        op: One of :data:`FILTER_OPERATORS`.
        value: The value, for a binary operator.
        values: The values, for ``in``.
    """

    column: str
    op: str
    value: str | None = None
    values: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> Filter:
        """
        Read a filter written ``column:op:value`` (``column:null`` for the
        unary operators, ``column:in:a,b,c`` for a list).

        A column whose name holds a ``:`` cannot be filtered this way; the
        catalog check refuses what the split produces.

        Args:
            text: The filter as a query parameter carries it.

        Returns:
            The filter.

        Raises:
            ValidationError: When the text has no operator, an unknown one,
                or lacks the value its operator needs.
        """
        parts = text.split(":", 2)
        if len(parts) < 2 or not parts[0]:
            raise ValidationError(
                f"Invalid filter: {text!r}",
                details="Write filters as column:operator:value, such as status:eq:paid.",
            )
        column, op = parts[0], parts[1].lower()
        value = parts[2] if len(parts) == 3 else None
        if op == "in" and value is not None:
            return cls.build(column, op, values=tuple(value.split(",")))
        return cls.build(column, op, value=value)

    @classmethod
    def build(
        cls, column: str, op: str, *, value: str | None = None, values: Sequence[str] = ()
    ) -> Filter:
        """
        Check a filter's operator and value.

        Args:
            column: The column, by name.
            op: The operator.
            value: The value of a binary operator.
            values: The values of ``in``.

        Returns:
            The filter.

        Raises:
            ValidationError: When the operator is unknown or the value does
                not fit it.
        """
        if op not in FILTER_OPERATORS:
            raise ValidationError(
                f"Unknown filter operator: {op!r}",
                details=f"Use one of: {', '.join(FILTER_OPERATORS)}.",
            )
        if op in _UNARY_OPERATORS:
            return cls(column=column, op=op)
        if op == "in":
            if not values or len(values) > MAX_IN_VALUES:
                raise ValidationError(
                    f"An 'in' filter takes from 1 to {MAX_IN_VALUES} values",
                    details="Separate the values with commas: status:in:paid,sent.",
                )
            for item in values:
                _check_text(item)
            return cls(column=column, op=op, values=tuple(values))
        if value is None:
            raise ValidationError(
                f"The {op!r} filter on {column!r} needs a value",
                details=f"Write it as {column}:{op}:value.",
            )
        _check_text(value)
        return cls(column=column, op=op, value=value)


@dataclass(frozen=True)
class Order:
    """
    One sort column.

    Attributes:
        column: The column, by name.
        descending: Largest first.
    """

    column: str
    descending: bool = False

    @classmethod
    def parse(cls, text: str) -> Order:
        """
        Read a sort written ``column`` or ``column:asc`` / ``column:desc``.

        Args:
            text: The sort as a query parameter carries it.

        Returns:
            The sort.

        Raises:
            ValidationError: When the direction is neither ``asc`` nor ``desc``.
        """
        column, _, direction = text.rpartition(":")
        if not column:
            return cls(column=text)
        if direction.lower() not in ("asc", "desc"):
            # The ':' belonged to the column's name.
            return cls(column=text)
        return cls(column=column, descending=direction.lower() == "desc")


@dataclass
class RowsPage:
    """
    One page of a relation's rows.

    Attributes:
        columns: The relation's columns, in the order of every row's cells.
        rows: Each row's cells, as :data:`ColumnKind` describes them.
        truncated: For each row, the indexes of the cells cut to
            :data:`CELL_TEXT_LIMIT` characters.
        keys: For each row, its primary key as text, by column; empty when
            the relation has none. What a row edit identifies the row by.
        limit: Rows asked for.
        offset: Rows skipped (offset pagination).
        pagination: ``keyset`` (sorted by the primary key, cursor-based) or
            ``offset``.
        has_more: Whether another page follows.
        next_cursor: Where the next keyset page starts.
        next_offset: Where the next offset page starts.
        count: The exact number of matching rows, when asked for.
    """

    columns: list[Column]
    rows: list[list[Any]] = field(default_factory=list)
    truncated: list[list[int]] = field(default_factory=list)
    keys: list[dict[str, str]] = field(default_factory=list)
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0
    pagination: str = "offset"
    has_more: bool = False
    next_cursor: str | None = None
    next_offset: int | None = None
    count: int | None = None


@dataclass(frozen=True)
class PageQuery:
    """
    Everything a page asks for, checked against the relation.

    Attributes:
        filters: Conditions, all of which a row must meet.
        order: Sort columns; the primary key when empty.
        limit: Rows to return.
        offset: Rows to skip (offset pagination only).
        after: Primary key values the page starts after (keyset only).
        count: Also count every matching row exactly.
    """

    filters: tuple[Filter, ...] = ()
    order: tuple[Order, ...] = ()
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0
    after: tuple[str, ...] | None = None
    count: bool = False


# ============================================================ helpers


def _check_text(value: str) -> None:
    """
    Refuse a value no statement can carry.

    Args:
        value: A value from a request.

    Raises:
        ValidationError: When it holds a NUL byte.
    """
    if "\x00" in value:
        raise ValidationError(
            "A value contains a NUL byte",
            details="Remove the NUL character; no database text value holds one.",
        )


def encode_cursor(values: Sequence[str]) -> str:
    """
    Turn a row's primary key into the opaque cursor the next page starts after.

    Args:
        values: The key's values as text, in key order.

    Returns:
        A URL-safe token.
    """
    raw = json.dumps(list(values), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(token: str, width: int) -> tuple[str, ...]:
    """
    Read a cursor :func:`encode_cursor` made.

    Args:
        token: The cursor.
        width: How many columns the primary key has.

    Returns:
        The key's values, in key order.

    Raises:
        ValidationError: When the token is not a cursor for this key.
    """
    try:
        padded = token + "=" * (-len(token) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationError(
            "Invalid page cursor", details="Start again from the first page."
        ) from exc
    if (
        not isinstance(values, list)
        or len(values) != width
        or not all(isinstance(value, str) for value in values)
    ):
        raise ValidationError("Invalid page cursor", details="Start again from the first page.")
    for value in values:
        _check_text(value)
    return tuple(values)


def _number(text: str) -> float | str:
    """
    Keep a JSON number exact: a float when it round-trips, its text otherwise.

    Args:
        text: The number as the engine printed it.

    Returns:
        The float, or the text when a float would change it.
    """
    try:
        value = float(text)
        exact = math.isfinite(value) and Decimal(text) == Decimal(repr(value))
    except (ValueError, InvalidOperation):
        return text
    return value if exact else text


def _integer(text: str) -> int | str:
    """
    Keep a JSON integer exact in a browser: an int when JavaScript can hold it.

    Args:
        text: The integer as the engine printed it.

    Returns:
        The int, or its text when it exceeds 2**53 - 1.
    """
    value = int(text)
    return value if abs(value) <= _SAFE_INTEGER else text


def load_json(text: str, *, what: str) -> Any:
    """
    Parse the engine's JSON answer, keeping large and precise numbers exact.

    Args:
        text: The engine's output.
        what: What was asked, for the error.

    Returns:
        The parsed value.

    Raises:
        DatabaseQueryError: When the output is not JSON, with the output.
    """
    try:
        return json.loads(text, parse_float=_number, parse_int=_integer)
    except ValueError as exc:
        raise DatabaseQueryError(
            f"The engine's answer to {what} could not be read",
            details="Noust expected JSON from its own query; the engine's output follows.",
            output=text.strip()[:2000],
        ) from exc


def _as_int(value: Any) -> int | None:
    """
    Args:
        value: A number the engine returned, maybe as text.

    Returns:
        It as an int, or None.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _page_limit(limit: int) -> int:
    """
    Args:
        limit: Rows asked for.

    Returns:
        The limit.

    Raises:
        ValidationError: When it is outside 1..:data:`MAX_PAGE_SIZE`.
    """
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValidationError(
            f"A page holds from 1 to {MAX_PAGE_SIZE} rows, not {limit}",
            details="Ask for a smaller page.",
        )
    return limit


# ============================================================ dialects


class Dialect(ABC):
    """
    One engine's SQL for the explorer, the editor and the metrics.

    Subclasses quote, classify types, and build and read the statements;
    the checks that do not depend on the engine (columns from the catalog,
    operators from the enumeration, page bounds) are made once, here.
    """

    #: Engine name, as the registry knows it.
    ENGINE: str = ""

    # ------------------------------------------------------------ quoting

    @abstractmethod
    def ident(self, name: str) -> str:
        """
        Quote an identifier.

        Args:
            name: A name from the catalog.

        Returns:
            The quoted identifier.
        """

    @abstractmethod
    def literal(self, value: str) -> str:
        """
        Quote a text value as a literal.

        Args:
            value: The value.

        Returns:
            A literal the engine reads back as exactly ``value``.
        """

    def qualified(self, schema: str, name: str) -> str:
        """
        Args:
            schema: The schema.
            name: The relation.

        Returns:
            ``schema.name``, both quoted.
        """
        return f"{self.ident(schema)}.{self.ident(name)}"

    # ------------------------------------------------------------ catalog

    @abstractmethod
    def catalog_sql(self, database: str) -> str:
        """
        Args:
            database: The database.

        Returns:
            The statement listing schemas and relations.
        """

    @abstractmethod
    def parse_catalog(self, output: str) -> Catalog:
        """
        Args:
            output: The engine's answer to :meth:`catalog_sql`.

        Returns:
            The catalog.
        """

    @abstractmethod
    def describe_sql(self, schema: str, name: str) -> str:
        """
        Args:
            schema: A schema from the catalog.
            name: A relation from the catalog.

        Returns:
            The statement describing the relation.
        """

    @abstractmethod
    def parse_describe(self, output: str, relation: Relation) -> RelationDetail:
        """
        Args:
            output: The engine's answer to :meth:`describe_sql`.
            relation: The relation, as the catalog listed it.

        Returns:
            Its structure.
        """

    # ------------------------------------------------------------ pages

    def check_page(self, detail: RelationDetail, page: PageQuery) -> PageQuery:
        """
        Hold a page request to what the relation really has.

        Args:
            detail: The relation's structure, from the catalog.
            page: What was asked.

        Returns:
            The request, unchanged.

        Raises:
            ValidationError: When a filter or a sort names a column the
                relation lacks, the page is out of bounds, or a cursor is
                used without keyset pagination.
        """
        _page_limit(page.limit)
        if len(page.filters) > MAX_FILTERS or len(page.order) > MAX_ORDER_COLUMNS:
            raise ValidationError(
                f"A page takes at most {MAX_FILTERS} filters and {MAX_ORDER_COLUMNS} sort columns",
                details="Narrow the request.",
            )
        for item in page.filters:
            detail.column(item.column)
        for sort in page.order:
            detail.column(sort.column)
        if page.offset < 0 or page.offset > MAX_OFFSET:
            raise ValidationError(
                f"Offsets go from 0 to {MAX_OFFSET}",
                details=(
                    "Sort by the primary key to page with a cursor, or add a filter to "
                    "reach rows further in."
                ),
            )
        if page.after is not None and not self.keyset(detail, page):
            raise ValidationError(
                "A page cursor only works when the rows are sorted by the primary key",
                details="Drop the cursor, or sort by the primary key alone.",
            )
        return page

    @staticmethod
    def keyset(detail: RelationDetail, page: PageQuery) -> bool:
        """
        Say whether a page can use keyset pagination.

        It can when the relation has a primary key and the page is sorted by
        exactly that key, one direction for every column (the default sort).

        Args:
            detail: The relation's structure.
            page: The request.

        Returns:
            True for keyset pagination.
        """
        if not detail.primary_key:
            return False
        if not page.order:
            return True
        directions = {sort.descending for sort in page.order}
        return (
            tuple(sort.column for sort in page.order) == detail.primary_key and len(directions) == 1
        )

    def effective_order(self, detail: RelationDetail, page: PageQuery) -> tuple[Order, ...]:
        """
        The sort a page really uses: the one asked, completed with the
        primary key so equal values keep a stable order across pages.

        Args:
            detail: The relation's structure.
            page: The request.

        Returns:
            The sort columns.
        """
        order = list(page.order)
        if not order:
            return tuple(Order(column) for column in detail.primary_key)
        named = {sort.column for sort in order}
        order.extend(Order(column) for column in detail.primary_key if column not in named)
        return tuple(order)

    def where(self, detail: RelationDetail, filters: Sequence[Filter]) -> list[str]:
        """
        Build the conditions of a page.

        Args:
            detail: The relation's structure.
            filters: The filters, already checked.

        Returns:
            One SQL condition per filter.
        """
        return [self.condition(detail.column(item.column), item) for item in filters]

    def condition(self, column: Column, item: Filter) -> str:
        """
        Build one condition.

        Args:
            column: The column, from the catalog.
            item: The filter.

        Returns:
            The SQL condition.
        """
        ident = self.ident(column.name)
        if item.op == "null":
            return f"{ident} IS NULL"
        if item.op == "notnull":
            return f"{ident} IS NOT NULL"
        if item.op == "in":
            return f"{ident} IN ({', '.join(self.literal(value) for value in item.values)})"
        value = self.literal(item.value or "")
        if item.op == "like":
            return self.like(ident, value, insensitive=False)
        if item.op == "ilike":
            return self.like(ident, value, insensitive=True)
        return f"{ident} {_COMPARISONS[item.op]} {value}"

    @abstractmethod
    def like(self, ident: str, literal: str, *, insensitive: bool) -> str:
        """
        Args:
            ident: A quoted column.
            literal: A quoted pattern.
            insensitive: Ignore case.

        Returns:
            The pattern match, on the column's text.
        """

    @abstractmethod
    def rows_sql(self, detail: RelationDetail, page: PageQuery) -> str:
        """
        Args:
            detail: The relation's structure.
            page: The request, already checked.

        Returns:
            The statement reading one page (one more row than asked, to know
            whether another follows).
        """

    @abstractmethod
    def parse_rows(self, output: str, detail: RelationDetail, page: PageQuery) -> RowsPage:
        """
        Args:
            output: The engine's answer to :meth:`rows_sql`.
            detail: The relation's structure.
            page: The request.

        Returns:
            The page.
        """

    def finish_page(
        self,
        detail: RelationDetail,
        page: PageQuery,
        raw_rows: list[Any],
        count: int | None,
    ) -> RowsPage:
        """
        Turn the rows an engine returned into a page.

        Args:
            detail: The relation's structure.
            page: The request.
            raw_rows: One ``[cells, truncation flags, key texts]`` per row.
            count: The exact count, when asked.

        Returns:
            The page, trimmed to the limit, with where the next one starts.
        """
        truncatable = [
            index for index, column in enumerate(detail.columns) if column.kind in _TRUNCATABLE
        ]
        keyset = self.keyset(detail, page)
        result = RowsPage(
            columns=list(detail.columns),
            limit=page.limit,
            offset=0 if keyset else page.offset,
            pagination="keyset" if keyset else "offset",
            count=count,
        )
        for raw in raw_rows[: page.limit]:
            cells, flags, key = raw[0], raw[1], raw[2]
            result.rows.append(list(cells))
            result.truncated.append(
                [index for index, flag in zip(truncatable, flags, strict=False) if flag]
            )
            result.keys.append(
                {
                    name: str(value)
                    for name, value in zip(detail.primary_key, key, strict=False)
                    if value is not None
                }
            )
        result.has_more = len(raw_rows) > page.limit
        if result.has_more:
            if keyset and result.keys:
                last = result.keys[-1]
                result.next_cursor = encode_cursor([last[name] for name in detail.primary_key])
            elif not keyset:
                result.next_offset = page.offset + page.limit
        return result

    # ------------------------------------------------------------ editing

    def check_values(
        self, detail: RelationDetail, values: Mapping[str, Any], *, allow_empty: bool
    ) -> dict[str, Any]:
        """
        Hold an edit's values to the relation's columns.

        Args:
            detail: The relation's structure.
            values: Column name to new value.
            allow_empty: Whether no value at all is acceptable (an insert of
                defaults).

        Returns:
            The values, by column name.

        Raises:
            ValidationError: When a column does not exist, a value is not a
                JSON scalar, object or array, or nothing is given.
        """
        if not values and not allow_empty:
            raise ValidationError(
                "Nothing to change", details="Send at least one column and its new value."
            )
        checked: dict[str, Any] = {}
        for name, value in values.items():
            detail.column(name)
            if isinstance(value, str):
                _check_text(value)
            elif value is not None and not isinstance(value, (bool, int, float, dict, list)):
                raise ValidationError(
                    f"Unsupported value for {name!r}",
                    details="Send a string, a number, a boolean, null, or JSON for a JSON column.",
                )
            checked[name] = value
        return checked

    def check_key(self, detail: RelationDetail, key: Mapping[str, Any]) -> dict[str, str]:
        """
        Hold a row's identity to its full primary key.

        Args:
            detail: The relation's structure.
            key: Primary key column to value, as a page returned it.

        Returns:
            The key, as text, in key order.

        Raises:
            ValidationError: When the relation has no primary key, or the key
                given is not exactly its columns.
        """
        if not detail.editable:
            raise ValidationError(
                f"{detail.schema}.{detail.name} has no primary key, so its rows cannot be edited",
                details=(
                    "A row is edited only when its primary key tells it apart from every "
                    "other. Add a primary key, or change it with the SQL console in write mode."
                ),
            )
        if set(key) != set(detail.primary_key):
            raise ValidationError(
                "A row is identified by its whole primary key",
                details=f"Send exactly these columns: {', '.join(detail.primary_key)}.",
            )
        checked: dict[str, str] = {}
        for name in detail.primary_key:
            value = key[name]
            if value is None or isinstance(value, (dict, list)):
                raise ValidationError(
                    f"Invalid primary key value for {name!r}",
                    details="Send the value as the page returned it.",
                )
            text = value if isinstance(value, str) else json.dumps(value)
            _check_text(text)
            checked[name] = text
        return checked

    @abstractmethod
    def value(self, column: Column, value: Any) -> str:
        """
        Build the SQL for a value an edit writes.

        Args:
            column: The column, from the catalog.
            value: The value, as the request sent it.

        Returns:
            The SQL expression.
        """

    def key_condition(self, detail: RelationDetail, key: Mapping[str, str]) -> str:
        """
        Args:
            detail: The relation's structure.
            key: The row's primary key, checked.

        Returns:
            The condition matching exactly that row.
        """
        return " AND ".join(
            f"{self.ident(name)} = {self.key_value(detail.column(name), key[name])}"
            for name in detail.primary_key
        )

    def key_value(self, column: Column, text: str) -> str:
        """
        Args:
            column: A primary key column.
            text: Its value as a page returned it.

        Returns:
            The literal matching it.
        """
        return self.literal(text)

    @abstractmethod
    def insert_sql(self, detail: RelationDetail, values: Mapping[str, Any]) -> str:
        """
        Args:
            detail: The relation's structure.
            values: The new row's values.

        Returns:
            The script inserting exactly one row and printing its image.
        """

    @abstractmethod
    def update_sql(
        self, detail: RelationDetail, key: Mapping[str, str], values: Mapping[str, Any]
    ) -> str:
        """
        Args:
            detail: The relation's structure.
            key: The row's primary key.
            values: The new values.

        Returns:
            The script changing exactly one row, or nothing.
        """

    @abstractmethod
    def delete_sql(self, detail: RelationDetail, key: Mapping[str, str]) -> str:
        """
        Args:
            detail: The relation's structure.
            key: The row's primary key.

        Returns:
            The script deleting exactly one row, or nothing.
        """

    @abstractmethod
    def parse_change(self, output: str) -> tuple[Any, Any]:
        """
        Args:
            output: The engine's answer to an edit script.

        Returns:
            The row before and after, as the engine rendered them.
        """


#: What the guard of an edit says when the row is not exactly one: the
#: PostgreSQL guard raises this, the MySQL one names this column.
EDIT_GUARD = "noust: expected exactly one row"
MYSQL_EDIT_GUARD = "noust_expected_one_row"


class PostgresDialect(Dialect):
    """PostgreSQL: ``pg_catalog`` for the structure, ``json_agg`` for the rows."""

    ENGINE = "postgresql"

    #: Schemas that belong to the cluster, never listed. Written without a
    #: backslash so it reads the same whatever standard_conforming_strings is.
    SYSTEM_SCHEMAS = (
        "n.nspname NOT IN ('pg_catalog', 'information_schema') AND left(n.nspname, 3) <> 'pg_'"
    )

    def ident(self, name: str) -> str:
        return quote_identifier(name, '"')

    def literal(self, value: str) -> str:
        # quote_literal's rules: doubled quotes, and an E'' string with doubled
        # backslashes when there is a backslash, which means the same thing
        # whether standard_conforming_strings is on or off.
        _check_text(value)
        escaped = value.replace("'", "''")
        if "\\" in value:
            return "E'" + escaped.replace("\\", "\\\\") + "'"
        return "'" + escaped + "'"

    @staticmethod
    def kind(base: str, category: str) -> ColumnKind:
        """
        Classify a PostgreSQL type.

        Args:
            base: ``pg_type.typname``.
            category: ``pg_type.typcategory``.

        Returns:
            Its kind.
        """
        if base in ("json", "jsonb"):
            return "json"
        if base == "bytea":
            return "binary"
        if base == "uuid":
            return "uuid"
        return _PG_CATEGORIES.get(category, "other")

    def catalog_sql(self, database: str) -> str:
        return (
            "SELECT json_build_object("  # noqa: S608 - identifiers from the catalog, values as literals
            "'schemas', coalesce((SELECT json_agg(n.nspname ORDER BY n.nspname) "
            f"FROM pg_catalog.pg_namespace n WHERE {self.SYSTEM_SCHEMAS}), '[]'::json), "
            "'relations', coalesce((SELECT json_agg(json_build_object("
            "'schema', n.nspname, 'name', c.relname, "
            "'kind', CASE c.relkind WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view' "
            "WHEN 'f' THEN 'foreign_table' ELSE 'table' END, "
            "'rows_estimate', CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END, "
            "'size_bytes', CASE WHEN c.relkind IN ('r', 'p', 'm') "
            "THEN pg_catalog.pg_total_relation_size(c.oid) END"
            ") ORDER BY n.nspname, c.relname) "
            "FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND {self.SYSTEM_SCHEMAS}), "
            "'[]'::json))"
        )

    def parse_catalog(self, output: str) -> Catalog:
        data = load_json(output.strip() or "{}", what="the catalog")
        relations = tuple(
            Relation(
                schema=str(item["schema"]),
                name=str(item["name"]),
                kind=str(item["kind"]),
                rows_estimate=_as_int(item.get("rows_estimate")),
                size_bytes=_as_int(item.get("size_bytes")),
            )
            for item in data.get("relations") or []
        )
        return Catalog(
            schemas=tuple(str(name) for name in data.get("schemas") or []), relations=relations
        )

    def describe_sql(self, schema: str, name: str) -> str:
        return (
            "SELECT json_build_object("  # noqa: S608 - identifiers from the catalog, values as literals
            "'columns', coalesce((SELECT json_agg(json_build_object("
            "'name', a.attname, "
            "'type', pg_catalog.format_type(a.atttypid, a.atttypmod), "
            "'base', t.typname, 'category', t.typcategory, "
            "'nullable', NOT a.attnotnull, "
            "'default', pg_catalog.pg_get_expr(d.adbin, d.adrelid), "
            "'generated', a.attidentity <> '' OR a.attgenerated <> ''"
            ") ORDER BY a.attnum) "
            "FROM pg_catalog.pg_attribute a "
            "JOIN pg_catalog.pg_type t ON t.oid = a.atttypid "
            "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
            "WHERE a.attrelid = r.oid AND a.attnum > 0 AND NOT a.attisdropped), '[]'::json), "
            "'primary_key', coalesce((SELECT json_agg(a.attname ORDER BY k.ord) "
            "FROM pg_catalog.pg_constraint con "
            "CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) "
            "JOIN pg_catalog.pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum "
            "WHERE con.conrelid = r.oid AND con.contype = 'p'), '[]'::json), "
            "'indexes', coalesce((SELECT json_agg(json_build_object("
            "'name', ic.relname, 'unique', i.indisunique, 'primary', i.indisprimary, "
            "'definition', pg_catalog.pg_get_indexdef(i.indexrelid), "
            "'columns', (SELECT coalesce(json_agg(a.attname ORDER BY k.ord), '[]'::json) "
            "FROM unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord) "
            "JOIN pg_catalog.pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum)"
            ") ORDER BY ic.relname) "
            "FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid "
            "WHERE i.indrelid = r.oid), '[]'::json), "
            "'constraints', coalesce((SELECT json_agg(json_build_object("
            "'name', con.conname, 'type', con.contype, "
            "'definition', pg_catalog.pg_get_constraintdef(con.oid)) ORDER BY con.conname) "
            "FROM pg_catalog.pg_constraint con WHERE con.conrelid = r.oid), '[]'::json)"
            ") "
            "FROM pg_catalog.pg_class r "
            "JOIN pg_catalog.pg_namespace n ON n.oid = r.relnamespace "
            f"WHERE n.nspname = {self.literal(schema)} AND r.relname = {self.literal(name)}"
        )

    _CONSTRAINT_TYPES: Mapping[str, str] = {
        "p": "primary_key",
        "f": "foreign_key",
        "u": "unique",
        "c": "check",
        "x": "exclusion",
        "t": "trigger",
    }

    def parse_describe(self, output: str, relation: Relation) -> RelationDetail:
        text = output.strip()
        if not text:
            raise_missing(relation)
        data = load_json(text, what="the structure")
        primary_key = tuple(str(name) for name in data.get("primary_key") or [])
        columns = tuple(
            Column(
                name=str(item["name"]),
                type=str(item["type"]),
                kind=self.kind(str(item.get("base") or ""), str(item.get("category") or "")),
                nullable=bool(item.get("nullable", True)),
                default=item.get("default"),
                primary_key=(
                    primary_key.index(str(item["name"])) + 1
                    if str(item["name"]) in primary_key
                    else None
                ),
                generated=bool(item.get("generated")),
            )
            for item in data.get("columns") or []
        )
        return RelationDetail(
            schema=relation.schema,
            name=relation.name,
            kind=relation.kind,
            columns=columns,
            primary_key=primary_key,
            indexes=tuple(
                Index(
                    name=str(item["name"]),
                    columns=tuple(str(column) for column in item.get("columns") or []),
                    unique=bool(item.get("unique")),
                    primary=bool(item.get("primary")),
                    definition=item.get("definition"),
                )
                for item in data.get("indexes") or []
            ),
            constraints=tuple(
                Constraint(
                    name=str(item["name"]),
                    type=self._CONSTRAINT_TYPES.get(str(item.get("type")), "other"),
                    definition=item.get("definition"),
                )
                for item in data.get("constraints") or []
            ),
            rows_estimate=relation.rows_estimate,
            size_bytes=relation.size_bytes,
        )

    def like(self, ident: str, literal: str, *, insensitive: bool) -> str:
        return f"{ident}::text {'ILIKE' if insensitive else 'LIKE'} {literal}"

    def _cell(self, column: Column) -> str:
        """
        Args:
            column: A column of the relation.

        Returns:
            The expression rendering one of its values for a page.
        """
        ref = f"t.{self.ident(column.name)}"
        if column.kind in ("numeric", "boolean"):
            return f"to_json({ref})"
        if column.kind == "binary":
            return (
                f"CASE WHEN {ref} IS NULL THEN NULL ELSE json_build_object("
                f"'bytes', octet_length({ref}), "
                f"'hex', encode(substring({ref} FROM 1 FOR {BINARY_PREVIEW_BYTES}), 'hex')) END"
            )
        if column.kind in _TRUNCATABLE:
            return f"to_json(left({ref}::text, {CELL_TEXT_LIMIT}))"
        return f"to_json({ref}::text)"

    def rows_sql(self, detail: RelationDetail, page: PageQuery) -> str:
        conditions = self.where(detail, page.filters)
        keyset = self.keyset(detail, page)
        order = self.effective_order(detail, page)
        if keyset and page.after is not None:
            descending = bool(order) and order[0].descending
            columns = ", ".join(self.ident(name) for name in detail.primary_key)
            values = ", ".join(
                self.key_value(detail.column(name), text)
                for name, text in zip(detail.primary_key, page.after, strict=True)
            )
            conditions.append(f"({columns}) {'<' if descending else '>'} ({values})")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        inner_order = ", ".join(
            f"{self.ident(sort.column)} {'DESC' if sort.descending else 'ASC'}" for sort in order
        )
        outer_order = ", ".join(
            f"t.{self.ident(sort.column)} {'DESC' if sort.descending else 'ASC'}" for sort in order
        )
        source = self.qualified(detail.schema, detail.name)
        offset = "" if keyset else f" OFFSET {page.offset}"
        cells = ", ".join(self._cell(column) for column in detail.columns)
        flags = ", ".join(
            f"coalesce(length(t.{self.ident(column.name)}::text) > {CELL_TEXT_LIMIT}, false)"
            for column in detail.columns
            if column.kind in _TRUNCATABLE
        )
        keys = ", ".join(f"t.{self.ident(name)}::text" for name in detail.primary_key)
        row = (
            f"json_build_array(array_to_json(ARRAY[{cells}]::json[]), "
            f"array_to_json(ARRAY[{flags}]::boolean[]), "
            f"array_to_json(ARRAY[{keys}]::text[]))"
        )
        count = (
            f", 'count', (SELECT count(*) FROM {source}{self._only_filters(detail, page)})"  # noqa: S608 - identifiers from the catalog, values as literals
            if page.count
            else ""
        )
        return (
            "SELECT json_build_object('rows', coalesce((SELECT json_agg("  # noqa: S608 - identifiers from the catalog, values as literals
            f"{row}{' ORDER BY ' + outer_order if outer_order else ''}) "
            f"FROM (SELECT * FROM {source}{where}"
            f"{' ORDER BY ' + inner_order if inner_order else ''} "
            f"LIMIT {page.limit + 1}{offset}) AS t), '[]'::json){count})"
        )

    def _only_filters(self, detail: RelationDetail, page: PageQuery) -> str:
        """
        Args:
            detail: The relation's structure.
            page: The request.

        Returns:
            The WHERE clause of the filters alone, for the exact count.
        """
        conditions = self.where(detail, page.filters)
        return f" WHERE {' AND '.join(conditions)}" if conditions else ""

    def parse_rows(self, output: str, detail: RelationDetail, page: PageQuery) -> RowsPage:
        data = load_json(output.strip() or "{}", what="the page")
        return self.finish_page(
            detail, page, list(data.get("rows") or []), _as_int(data.get("count"))
        )

    def value(self, column: Column, value: Any) -> str:
        if value is None:
            return "NULL"
        if column.kind == "binary" and isinstance(value, dict):
            return self.literal("\\x" + _hex_of(value, column))
        if isinstance(value, bool):
            return self.literal("true" if value else "false")
        if isinstance(value, (int, float)):
            return self.literal(repr(value))
        if isinstance(value, (dict, list)):
            return self.literal(json.dumps(value, ensure_ascii=False))
        return self.literal(str(value))

    def _edit(self, statement: str, images: str, *, before: str | None = None) -> str:
        """
        Wrap one data change in the guard every edit runs under.

        The change and the row's images land in a temporary table; a DO block
        raises unless exactly one row is there, which rolls the whole
        transaction back (psql stops at the first error, and the session
        ends without a COMMIT).

        Args:
            statement: ``INSERT``/``UPDATE``/``DELETE ... RETURNING
                to_jsonb(t) AS row``, the ``changed`` CTE.
            images: The ``before, after`` pair selected from ``changed``.
            before: A ``SELECT to_jsonb(t) AS row`` reading the row as it
                was, in the same snapshot, as the ``before`` CTE.

        Returns:
            The script.
        """
        with_before = f", before AS ({before})" if before is not None else ""
        return (
            "BEGIN;\n"  # noqa: S608 - identifiers from the catalog, values as literals
            "CREATE TEMPORARY TABLE noust_row_change (before jsonb, after jsonb) ON COMMIT DROP;\n"
            f"WITH changed AS ({statement}){with_before}\n"
            f"INSERT INTO noust_row_change SELECT {images} FROM changed;\n"
            "DO $noust_guard$ BEGIN IF (SELECT count(*) FROM noust_row_change) <> 1 THEN "
            f"RAISE EXCEPTION '{EDIT_GUARD}, found %', (SELECT count(*) FROM noust_row_change); "
            "END IF; END $noust_guard$;\n"
            "SELECT json_build_object('before', before, 'after', after) FROM noust_row_change;\n"
            "COMMIT;\n"
        )

    def insert_sql(self, detail: RelationDetail, values: Mapping[str, Any]) -> str:
        source = self.qualified(detail.schema, detail.name)
        if values:
            columns = ", ".join(self.ident(name) for name in values)
            literals = ", ".join(
                self.value(detail.column(name), value) for name, value in values.items()
            )
            body = f"({columns}) VALUES ({literals})"
        else:
            body = "DEFAULT VALUES"
        return self._edit(
            f"INSERT INTO {source} AS t {body} RETURNING to_jsonb(t) AS row", "NULL, changed.row"
        )

    def update_sql(
        self, detail: RelationDetail, key: Mapping[str, str], values: Mapping[str, Any]
    ) -> str:
        source = self.qualified(detail.schema, detail.name)
        condition = self.key_condition(detail, key)
        assignments = ", ".join(
            f"{self.ident(name)} = {self.value(detail.column(name), value)}"
            for name, value in values.items()
        )
        return self._edit(
            f"UPDATE {source} AS t SET {assignments} WHERE {condition} "  # noqa: S608 - identifiers from the catalog, values as literals
            "RETURNING to_jsonb(t) AS row",
            "(SELECT row FROM before), changed.row",
            before=f"SELECT to_jsonb(t) AS row FROM {source} AS t WHERE {condition}",  # noqa: S608 - identifiers from the catalog, values as literals
        )

    def delete_sql(self, detail: RelationDetail, key: Mapping[str, str]) -> str:
        source = self.qualified(detail.schema, detail.name)
        condition = self.key_condition(detail, key)
        return self._edit(
            f"DELETE FROM {source} AS t WHERE {condition} RETURNING to_jsonb(t) AS row",  # noqa: S608 - identifiers from the catalog, values as literals
            "changed.row, NULL",
        )

    def parse_change(self, output: str) -> tuple[Any, Any]:
        data = load_json(output.strip() or "{}", what="the edit")
        return data.get("before"), data.get("after")


class MySQLDialect(Dialect):
    """MySQL and MariaDB: ``information_schema`` for the structure, one JSON row per line."""

    ENGINE = "mysql"

    #: Whether the server is MariaDB, which names a few things differently.
    mariadb: bool = False

    _NUMERIC = frozenset(
        {
            "tinyint",
            "smallint",
            "mediumint",
            "int",
            "integer",
            "bigint",
            "decimal",
            "numeric",
            "float",
            "double",
            "real",
            "bit",
        }
    )
    _DATETIME = frozenset({"date", "datetime", "timestamp", "time", "year"})
    _BINARY = frozenset(
        {
            "binary",
            "varbinary",
            "tinyblob",
            "blob",
            "mediumblob",
            "longblob",
            "geometry",
            "point",
            "linestring",
            "polygon",
            "multipoint",
            "multilinestring",
            "multipolygon",
            "geometrycollection",
        }
    )
    _TEXT = frozenset(
        {"char", "varchar", "tinytext", "text", "mediumtext", "longtext", "enum", "set"}
    )

    def __init__(self, *, mariadb: bool = False) -> None:
        """
        Args:
            mariadb: The server is MariaDB.
        """
        self.mariadb = mariadb

    def ident(self, name: str) -> str:
        return quote_identifier(name, "`")

    def literal(self, value: str) -> str:
        # Hexadecimal with a character set introducer: the value's bytes, read
        # back as utf8mb4 text with a literal's coercibility, whatever
        # sql_mode (NO_BACKSLASH_ESCAPES, ANSI_QUOTES) the server runs with.
        _check_text(value)
        return f"_utf8mb4 X'{value.encode('utf-8').hex()}'"

    def kind(self, data_type: str) -> ColumnKind:
        """
        Classify a MySQL type.

        Args:
            data_type: ``information_schema.COLUMNS.DATA_TYPE``.

        Returns:
            Its kind.
        """
        base = data_type.lower()
        if base in self._NUMERIC:
            return "numeric"
        if base in self._DATETIME:
            return "datetime"
        if base == "json":
            return "json"
        if base in self._BINARY:
            return "binary"
        if base in self._TEXT:
            return "text"
        return "other"

    def catalog_sql(self, database: str) -> str:
        schema = self.literal(database)
        return (
            "SELECT JSON_ARRAY('relation', TABLE_SCHEMA, TABLE_NAME, "  # noqa: S608 - identifiers from the catalog, values as literals
            "IF(TABLE_TYPE = 'VIEW', 'view', 'table'), TABLE_ROWS, "
            "IF(TABLE_TYPE = 'VIEW', NULL, DATA_LENGTH + INDEX_LENGTH)) "
            f"FROM information_schema.TABLES WHERE TABLE_SCHEMA = {schema} "
            "ORDER BY TABLE_NAME"
        )

    def parse_catalog(self, output: str) -> Catalog:
        relations: list[Relation] = []
        schemas: set[str] = set()
        for line in _lines(output):
            item = load_json(line, what="the catalog")
            if isinstance(item, list) and item and item[0] == "relation":
                schemas.add(str(item[1]))
                relations.append(
                    Relation(
                        schema=str(item[1]),
                        name=str(item[2]),
                        kind=str(item[3]),
                        rows_estimate=_as_int(item[4]) if item[3] == "table" else None,
                        size_bytes=_as_int(item[5]),
                    )
                )
        return Catalog(schemas=tuple(sorted(schemas)), relations=tuple(relations))

    def describe_sql(self, schema: str, name: str) -> str:
        where = f"TABLE_SCHEMA = {self.literal(schema)} AND TABLE_NAME = {self.literal(name)}"
        return (
            "SELECT JSON_ARRAY('column', COLUMN_NAME, COLUMN_TYPE, DATA_TYPE, "  # noqa: S608 - identifiers from the catalog, values as literals
            "IS_NULLABLE = 'YES', COLUMN_DEFAULT, EXTRA, ORDINAL_POSITION) "
            f"FROM information_schema.COLUMNS WHERE {where} "
            "UNION ALL "
            "SELECT JSON_ARRAY('index', INDEX_NAME, COLUMN_NAME, NON_UNIQUE = 0, SEQ_IN_INDEX) "
            f"FROM information_schema.STATISTICS WHERE {where} "
            "UNION ALL "
            "SELECT JSON_ARRAY('constraint', CONSTRAINT_NAME, CONSTRAINT_TYPE) "
            f"FROM information_schema.TABLE_CONSTRAINTS WHERE {where}"
        )

    _CONSTRAINT_TYPES: Mapping[str, str] = {
        "PRIMARY KEY": "primary_key",
        "FOREIGN KEY": "foreign_key",
        "UNIQUE": "unique",
        "CHECK": "check",
    }

    def parse_describe(self, output: str, relation: Relation) -> RelationDetail:
        columns: list[tuple[int, dict[str, Any]]] = []
        indexes: dict[str, dict[str, Any]] = {}
        constraints: list[Constraint] = []
        for line in _lines(output):
            item = load_json(line, what="the structure")
            if not isinstance(item, list) or not item:
                continue
            tag = item[0]
            if tag == "column":
                columns.append(
                    (
                        _as_int(item[7]) or 0,
                        {
                            "name": str(item[1]),
                            "type": str(item[2]),
                            "kind": self.kind(str(item[3])),
                            "nullable": bool(item[4]),
                            "default": None if item[5] is None else str(item[5]),
                            "generated": "auto_increment" in str(item[6] or "").lower()
                            or "generated" in str(item[6] or "").lower(),
                        },
                    )
                )
            elif tag == "index":
                entry = indexes.setdefault(str(item[1]), {"columns": {}, "unique": bool(item[3])})
                entry["columns"][_as_int(item[4]) or 0] = str(item[2])
            elif tag == "constraint":
                constraints.append(
                    Constraint(
                        name=str(item[1]),
                        type=self._CONSTRAINT_TYPES.get(str(item[2]).upper(), "other"),
                    )
                )
        if not columns:
            raise_missing(relation)
        primary = indexes.get("PRIMARY")
        primary_key = (
            tuple(primary["columns"][seq] for seq in sorted(primary["columns"])) if primary else ()
        )
        ordered = [spec for _, spec in sorted(columns, key=lambda pair: pair[0])]
        return RelationDetail(
            schema=relation.schema,
            name=relation.name,
            kind=relation.kind,
            columns=tuple(
                Column(
                    **spec,
                    primary_key=(
                        primary_key.index(spec["name"]) + 1 if spec["name"] in primary_key else None
                    ),
                )
                for spec in ordered
            ),
            primary_key=primary_key,
            indexes=tuple(
                Index(
                    name=name,
                    columns=tuple(entry["columns"][seq] for seq in sorted(entry["columns"])),
                    unique=bool(entry["unique"]),
                    primary=name == "PRIMARY",
                )
                for name, entry in sorted(indexes.items())
            ),
            constraints=tuple(constraints),
            rows_estimate=relation.rows_estimate,
            size_bytes=relation.size_bytes,
        )

    def like(self, ident: str, literal: str, *, insensitive: bool) -> str:
        if insensitive:
            return f"LOWER({ident}) LIKE LOWER({literal})"
        return f"{ident} LIKE {literal}"

    def _cell(self, column: Column, ref: str) -> str:
        """
        Args:
            column: A column of the relation.
            ref: The quoted column reference.

        Returns:
            The expression rendering one of its values for a page.
        """
        if column.kind == "numeric":
            return f"{ref} + 0" if column.type.lower().startswith("bit") else ref
        if column.kind == "binary":
            return (
                f"IF({ref} IS NULL, NULL, JSON_OBJECT('bytes', LENGTH({ref}), "
                f"'hex', LOWER(HEX(LEFT({ref}, {BINARY_PREVIEW_BYTES})))))"
            )
        if column.kind in _TRUNCATABLE:
            return f"LEFT(CAST({ref} AS CHAR), {CELL_TEXT_LIMIT})"
        return f"CAST({ref} AS CHAR)"

    def _key_text(self, column: Column, ref: str) -> str:
        """
        Args:
            column: A primary key column.
            ref: The quoted column reference.

        Returns:
            The expression rendering its value as the text an edit sends back.
        """
        if column.kind == "binary":
            return f"LOWER(HEX({ref}))"
        return f"CAST({ref} AS CHAR)"

    def key_value(self, column: Column, text: str) -> str:
        if column.kind == "binary":
            return f"X'{_valid_hex(text, column)}'"
        return self.literal(text)

    def rows_sql(self, detail: RelationDetail, page: PageQuery) -> str:
        conditions = self.where(detail, page.filters)
        filters_only = list(conditions)
        keyset = self.keyset(detail, page)
        order = self.effective_order(detail, page)
        if keyset and page.after is not None:
            descending = bool(order) and order[0].descending
            columns = ", ".join(self.ident(name) for name in detail.primary_key)
            values = ", ".join(
                self.key_value(detail.column(name), text)
                for name, text in zip(detail.primary_key, page.after, strict=True)
            )
            conditions.append(f"({columns}) {'<' if descending else '>'} ({values})")
        source = self.qualified(detail.schema, detail.name)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        order_by = ", ".join(
            f"{self.ident(sort.column)} {'DESC' if sort.descending else 'ASC'}" for sort in order
        )
        cells = ", ".join(self._cell(column, self.ident(column.name)) for column in detail.columns)
        flags = ", ".join(
            f"COALESCE(CHAR_LENGTH(CAST({self.ident(column.name)} AS CHAR)) > {CELL_TEXT_LIMIT}, 0)"
            for column in detail.columns
            if column.kind in _TRUNCATABLE
        )
        keys = ", ".join(
            self._key_text(detail.column(name), self.ident(name)) for name in detail.primary_key
        )
        offset = "" if keyset else f" OFFSET {page.offset}"
        statements = []
        if page.count:
            only = f" WHERE {' AND '.join(filters_only)}" if filters_only else ""
            statements.append(f"SELECT JSON_ARRAY('count', COUNT(*)) FROM {source}{only};")  # noqa: S608 - identifiers from the catalog, values as literals
        statements.append(
            f"SELECT JSON_ARRAY(JSON_ARRAY({cells}), JSON_ARRAY({flags}), JSON_ARRAY({keys})) "  # noqa: S608 - identifiers from the catalog, values as literals
            f"FROM {source}{where}{' ORDER BY ' + order_by if order_by else ''} "
            f"LIMIT {page.limit + 1}{offset};"
        )
        return "\n".join(statements)

    def parse_rows(self, output: str, detail: RelationDetail, page: PageQuery) -> RowsPage:
        count: int | None = None
        rows: list[Any] = []
        for line in _lines(output):
            item = load_json(line, what="the page")
            if isinstance(item, list) and len(item) == 2 and item[0] == "count":
                count = _as_int(item[1])
            elif isinstance(item, list) and len(item) == 3:
                rows.append(item)
        return self.finish_page(detail, page, rows, count)

    def value(self, column: Column, value: Any) -> str:
        if value is None:
            return "NULL"
        if column.kind == "binary" and isinstance(value, dict):
            return f"X'{_hex_of(value, column)}'"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValidationError(
                    f"Invalid number for {column.name!r}",
                    details="MySQL stores no infinity or NaN.",
                )
            return repr(value)
        if isinstance(value, (dict, list)):
            return self.literal(json.dumps(value, ensure_ascii=False))
        return self.literal(str(value))

    def _image(self, detail: RelationDetail, condition: str, tag: str) -> str:
        """
        Args:
            detail: The relation's structure.
            condition: The row's key condition.
            tag: ``before`` or ``after``.

        Returns:
            A SELECT printing the row's image as one tagged JSON line.
        """
        pairs = ", ".join(
            f"{self.literal(column.name)}, {self._cell(column, self.ident(column.name))}"
            for column in detail.columns
        )
        return (
            f"SELECT JSON_ARRAY('{tag}', JSON_OBJECT({pairs})) "  # noqa: S608 - identifiers from the catalog, values as literals
            f"FROM {self.qualified(detail.schema, detail.name)} WHERE {condition}"
        )

    @staticmethod
    def _guard(count: str) -> str:
        """
        Args:
            count: An expression that must be exactly 1.

        Returns:
            Statements that stop the script, and so roll it back, when it is not.
        """
        # A plain script has no IF; a NOT NULL column does the asserting on
        # every MySQL and MariaDB: a single-row INSERT of NULL fails in any
        # sql_mode, the client stops at the error, and the transaction ends
        # without a COMMIT. The column's name is what the error then says.
        return (
            f"SET @noust_rows = ({count});\n"
            f"INSERT INTO noust_row_guard ({MYSQL_EDIT_GUARD}) "
            "VALUES (IF(@noust_rows = 1, 1, NULL));\n"
        )

    _PROLOGUE = (
        "START TRANSACTION;\n"
        f"CREATE TEMPORARY TABLE noust_row_guard ({MYSQL_EDIT_GUARD} INT NOT NULL);\n"
    )

    def insert_sql(self, detail: RelationDetail, values: Mapping[str, Any]) -> str:
        source = self.qualified(detail.schema, detail.name)
        if values:
            columns = ", ".join(self.ident(name) for name in values)
            literals = ", ".join(
                self.value(detail.column(name), value) for name, value in values.items()
            )
            insert = f"INSERT INTO {source} ({columns}) VALUES ({literals});\n"  # noqa: S608 - identifiers from the catalog, values as literals
        else:
            insert = f"INSERT INTO {source} () VALUES ();\n"  # noqa: S608 - identifiers from the catalog, values as literals
        after = self._inserted_key(detail, values)
        image = f"{self._image(detail, after, 'after')};\n" if after else ""
        return f"{self._PROLOGUE}{insert}{self._guard('ROW_COUNT()')}{image}COMMIT;\n"

    def _inserted_key(self, detail: RelationDetail, values: Mapping[str, Any]) -> str | None:
        """
        Find the condition that reads back the row an insert made.

        Args:
            detail: The relation's structure.
            values: The values inserted.

        Returns:
            The key condition: from the values when they hold the whole key,
            from ``LAST_INSERT_ID()`` for a single auto-increment key, else
            None (the image is then not read back).
        """
        if all(name in values and values[name] is not None for name in detail.primary_key):
            return " AND ".join(
                f"{self.ident(name)} = {self.value(detail.column(name), values[name])}"
                for name in detail.primary_key
            )
        if len(detail.primary_key) == 1 and detail.column(detail.primary_key[0]).generated:
            return f"{self.ident(detail.primary_key[0])} = LAST_INSERT_ID()"
        return None

    def update_sql(
        self, detail: RelationDetail, key: Mapping[str, str], values: Mapping[str, Any]
    ) -> str:
        source = self.qualified(detail.schema, detail.name)
        condition = self.key_condition(detail, key)
        assignments = ", ".join(
            f"{self.ident(name)} = {self.value(detail.column(name), value)}"
            for name, value in values.items()
        )
        # A primary key column the update changes is read back at its new value.
        moved = {name: values[name] for name in detail.primary_key if name in values}
        after = (
            " AND ".join(
                f"{self.ident(name)} = "
                + (
                    self.value(detail.column(name), moved[name])
                    if name in moved
                    else self.key_value(detail.column(name), key[name])
                )
                for name in detail.primary_key
            )
            if moved
            else condition
        )
        return (
            f"{self._PROLOGUE}"  # noqa: S608 - identifiers from the catalog, values as literals
            f"{self._image(detail, condition, 'before')} FOR UPDATE;\n"
            f"{self._guard(f'SELECT COUNT(*) FROM {source} WHERE {condition}')}"  # noqa: S608 - identifiers from the catalog, values as literals
            f"UPDATE {source} SET {assignments} WHERE {condition};\n"
            f"{self._image(detail, after, 'after')};\n"
            "COMMIT;\n"
        )

    def delete_sql(self, detail: RelationDetail, key: Mapping[str, str]) -> str:
        source = self.qualified(detail.schema, detail.name)
        condition = self.key_condition(detail, key)
        return (
            f"{self._PROLOGUE}"
            f"{self._image(detail, condition, 'before')} FOR UPDATE;\n"
            f"DELETE FROM {source} WHERE {condition};\n"
            f"{self._guard('ROW_COUNT()')}"
            "COMMIT;\n"
        )

    def parse_change(self, output: str) -> tuple[Any, Any]:
        before: Any = None
        after: Any = None
        for line in _lines(output):
            item = load_json(line, what="the edit")
            if isinstance(item, list) and len(item) == 2:
                if item[0] == "before":
                    before = item[1]
                elif item[0] == "after":
                    after = item[1]
        return before, after


# ============================================================ shared


def raise_missing(relation: Relation) -> NoReturn:
    """
    Args:
        relation: The relation the catalog listed.

    Raises:
        DatabaseNotFoundError: Always: the catalog listed it, the engine no
            longer describes it.
    """
    raise DatabaseNotFoundError(
        f"{relation.schema}.{relation.name} is gone",
        details="It was dropped or renamed since the list was read; reload the list.",
    )


def _lines(output: str) -> list[str]:
    """
    Args:
        output: A client's output.

    Returns:
        Its non-empty lines.
    """
    return [line for line in output.splitlines() if line.strip()]


def _valid_hex(text: str, column: Column) -> str:
    """
    Args:
        text: Hexadecimal digits.
        column: The column they are for, for the error.

    Returns:
        The digits, lower case.

    Raises:
        ValidationError: When they are not an even run of hex digits.
    """
    digits = text.strip().lower().removeprefix("\\x").removeprefix("0x")
    if len(digits) % 2 or any(char not in "0123456789abcdef" for char in digits):
        raise ValidationError(
            f"Invalid hexadecimal value for {column.name!r}",
            details="Send binary values as an even number of hexadecimal digits.",
        )
    return digits


def _hex_of(value: Mapping[str, Any], column: Column) -> str:
    """
    Read a binary value an edit sends as ``{"hex": "..."}``.

    Args:
        value: The object.
        column: The column, for the error.

    Returns:
        The digits.

    Raises:
        ValidationError: When the object is not ``{"hex": "<digits>"}``.
    """
    digits = value.get("hex")
    if set(value) != {"hex"} or not isinstance(digits, str):
        raise ValidationError(
            f"Invalid binary value for {column.name!r}",
            details='Send binary values as {"hex": "<hexadecimal digits>"}.',
        )
    return _valid_hex(digits, column)


def dialect_for(engine: str, *, mariadb: bool = False) -> Dialect | None:
    """
    Find the dialect of an engine.

    Args:
        engine: The engine's canonical name.
        mariadb: The MySQL-family server is MariaDB.

    Returns:
        The dialect, or None for an engine with no SQL tables (Redis, MongoDB).
    """
    if engine == "postgresql":
        return PostgresDialect()
    if engine == "mysql":
        return MySQLDialect(mariadb=mariadb)
    return None
