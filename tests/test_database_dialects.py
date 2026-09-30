# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The SQL the data explorer and the row editor build (noust.managers.database.dialects).

What is pinned: identifiers are quoted with the engine's own quoting and only
ever come from the catalog; a value an operator types becomes a literal that
cannot close itself whatever the server's settings (PostgreSQL's E'' rule,
MySQL's hexadecimal literal); operators are an enumeration; pages are
bounded; keyset pagination applies exactly when the sort is the primary key;
and an edit is a single transaction that cannot change more than one row.
"""

from __future__ import annotations

import json

import pytest

from noust.core.exceptions import DatabaseNotFoundError, ValidationError
from noust.managers.database.dialects import (
    CELL_TEXT_LIMIT,
    EDIT_GUARD,
    MAX_OFFSET,
    MAX_PAGE_SIZE,
    MYSQL_EDIT_GUARD,
    Column,
    Filter,
    MySQLDialect,
    Order,
    PageQuery,
    PostgresDialect,
    Relation,
    RelationDetail,
    decode_cursor,
    dialect_for,
    encode_cursor,
    load_json,
)

PG = PostgresDialect()
MY = MySQLDialect()


def orders_table(**overrides) -> RelationDetail:
    """
    Args:
        **overrides: Fields to change.

    Returns:
        A table with an integer primary key, a text, a JSON and a binary column.
    """
    values = {
        "schema": "public",
        "name": "orders",
        "kind": "table",
        "columns": (
            Column("id", "integer", "numeric", nullable=False, primary_key=1, generated=True),
            Column("note", "text", "text"),
            Column("meta", "jsonb", "json"),
            Column("blob", "bytea", "binary"),
        ),
        "primary_key": ("id",),
    }
    values.update(overrides)
    return RelationDetail(**values)


# ============================================================ literals


def test_a_postgres_literal_doubles_quotes() -> None:
    assert PG.literal("it's") == "'it''s'"


def test_a_postgres_literal_with_a_backslash_is_an_escape_string() -> None:
    """quote_literal's rule: the value reads the same with standard_conforming_strings off."""
    assert PG.literal("a\\'; DROP TABLE t; --") == "E'a\\\\''; DROP TABLE t; --'"


def test_a_mysql_literal_is_hexadecimal_whatever_the_value() -> None:
    literal = MY.literal("x' OR '1'='1")

    assert literal == "_utf8mb4 X'" + b"x' OR '1'='1".hex() + "'"
    assert "'1'" not in literal


def test_a_nul_byte_is_refused() -> None:
    with pytest.raises(ValidationError, match="NUL"):
        PG.literal("a\x00b")


def test_identifiers_are_quoted_with_embedded_quotes_doubled() -> None:
    assert PG.ident('we"ird') == '"we""ird"'
    assert MY.ident("we`ird") == "`we``ird`"


# ============================================================ filters and sorts


def test_a_filter_parses_column_operator_and_value() -> None:
    assert Filter.parse("status:eq:paid") == Filter("status", "eq", value="paid")
    assert Filter.parse("note:like:a:b") == Filter("note", "like", value="a:b")
    assert Filter.parse("id:in:1,2,3") == Filter("id", "in", values=("1", "2", "3"))
    assert Filter.parse("deleted_at:null") == Filter("deleted_at", "null")


@pytest.mark.parametrize("text", ["status", "status:drop:x", "status:eq", ":eq:1"])
def test_a_malformed_filter_is_refused(text: str) -> None:
    with pytest.raises(ValidationError):
        Filter.parse(text)


def test_an_order_parses_its_direction() -> None:
    assert Order.parse("id") == Order("id")
    assert Order.parse("id:desc") == Order("id", descending=True)
    assert Order.parse("a:b") == Order("a:b")


def test_a_filter_on_a_column_the_catalog_lacks_is_refused() -> None:
    page = PageQuery(filters=(Filter("password", "eq", value="x"),))

    with pytest.raises(ValidationError, match="no column 'password'"):
        PG.check_page(orders_table(), page)


def test_a_sort_on_a_column_the_catalog_lacks_is_refused() -> None:
    with pytest.raises(ValidationError):
        PG.check_page(orders_table(), PageQuery(order=(Order("1; DROP TABLE t"),)))


