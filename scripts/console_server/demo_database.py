# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What a database's own page reads (3.1): the data browser, the row editor and the demonstration
engine in memory that answers them."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from .engine_settings import _DatabaseContainer

# ---------------------------------------------------------------------------
# Console: what a database's own page reads (3.1). The data browser, the row
# editor, the metrics, the access list and the dumps run their own catalog
# queries, answered here from a small model of example_production's tables:
# each query is matched by a fragment only it contains, before the generic
# listing answers of _sql.
# ---------------------------------------------------------------------------

#: The one database the model has tables for.
_DEMO_DATABASE = "example_production"

#: A column: name, type as printed, pg_type.typname, typcategory, nullable,
#: default, generated.
_DemoColumn = tuple[str, str, str, str, bool, str | None, bool]


def _demo_catalog() -> dict[tuple[str, str], dict[str, Any]]:
    """
    Build the model's tables, with their rows.

    Returns:
        ``(schema, name)`` to the relation: its kind, columns, primary key,
        indexes, constraints and rows (column name to Python value).
    """
    now = datetime(2026, 9, 29, 17, 42, 11, tzinfo=timezone.utc)
    names = ("Maria", "Jon", "Priya", "Lucas", "Aiko", "Omar", "Sofia", "Ines", "Tomas", "Lena")
    countries = ("ES", "PT", "FR", "DE", None, "IE", "NL")
    customers = [
        {
            "id": index,
            "email": f"{names[index % len(names)].lower()}{index}@example.com",
            "name": f"{names[index % len(names)]} {chr(65 + index % 26)}.",
            "country": countries[index % len(countries)],
            "created_at": (now - timedelta(days=400 - index)).isoformat(),
        }
        for index in range(1, 241)
    ]
    statuses = ("paid", "paid", "shipped", "pending", "refunded", "paid", "shipped")
    long_note = ("Customer asked to split the delivery. " * 70).strip()
    orders = []
    for index in range(1284):
        status = statuses[index % len(statuses)]
        receipt = None
        if index % 9 == 0:
            receipt = bytes.fromhex(
                "255044462d312e370a25e2e3cfd30a312030206f626a0a3c3c2f547970652f436174"
            )
        orders.append(
            {
                "id": 1001 + index,
                "customer_id": 1 + (index * 7) % 240,
                "total": round(((index * 37) % 500) + 9.9, 2),
                "status": status,
                "paid": status in ("paid", "shipped", "refunded"),
                "created_at": (now - timedelta(hours=3 * (1284 - index))).isoformat(),
                "notes": long_note if index == 1280 else ("Gift wrap" if index % 13 == 0 else None),
                "metadata": json.dumps(
                    {
                        "channel": "web" if index % 3 else "shop",
                        "coupon": None if index % 5 else "AUTUMN10",
                    }
                ),
                "receipt": receipt,
            }
        )
    items = [
        {
            "order_id": 1001 + index // 3,
            "sku": f"SKU-{(index * 11) % 97:03d}",
            "quantity": 1 + index % 4,
            "unit_price": round(4.5 + (index % 40) * 1.25, 2),
        }
        for index in range(3000)
    ]
    events = [
        {
            "at": (now - timedelta(minutes=17 * index)).isoformat(),
            "actor": ("worker", "web", "cron")[index % 3],
            "action": ("login", "checkout", "export", "refund")[index % 4],
        }
        for index in range(500)
    ]
    revenue = [
        {
            "day": (now - timedelta(days=index)).date().isoformat(),
            "orders": 8 + (index * 5) % 17,
            "revenue": round(420 + (index * 97) % 900 + 0.5, 2),
        }
        for index in range(90)
    ]
    bigint = ("bigint", "int8", "N")
    text = ("text", "text", "S")
    stamp = ("timestamp with time zone", "timestamptz", "D")

    def col(
        name: str,
        kind: tuple[str, str, str],
        nullable: bool = False,
        default: str | None = None,
        generated: bool = False,
    ) -> _DemoColumn:
        return (name, kind[0], kind[1], kind[2], nullable, default, generated)

    return {
        ("public", "customers"): {
            "kind": "table",
            "columns": [
                col("id", bigint, generated=True),
                col("email", text),
                col("name", text),
                col("country", text, True),
                col("created_at", stamp, default="now()"),
            ],
            "primary_key": ["id"],
            "indexes": [
                ("customers_pkey", ["id"], True, True),
                ("customers_email_key", ["email"], True, False),
            ],
            "constraints": [
                ("customers_pkey", "p", "PRIMARY KEY (id)"),
                ("customers_email_key", "u", "UNIQUE (email)"),
            ],
            "rows": customers,
            "size": 90_112,
        },
        ("public", "orders"): {
            "kind": "table",
            "columns": [
                col("id", bigint, default="nextval('orders_id_seq'::regclass)"),
                col("customer_id", bigint),
                col("total", ("numeric(10,2)", "numeric", "N")),
                col("status", text, default="'pending'::text"),
                col("paid", ("boolean", "bool", "B"), default="false"),
                col("created_at", stamp, default="now()"),
                col("notes", text, True),
                col("metadata", ("jsonb", "jsonb", "U"), default="'{}'::jsonb"),
                col("receipt", ("bytea", "bytea", "U"), True),
            ],
            "primary_key": ["id"],
            "indexes": [
                ("orders_pkey", ["id"], True, True),
                ("orders_customer_id_idx", ["customer_id"], False, False),
                ("orders_created_at_idx", ["created_at"], False, False),
            ],
            "constraints": [
                (
                    "orders_customer_id_fkey",
                    "f",
                    "FOREIGN KEY (customer_id) REFERENCES customers(id)",
                ),
                ("orders_pkey", "p", "PRIMARY KEY (id)"),
                ("orders_total_check", "c", "CHECK ((total >= (0)::numeric))"),
            ],
            "rows": orders,
            "size": 1_556_480,
        },
        ("public", "order_items"): {
            "kind": "table",
            "columns": [
                col("order_id", bigint),
                col("sku", text),
                col("quantity", ("integer", "int4", "N"), default="1"),
                col("unit_price", ("numeric(10,2)", "numeric", "N")),
            ],
            "primary_key": ["order_id", "sku"],
            "indexes": [("order_items_pkey", ["order_id", "sku"], True, True)],
            "constraints": [
                ("order_items_order_id_fkey", "f", "FOREIGN KEY (order_id) REFERENCES orders(id)"),
                ("order_items_pkey", "p", "PRIMARY KEY (order_id, sku)"),
            ],
            "rows": items,
            "size": 434_176,
        },
        ("public", "audit_events"): {
            "kind": "table",
            "columns": [col("at", stamp, default="now()"), col("actor", text), col("action", text)],
            "primary_key": [],
            "indexes": [],
            "constraints": [],
            "rows": events,
            "size": 73_728,
        },
        ("public", "paid_orders"): {
            "kind": "view",
            "columns": [
                col("id", bigint, True),
                col("total", ("numeric(10,2)", "numeric", "N"), True),
                col("created_at", stamp, True),
            ],
            "primary_key": [],
            "indexes": [],
            "constraints": [],
            "rows": [
                {"id": row["id"], "total": row["total"], "created_at": row["created_at"]}
                for row in orders
                if row["paid"]
            ],
            "size": None,
        },
        ("analytics", "daily_revenue"): {
            "kind": "table",
            "columns": [
                col("day", ("date", "date", "D")),
                col("orders", ("integer", "int4", "N")),
                col("revenue", ("numeric(12,2)", "numeric", "N")),
            ],
            "primary_key": ["day"],
            "indexes": [("daily_revenue_pkey", ["day"], True, True)],
            "constraints": [("daily_revenue_pkey", "p", "PRIMARY KEY (day)")],
            "rows": revenue,
            "size": 16_384,
        },
    }