@pytest.mark.parametrize(
    "page",
    [
        PageQuery(limit=0),
        PageQuery(limit=MAX_PAGE_SIZE + 1),
        PageQuery(offset=MAX_OFFSET + 1),
        PageQuery(offset=-1),
    ],
)
def test_pages_are_bounded(page: PageQuery) -> None:
    with pytest.raises(ValidationError):
        PG.check_page(orders_table(), page)


def test_a_cursor_needs_the_primary_key_sort() -> None:
    page = PageQuery(order=(Order("note"),), after=("5",))

    with pytest.raises(ValidationError, match="cursor"):
        PG.check_page(orders_table(), page)


def test_conditions_quote_the_column_and_the_value() -> None:
    detail = orders_table()

    assert PG.condition(detail.column("note"), Filter("note", "eq", value="a'b")) == (
        "\"note\" = 'a''b'"
    )
    assert PG.condition(detail.column("note"), Filter("note", "ilike", value="%x%")) == (
        "\"note\"::text ILIKE '%x%'"
    )
    assert PG.condition(detail.column("id"), Filter("id", "in", values=("1", "2"))) == (
        "\"id\" IN ('1', '2')"
    )
    assert PG.condition(detail.column("note"), Filter("note", "notnull")) == ('"note" IS NOT NULL')


# ============================================================ cursors


def test_a_cursor_round_trips() -> None:
    token = encode_cursor(["42", "a/b"])

    assert decode_cursor(token, 2) == ("42", "a/b")


@pytest.mark.parametrize("token", ["!!!", encode_cursor(["1"]), "e30"])
def test_a_foreign_cursor_is_refused(token: str) -> None:
    with pytest.raises(ValidationError, match="cursor"):
        decode_cursor(token, 2)


# ============================================================ rows


def test_the_default_page_is_keyset_on_the_primary_key() -> None:
    detail = orders_table()
    page = PageQuery(limit=50)

    sql = PG.rows_sql(detail, page)

    assert PG.keyset(detail, page)
    assert 'ORDER BY "id" ASC LIMIT 51)' in sql
    assert "OFFSET" not in sql


def test_a_keyset_page_starts_after_the_cursor() -> None:
    sql = PG.rows_sql(orders_table(), PageQuery(after=("42",)))

    assert "WHERE (\"id\") > ('42')" in sql


def test_a_descending_keyset_page_starts_before_the_cursor() -> None:
    page = PageQuery(order=(Order("id", descending=True),), after=("42",))

    assert "(\"id\") < ('42')" in PG.rows_sql(orders_table(), page)


def test_a_sort_on_another_column_pages_by_offset_and_breaks_ties_on_the_key() -> None:
    page = PageQuery(order=(Order("note", descending=True),), offset=100, limit=10)

    sql = PG.rows_sql(orders_table(), page)

    assert 'ORDER BY "note" DESC, "id" ASC LIMIT 11 OFFSET 100' in sql


def test_a_table_without_a_primary_key_pages_by_offset() -> None:
    detail = orders_table(primary_key=())

    assert not PG.keyset(detail, PageQuery())
    assert "OFFSET 0" in PG.rows_sql(detail, PageQuery())


def test_values_are_cut_and_binary_values_summarised_by_the_server() -> None:
    sql = PG.rows_sql(orders_table(), PageQuery())

    assert f'left(t."note"::text, {CELL_TEXT_LIMIT})' in sql
    assert 'octet_length(t."blob")' in sql
    assert "encode(substring(t.\"blob\" FROM 1 FOR 32), 'hex')" in sql
    assert 'to_json(t."id")' in sql


def test_an_exact_count_counts_the_filtered_rows_only() -> None:
    page = PageQuery(filters=(Filter("note", "eq", value="x"),), after=("1",), count=True)

    sql = PG.rows_sql(orders_table(), page)

    assert '\'count\', (SELECT count(*) FROM "public"."orders" WHERE "note" = \'x\')' in sql


def test_a_postgres_page_is_read_with_null_distinct_from_empty() -> None:
    detail = orders_table()
    output = json.dumps(
        {
            "rows": [
                [[1, None, "{}", None], [False, False, False], ["1"]],
                [[2, "", "[1]", {"bytes": 3, "hex": "010203"}], [True, False, False], ["2"]],
                [[3, "x", None, None], [False, False, False], ["3"]],
            ]
        }
    )

    page = PG.parse_rows(output, detail, PageQuery(limit=2))

    assert page.rows == [[1, None, "{}", None], [2, "", "[1]", {"bytes": 3, "hex": "010203"}]]
    assert page.truncated == [[], [1]]
    assert page.keys == [{"id": "1"}, {"id": "2"}]
    assert page.has_more
    assert decode_cursor(page.next_cursor or "", 1) == ("2",)
    assert page.pagination == "keyset"


def test_an_offset_page_says_where_the_next_one_starts() -> None:
    detail = orders_table(primary_key=())
    output = json.dumps({"rows": [[[1], [], []], [[2], [], []]]})

    page = PG.parse_rows(output, detail, PageQuery(limit=1, offset=10))

    assert page.next_offset == 11
    assert page.next_cursor is None


def test_numbers_stay_exact() -> None:
    parsed = load_json("[9007199254740993, 12.5, 0.1000000000000000055511151231257827]", what="x")

    assert parsed[0] == "9007199254740993"
    assert parsed[1] == 12.5
    assert isinstance(parsed[2], str)


def test_a_mysql_page_reads_one_json_row_per_line_and_the_count() -> None:
    detail = orders_table(schema="shop")
    output = '["count", 7]\n[[1, "a", "{}", null], [0, 0], ["1"]]\n'

    page = MY.parse_rows(output, detail, PageQuery(count=True))

    assert page.count == 7
    assert page.rows == [[1, "a", "{}", None]]
    assert not page.has_more


def test_a_mysql_page_matches_values_with_hexadecimal_literals() -> None:
    detail = orders_table(schema="shop")
    page = PageQuery(filters=(Filter("note", "eq", value="it's"),), count=True)

    sql = MY.rows_sql(detail, page)

    assert "`note` = _utf8mb4 X'69742773'" in sql
    assert sql.startswith("SELECT JSON_ARRAY('count', COUNT(*)) FROM `shop`.`orders` WHERE")
    assert "LIMIT 101;" in sql


# ============================================================ catalog and structure


def test_the_postgres_catalog_skips_system_schemas_without_a_backslash() -> None:
    sql = PG.catalog_sql("shop")

    assert "left(n.nspname, 3) <> 'pg_'" in sql
    assert "\\" not in sql


def test_the_postgres_catalog_is_parsed() -> None:
    output = json.dumps(
        {
            "schemas": ["public"],
            "relations": [
                {
                    "schema": "public",
                    "name": "orders",
                    "kind": "table",
                    "rows_estimate": 10,
                    "size_bytes": 8192,
                }
            ],
        }
    )

    catalog = PG.parse_catalog(output)

    assert catalog.schemas == ("public",)
    assert catalog.find("public", "orders") == Relation("public", "orders", "table", 10, 8192)
    assert catalog.find("public", "ORDERS") is None


def test_the_postgres_structure_is_parsed() -> None:
    output = json.dumps(
        {
            "columns": [
                {
                    "name": "id",
                    "type": "integer",
                    "base": "int4",
                    "category": "N",
                    "nullable": False,
                    "default": "nextval('orders_id_seq'::regclass)",
                    "generated": False,
                },
                {"name": "data", "type": "bytea", "base": "bytea", "category": "U"},
                {"name": "doc", "type": "jsonb", "base": "jsonb", "category": "U"},
            ],
            "primary_key": ["id"],
            "indexes": [
                {
                    "name": "orders_pkey",
                    "unique": True,
                    "primary": True,
                    "definition": "CREATE UNIQUE INDEX ...",
                    "columns": ["id"],
                }
            ],
            "constraints": [{"name": "orders_pkey", "type": "p", "definition": "PRIMARY KEY (id)"}],
        }
    )

    detail = PG.parse_describe(output, Relation("public", "orders", "table"))

    assert [(c.name, c.kind, c.primary_key) for c in detail.columns] == [
        ("id", "numeric", 1),
        ("data", "binary", None),
        ("doc", "json", None),
    ]
    assert detail.editable
    assert detail.indexes[0].columns == ("id",)
    assert detail.constraints[0].type == "primary_key"


def test_a_relation_the_engine_no_longer_describes_is_not_found() -> None:
    with pytest.raises(DatabaseNotFoundError):
        PG.parse_describe("", Relation("public", "gone", "table"))