_DEMO_KINDS = {"N": "numeric", "B": "boolean", "D": "datetime", "S": "text", "A": "array"}


def _demo_kind(column: _DemoColumn) -> str:
    """How the dialect classifies a model column (PostgresDialect.kind)."""
    if column[2] in ("json", "jsonb"):
        return "json"
    if column[2] == "bytea":
        return "binary"
    return _DEMO_KINDS.get(column[3], "other")


def _demo_literal(text: str) -> str:
    """The value inside a PostgreSQL literal ('...' or E'...')."""
    body = text[1:] if text.startswith(("E'", "e'")) else text
    body = body[1:-1] if body.startswith("'") and body.endswith("'") else body
    value = body.replace("''", "'")
    return value.replace("\\\\", "\\") if text.startswith(("E'", "e'")) else value


def _demo_cell(value: Any, kind: str) -> tuple[Any, bool]:
    """A value as the browser's SQL renders it, and whether it was cut."""
    if value is None:
        return None, False
    if kind == "binary":
        raw = value if isinstance(value, bytes) else b""
        return {"bytes": len(raw) * 64, "hex": raw[:32].hex()}, False
    if kind in ("numeric", "boolean"):
        return value, False
    text = str(value)
    if kind in ("text", "json", "array", "other"):
        return text[:2000], len(text) > 2000
    return text, False