def test_the_mysql_structure_is_parsed_from_tagged_lines() -> None:
    lines = [
        '["column", "id", "int(11)", "int", 0, null, "auto_increment", 1]',
        '["column", "email", "varchar(255)", "varchar", 1, null, "", 2]',
        '["index", "PRIMARY", "id", 1, 1]',
        '["index", "email_idx", "email", 0, 1]',
        '["constraint", "PRIMARY", "PRIMARY KEY"]',
    ]

    detail = MY.parse_describe("\n".join(lines), Relation("shop", "users", "table"))

    assert detail.primary_key == ("id",)
    assert detail.column("id").generated
    assert detail.column("email").kind == "text"
    assert [index.name for index in detail.indexes] == ["PRIMARY", "email_idx"]


def test_mysql_describes_through_literals_not_names() -> None:
    sql = MY.describe_sql("shop", "us`ers")

    assert "`" not in sql.split("FROM", 1)[1].split("WHERE", 1)[1]
    assert "X'" in sql


# ============================================================ edits


def test_an_edit_needs_a_primary_key() -> None:
    with pytest.raises(ValidationError, match="no primary key"):
        PG.check_key(orders_table(primary_key=()), {"id": "1"})


def test_an_edit_needs_the_whole_primary_key() -> None:
    detail = orders_table(primary_key=("id", "note"))

    with pytest.raises(ValidationError, match="whole primary key"):
        PG.check_key(detail, {"id": "1"})


def test_an_edit_refuses_a_column_the_relation_lacks() -> None:
    with pytest.raises(ValidationError, match="no column"):
        PG.check_values(orders_table(), {"id = 1; --": 1}, allow_empty=False)


def test_a_postgres_update_is_guarded_to_one_row_in_one_transaction() -> None:
    detail = orders_table()

    script = PG.update_sql(detail, {"id": "7"}, {"note": "it's", "meta": {"a": 1}})

    assert script.startswith("BEGIN;\n")
    assert script.rstrip().endswith("COMMIT;")
    assert 'UPDATE "public"."orders" AS t SET "note" = \'it\'\'s\', "meta" = \'{"a": 1}\'' in script
    assert "WHERE \"id\" = '7'" in script
    assert EDIT_GUARD in script
    assert "<> 1 THEN" in script


def test_a_postgres_insert_and_delete_record_their_images() -> None:
    detail = orders_table()

    insert = PG.insert_sql(detail, {"note": None, "blob": {"hex": "00ff"}})
    delete = PG.delete_sql(detail, {"id": "3"})

    assert '("note", "blob") VALUES (NULL, E\'\\\\x00ff\')' in insert
    assert "SELECT NULL, changed.row FROM changed" in insert
    assert "SELECT changed.row, NULL FROM changed" in delete
    assert 'DELETE FROM "public"."orders" AS t WHERE "id" = \'3\'' in delete


def test_a_bad_binary_value_is_refused() -> None:
    with pytest.raises(ValidationError, match="hexadecimal"):
        PG.value(orders_table().column("blob"), {"hex": "zz"})


def test_a_mysql_update_guards_the_match_and_reads_both_images() -> None:
    detail = orders_table(schema="shop")

    script = MY.update_sql(detail, {"id": "7"}, {"note": "x"})

    assert script.startswith("START TRANSACTION;\n")
    assert MYSQL_EDIT_GUARD in script
    assert "SELECT JSON_ARRAY('before'" in script
    assert "FOR UPDATE;" in script
    assert (
        "UPDATE `shop`.`orders` SET `note` = _utf8mb4 X'78' WHERE `id` = _utf8mb4 X'37';" in script
    )
    assert script.rstrip().endswith("COMMIT;")


def test_a_mysql_insert_reads_back_an_auto_increment_key() -> None:
    script = MY.insert_sql(orders_table(schema="shop"), {"note": "x"})

    assert "`id` = LAST_INSERT_ID()" in script


def test_the_mysql_edit_images_are_parsed() -> None:
    output = '["before", {"id": 1, "note": "a"}]\n["after", {"id": 1, "note": "b"}]\n'

    assert MY.parse_change(output) == ({"id": 1, "note": "a"}, {"id": 1, "note": "b"})


def test_engines_without_tables_have_no_dialect() -> None:
    assert dialect_for("redis") is None
    assert isinstance(dialect_for("mysql", mariadb=True), MySQLDialect)