def _demo_matches(row: dict[str, Any], condition: str, columns: dict[str, _DemoColumn]) -> bool:
    """Whether a row satisfies one condition of PostgresDialect.condition's shapes."""
    condition = condition.strip()
    match = re.fullmatch(r'"(.+?)" IS (NOT )?NULL', condition)
    if match:
        return (row.get(match.group(1)) is None) != bool(match.group(2))
    match = re.fullmatch(r'"(.+?)"::text (I?LIKE) (E?\'.*\')', condition)
    if match:
        pattern = (
            "^"
            + re.escape(_demo_literal(match.group(3))).replace("%", ".*").replace("_", ".")
            + "$"
        )
        value = row.get(match.group(1))
        flags = re.IGNORECASE if match.group(2) == "ILIKE" else 0
        return value is not None and re.search(pattern, str(value), flags) is not None
    match = re.fullmatch(r'"(.+?)" IN \((.*)\)', condition)
    if match:
        options = [
            _demo_literal(item.strip()) for item in re.findall(r"E?'(?:[^']|'')*'", match.group(2))
        ]
        return str(row.get(match.group(1))) in options
    match = re.fullmatch(r"\((.+?)\) ([<>]) \((.+)\)", condition)
    if match:
        names = re.findall(r'"(.+?)"', match.group(1))
        wanted = [_demo_literal(item) for item in re.findall(r"E?'(?:[^']|'')*'", match.group(3))]
        have = tuple(_demo_sortable(row.get(name), columns[name]) for name in names)
        other = tuple(
            _demo_sortable(_demo_typed(text, columns[name]), columns[name])
            for name, text in zip(names, wanted, strict=False)
        )
        return have > other if match.group(2) == ">" else have < other
    match = re.fullmatch(r'"(.+?)" (=|<>|<=|>=|<|>) (E?\'.*\')', condition)
    if match:
        name, operator = match.group(1), match.group(2)
        column = columns.get(name)
        if column is None:
            return True
        left = _demo_sortable(row.get(name), column)
        right = _demo_sortable(_demo_typed(_demo_literal(match.group(3)), column), column)
        if row.get(name) is None:
            return False
        return {
            "=": left == right,
            "<>": left != right,
            "<": left < right,
            "<=": left <= right,
            ">": left > right,
            ">=": left >= right,
        }[operator]
    return True


def _demo_typed(text: str, column: _DemoColumn) -> Any:
    """A literal's text as the model stores the column's values."""
    kind = _demo_kind(column)
    if kind == "numeric":
        try:
            number = float(text)
        except ValueError:
            return text
        return int(number) if number.is_integer() and "." not in text else number
    if kind == "boolean":
        return text.lower() in ("true", "t", "1")
    return text


def _demo_sortable(value: Any, column: _DemoColumn) -> Any:
    """A value made comparable with the others of its column."""
    if value is None:
        return (1, 0)
    if _demo_kind(column) == "numeric":
        try:
            return (0, float(value))
        except (TypeError, ValueError):
            return (0, 0.0)
    return (0, str(value))


#: The extensions the modelled PostgreSQL's packages installed: name, default version, what it is,
#: whether a database owner may load it, and what it needs loaded first.
_PG_AVAILABLE_EXTENSIONS: dict[str, tuple[str, str, bool, str]] = {
    "pg_trgm": ("1.6", "text similarity measurement and index searching", True, ""),
    "pgcrypto": ("1.3", "cryptographic functions", True, ""),
    "postgis": ("3.4.2", "PostGIS geometry and geography spatial types", False, ""),
    "postgis_topology": ("3.4.2", "PostGIS topology spatial types", False, "postgis"),
}


class _DemoDatabase:
    """example_production as the data browser, the metrics and the access list read it."""

    def __init__(self) -> None:
        self.relations = _demo_catalog()
        self.lock = threading.Lock()
        # Roles the console created this run: a new user is looked up right after it is made.
        self.roles: set[str] = set()

    def answer(
        self,
        database: str | None,
        statement: str,
        *,
        csv: bool,
        container: _DatabaseContainer | None = None,
    ) -> str | None:
        """
        Answer a statement the database page sends, or None to leave it to _sql.

        Args:
            database: The database the client was pointed at (``-d``).
            statement: The SQL.
            csv: The structured console asked for a header row.
            container: The database container the client runs in, whose databases and
                roles the server-wide answers describe instead of the host's.

        Returns:
            What psql prints, or None.
        """
        mine = database == _DEMO_DATABASE
        created = re.match(r'\s*CREATE (?:ROLE|USER) "?([A-Za-z0-9_]+)"?', statement)
        if created:
            self.roles.add(created.group(1))
            return None
        asked = re.match(r"\s*SELECT 1 FROM pg_roles WHERE rolname = '([^']*)'", statement)
        if asked and asked.group(1) in self.roles:
            return "1\n"
        if "'schemas', coalesce((SELECT json_agg(n.nspname" in statement:
            return json.dumps(self.catalog() if mine else {"schemas": ["public"], "relations": []})
        if "'columns', coalesce((SELECT json_agg(json_build_object(" in statement and mine:
            return self.describe(statement)
        if "SELECT json_build_object('rows'" in statement and mine:
            return self.rows(statement)
        if "noust_row_change" in statement and mine:
            return self.edit(statement)
        if container is not None and "'blks_hit', (SELECT sum(blks_hit)" in statement:
            return json.dumps(
                {
                    "connections": 2,
                    "max_connections": 100,
                    "blks_hit": 4_120_311,
                    "blks_read": 20_311,
                    "databases": [
                        {
                            "name": name,
                            "size_bytes": size,
                            "connections": 2,
                            "xact": 318_207,
                            "blks_hit": 4_120_311,
                            "blks_read": 20_311,
                        }
                        for name, (_owner, size) in container.databases.items()
                        if name != "postgres"
                    ],
                }
            )
        if "'blks_hit', (SELECT sum(blks_hit) FROM pg_catalog.pg_stat_database)" in statement:
            return json.dumps(
                {
                    "connections": 9,
                    "max_connections": 100,
                    "blks_hit": 91_827_345,
                    "blks_read": 120_311,
                    "databases": [
                        {
                            "name": "example_production",
                            "size_bytes": 48_218_931,
                            "connections": 6,
                            "xact": 1_829_384,
                            "blks_hit": 77_123_450,
                            "blks_read": 98_765,
                        },
                        {
                            "name": "example_staging",
                            "size_bytes": 9_120_563,
                            "connections": 1,
                            "xact": 45_012,
                            "blks_hit": 1_203_111,
                            "blks_read": 9_871,
                        },
                    ],
                }
            )
        if "'server_connections', (SELECT count(*) FROM pg_catalog.pg_stat_activity" in statement:
            size = container.databases.get(database or "", (None, 0))[1] if container else None
            return self.database_metrics(mine, size_bytes=size)
        if "e.extname = 'pg_stat_statements'" in statement:
            return json.dumps({"schema": "public" if mine else None, "preloaded": mine})
        if "pg_stat_statements p" in statement:
            return json.dumps(
                [
                    {
                        "query": "SELECT o.*, c.email FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.created_at > $1 ORDER BY o.created_at DESC",
                        "calls": 18_422,
                        "total_ms": 1_214_993.2,
                        "mean_ms": 65.95,
                        "rows": 921_100,
                    },
                    {
                        "query": "UPDATE orders SET status = $1, paid = $2 WHERE id = $3",
                        "calls": 4_102,
                        "total_ms": 61_530.4,
                        "mean_ms": 15.0,
                        "rows": 4_102,
                    },
                    {
                        "query": "SELECT count(*) FROM order_items WHERE order_id = ANY($1)",
                        "calls": 9_870,
                        "total_ms": 88_830.0,
                        "mean_ms": 9.0,
                        "rows": 9_870,
                    },
                    {
                        "query": "INSERT INTO audit_events (actor, action) VALUES ($1, $2)",
                        "calls": 52_004,
                        "total_ms": 104_008.1,
                        "mean_ms": 2.0,
                        "rows": 52_004,
                    },
                ]
            )
        if "aclexplode(d.defaclacl)" in statement:
            if container is not None:
                # The image's POSTGRES_USER owns what it created and is the superuser.
                return "catalogo|t|t|0|0|0|f|f\n"
            if not mine:
                return "app_owner|t|f|0|0|0|f|f\npostgres|f|t|0|0|0|f|f\n"
            return (
                "analytics_reader|f|f|5|5|0|f|f\n"
                "example_production|f|f|5|5|5|t|t\n"
                "postgres|f|t|5|5|5|f|f\n"
                "app_owner|t|f|5|5|5|f|f\n"
                "noust_ro_example_production|f|f|5|5|0|t|f\n"
            )
        if mine and re.search(r"^\s*EXPLAIN\b", statement, re.IGNORECASE | re.MULTILINE):
            analyze = "ANALYZE" in statement.upper()
            node: dict[str, Any] = {
                "Node Type": "Limit",
                "Total Cost": 12.4,
                "Plan Rows": 20,
                "Plans": [
                    {
                        "Node Type": "Index Scan Backward",
                        "Relation Name": "orders",
                        "Index Name": "orders_created_at_idx",
                        "Total Cost": 88.1,
                        "Plan Rows": 1284,
                    }
                ],
            }
            if analyze:
                node["Actual Total Time"] = 0.412
                node["Plans"][0]["Actual Total Time"] = 0.388
            return json.dumps([{"Plan": node, "Planning Time": 0.11}])
        if mine and csv and re.search(r"\bfrom\s+(public\.)?orders\b", statement, re.IGNORECASE):
            rows = self.relations[("public", "orders")]["rows"][-20:][::-1]
            lines = ["id,customer_id,total,status,paid,created_at"]
            lines += [
                f"{row['id']},{row['customer_id']},{row['total']:.2f},{row['status']},{'t' if row['paid'] else 'f'},{row['created_at']}"
                for row in rows
            ]
            return "\n".join(lines) + "\n"
        return None

    def catalog(self) -> dict[str, Any]:
        """The schemas and relations, as PostgresDialect.catalog_sql answers."""
        return {
            "schemas": sorted({schema for schema, _ in self.relations}),
            "relations": [
                {
                    "schema": schema,
                    "name": name,
                    "kind": relation["kind"],
                    "rows_estimate": len(relation["rows"]) if relation["kind"] == "table" else None,
                    "size_bytes": relation["size"],
                }
                for (schema, name), relation in sorted(self.relations.items())
            ],
        }

    def _relation(self, statement: str) -> tuple[tuple[str, str], dict[str, Any]] | None:
        match = (
            re.search(r'FROM "([^"]+)"\."([^"]+)"', statement)
            or re.search(r'INTO "([^"]+)"\."([^"]+)"', statement)
            or re.search(r'UPDATE "([^"]+)"\."([^"]+)"', statement)
        )
        if match is None:
            literals = re.findall(r"n\.nspname = '([^']*)' AND r\.relname = '([^']*)'", statement)
            if not literals:
                return None
            key = literals[0]
        else:
            key = (match.group(1), match.group(2))
        relation = self.relations.get(key)
        return (key, relation) if relation is not None else None

    def describe(self, statement: str) -> str:
        """A relation's structure, as PostgresDialect.describe_sql answers."""
        found = self._relation(statement)
        if found is None:
            return ""
        _, relation = found
        return json.dumps(
            {
                "columns": [
                    {
                        "name": c[0],
                        "type": c[1],
                        "base": c[2],
                        "category": c[3],
                        "nullable": c[4],
                        "default": c[5],
                        "generated": c[6],
                    }
                    for c in relation["columns"]
                ],
                "primary_key": relation["primary_key"],
                "indexes": [
                    {
                        "name": name,
                        "columns": cols,
                        "unique": unique,
                        "primary": primary,
                        "definition": f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} ON {found[0][0]}.{found[0][1]} USING btree ({', '.join(cols)})",
                    }
                    for name, cols, unique, primary in relation["indexes"]
                ],
                "constraints": [
                    {"name": name, "type": kind, "definition": definition}
                    for name, kind, definition in relation["constraints"]
                ],
            }
        )

    def rows(self, statement: str) -> str:
        """One page of rows, as PostgresDialect.rows_sql answers."""
        found = self._relation(statement)
        if found is None:
            return json.dumps({"rows": []})
        _, relation = found
        columns = {column[0]: column for column in relation["columns"]}
        inner = statement[statement.index("FROM (SELECT * FROM") :]
        where = re.search(r" WHERE (.+?)(?: ORDER BY | LIMIT )", inner)
        conditions = re.split(r" AND (?=[\"(])", where.group(1)) if where else []
        with self.lock:
            rows = [
                row
                for row in relation["rows"]
                if all(_demo_matches(row, condition, columns) for condition in conditions)
            ]
        order = re.search(r"ORDER BY (.+?) LIMIT", inner)
        if order:
            for name, direction in reversed(re.findall(r'"(.+?)" (ASC|DESC)', order.group(1))):
                if name in columns:
                    rows.sort(
                        key=lambda row, n=name: _demo_sortable(row.get(n), columns[n]),
                        reverse=direction == "DESC",
                    )
        limit = int(re.search(r"LIMIT (\d+)", inner).group(1))  # type: ignore[union-attr]
        offset_match = re.search(r"OFFSET (\d+)", inner)
        offset = int(offset_match.group(1)) if offset_match else 0
        page = rows[offset : offset + limit]
        rendered = []
        for row in page:
            cells, flags = [], []
            for column in relation["columns"]:
                kind = _demo_kind(column)
                cell, cut = _demo_cell(row.get(column[0]), kind)
                cells.append(cell)
                if kind in ("text", "json", "array", "other"):
                    flags.append(cut)
            rendered.append(
                [cells, flags, [str(row.get(name)) for name in relation["primary_key"]]]
            )
        answer: dict[str, Any] = {"rows": rendered}
        if "'count', (SELECT count(*)" in statement:
            answer["count"] = len(rows)
        return json.dumps(answer)

    def edit(self, statement: str) -> str:
        """One row inserted, updated or deleted, as PostgresDialect._edit's script answers."""
        found = self._relation(statement)
        if found is None:
            return ""
        _, relation = found
        columns = {column[0]: column for column in relation["columns"]}

        def image(row: dict[str, Any] | None) -> Any:
            if row is None:
                return None
            return {
                name: (("\\x" + value.hex()) if isinstance(value, bytes) else value)
                for name, value in row.items()
            }

        def assigned(text: str) -> dict[str, Any]:
            values: dict[str, Any] = {}
            for name, literal in re.findall(r'"(\w+)" = (NULL|E?\'(?:[^\']|\'\')*\')', text):
                values[name] = (
                    None
                    if literal == "NULL"
                    else _demo_typed(_demo_literal(literal), columns[name])
                    if name in columns
                    else _demo_literal(literal)
                )
            return values

        with self.lock:
            rows: list[dict[str, Any]] = relation["rows"]
            update = re.search(r"AS t SET (.+?) WHERE (.+?) RETURNING", statement, re.DOTALL)
            if update:
                key = assigned(update.group(2))
                for row in rows:
                    if all(str(row.get(name)) == str(value) for name, value in key.items()):
                        before = dict(row)
                        row.update(assigned(update.group(1)))
                        return json.dumps({"before": image(before), "after": image(row)})
                return json.dumps({"before": None, "after": None})
            delete = re.search(r"DELETE FROM .+? AS t WHERE (.+?) RETURNING", statement, re.DOTALL)
            if delete:
                key = assigned(delete.group(1))
                for index, row in enumerate(rows):
                    if all(str(row.get(name)) == str(value) for name, value in key.items()):
                        rows.pop(index)
                        return json.dumps({"before": image(row), "after": None})
                return json.dumps({"before": None, "after": None})
            insert = re.search(r"AS t \((.+?)\) VALUES \((.+?)\) RETURNING", statement, re.DOTALL)
            new: dict[str, Any] = dict.fromkeys(columns)
            if insert:
                names = re.findall(r'"(\w+)"', insert.group(1))
                literals = re.findall(r"NULL|E?'(?:[^']|'')*'", insert.group(2))
                for name, literal in zip(names, literals, strict=False):
                    new[name] = (
                        None
                        if literal == "NULL"
                        else _demo_typed(_demo_literal(literal), columns[name])
                    )
            for name in relation["primary_key"]:
                if new.get(name) is None and _demo_kind(columns[name]) == "numeric":
                    new[name] = max((int(row.get(name) or 0) for row in rows), default=0) + 1
            if "created_at" in columns and new.get("created_at") is None:
                new["created_at"] = datetime.now(timezone.utc).isoformat()
            rows.append(new)
            return json.dumps({"before": None, "after": image(new)})

    def database_metrics(self, mine: bool, *, size_bytes: int | None = None) -> str:
        """A database's statistics, as the metrics reader's per-database query answers."""
        if not mine:
            return json.dumps(
                {
                    "size_bytes": size_bytes or 9_120_563,
                    "server_connections": 9,
                    "max_connections": 100,
                    "stat": {
                        "connections": 1,
                        "xact": 45_012,
                        "blks_hit": 1_203_111,
                        "blks_read": 9_871,
                        "deadlocks": 0,
                    },
                    "tables": [],
                }
            )
        tables = sorted(
            (
                {
                    "schema": schema,
                    "name": name,
                    "size_bytes": relation["size"],
                    "rows_estimate": len(relation["rows"]),
                }
                for (schema, name), relation in self.relations.items()
                if relation["kind"] == "table"
            ),
            key=lambda table: -(table["size_bytes"] or 0),
        )
        return json.dumps(
            {
                "size_bytes": 48_218_931,
                "server_connections": 9,
                "max_connections": 100,
                "stat": {
                    "connections": 6,
                    "xact": 1_829_384,
                    "blks_hit": 77_123_450,
                    "blks_read": 98_765,
                    "deadlocks": 2,
                },
                "tables": tables,
            }
        )


#: What pg_dump writes for the model: a custom-format archive's signature and some bytes.
_PG_DUMP_BYTES = "PGDMP\x01\x0e\x00\x04\x08\x01\x01\x01" + "x" * 4096

#: What pg_restore --list prints of it.
_PG_RESTORE_LIST = (
    ";\n; Archive created at 2026-09-29 02:00:04 UTC\n;     dbname: example_production\n;\n"
    "215; 1259 16390 TABLE public customers app_owner\n"
    "216; 1259 16402 TABLE public orders app_owner\n"
    "217; 1259 16420 TABLE public order_items app_owner\n"
    "218; 1259 16431 TABLE public audit_events app_owner\n"
    "219; 1259 16440 VIEW public paid_orders app_owner\n"
    "220; 1259 16450 TABLE analytics daily_revenue app_owner\n"
    "3310; 0 16390 TABLE DATA public customers app_owner\n"
    "3311; 0 16402 TABLE DATA public orders app_owner\n"
)


def _psql_database(args: Sequence[str]) -> str | None:
    """The database a psql invocation is pointed at (``-d NAME`` or ``--dbname=NAME``)."""
    for index, arg in enumerate(args):
        if arg == "-d" and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--dbname="):
            return arg.split("=", 1)[1]
    return None
