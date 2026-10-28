# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the modelled engines answer to the Databases page: their databases, users and rows."""

from __future__ import annotations

import re
from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Console: the Databases page needs more than `list_databases` out of the
# fake SQL clients - the detail page (owner, size, table count) and the
# users list run their own queries, and the SQL console needs a query that
# answers with rows instead of nothing. Each engine manager's exact SQL text
# is matched by a fragment unique to that query, so the generic listing
# queries below (kept as they were) still answer everything else.
# ---------------------------------------------------------------------------

#: PostgreSQL databases the demo machine knows about: owner and size in bytes,
#: matching what `example_production` and `example_staging` list as.
_PG_DATABASES: dict[str, tuple[str, int]] = {
    "postgres": ("postgres", 7553827),
    "example_production": ("app_owner", 48218931),
    "example_staging": ("app_owner", 9120563),
}

#: PostgreSQL roles `list_users` reports: name, superuser, createdb, createrole,
#: and (5th column) the databases it may connect to, comma-separated - the same
#: shape `PostgresManager.list_users`'s combined query now answers in one row.
_PG_USERS = (
    "postgres|t|t|t|postgres,example_production,example_staging\n"
    "app_owner|f|f|f|example_production,example_staging\n"
    "app_readonly|f|f|f|example_production,example_staging\n"
)

#: A read the SQL console can run against any PostgreSQL database, pipe-separated
#: the way `psql -t -A` prints it (no header row: see databases.py's docstring).
_PG_DEMO_ROWS = (
    "1024|maria@example.com|129.90\n1025|jon@example.com|54.00\n1026|priya@example.com|312.40\n"
)

#: The same read, in the structured console's shape: a header row, comma
#: separated, the way `psql --csv` prints it (see execute_query_structured).
_PG_DEMO_ROWS_CSV = (
    "id,email,total\n1024,maria@example.com,129.90\n1025,jon@example.com,54.00\n"
    "1026,priya@example.com,312.40\n"
)

#: MySQL/MariaDB schemas the demo machine knows about: size in bytes, table count.
_MYSQL_DATABASES: dict[str, tuple[int, int]] = {
    "shop_wp": (52_428_800, 18),
    "example_shop": (23_068_672, 11),
}

#: MySQL users `list_users` reports: name, host and (3rd column) the databases
#: it has database-level grants on, comma-separated - the same shape
#: `MySQLManager.list_users`'s combined query now answers in one row.
_MYSQL_USERS = "root\tlocalhost\t\nshop\t%\tshop_wp\nexample\t%\texample_shop\n"

#: The same read as `_PG_DEMO_ROWS`, tab-separated the way `mysql -N -B` prints it.
_MYSQL_DEMO_ROWS = "1024\tmaria@example.com\t129.90\n1025\tjon@example.com\t54.00\n1026\tpriya@example.com\t312.40\n"

#: The same read, in the structured console's shape: a header row, tab
#: separated, the way `mysql -B` (without -N) prints it.
_MYSQL_DEMO_ROWS_HEADERS = (
    "id\temail\ttotal\n1024\tmaria@example.com\t129.90\n1025\tjon@example.com\t54.00\n"
    "1026\tpriya@example.com\t312.40\n"
)

#: Matches the literal a `WHERE datname = '...'` or `WHERE SCHEMA_NAME = '...'`
#: clause quotes, so a canned answer can be specific to the database it was
#: asked about instead of always answering as the first one in the list.
_SQL_LITERAL = re.compile(r"=\s*'([^']*)'")


def _psql_console_sql(args: Sequence[str]) -> str:
    """
    Recover the SQL a psql invocation carries in its ``-c`` options.

    The console statement reaches psql as ``-c`` strings rather than on stdin
    (see PostgresManager._console_argv), so the model reads it from argv.

    Args:
        args: The psql argv.

    Returns:
        The ``-c`` strings joined by newlines, or "" when there are none.
    """
    return "\n".join(args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "-c")


def _sql_literal(statement: str) -> str | None:
    """
    Args:
        statement: SQL text containing a single-quoted literal.

    Returns:
        The literal's contents, or None when the statement has none.
    """
    match = _SQL_LITERAL.search(statement)
    return match.group(1) if match else None


def _sql_identifier(statement: str, quote: str, *, after: str | None = None) -> str | None:
    """
    Extracts a quoted identifier from a CREATE/DROP DATABASE statement.

    Args:
        statement: SQL text.
        quote: The quote character the engine uses for identifiers: `"` for
            PostgreSQL (`_escape_identifier`), `` ` `` for MySQL/MariaDB.
        after: When given, only matches the identifier immediately following
            this keyword (`"OWNER"`), for the optional owner clause; the
            first quoted identifier in the statement otherwise.

    Returns:
        The identifier, with its doubled escape quote un-escaped, or None
        when the statement holds no such identifier.
    """
    lead = rf"{re.escape(after)}\s+" if after else ""
    q = re.escape(quote)
    match = re.search(rf"{lead}{q}((?:[^{q}]|{q}{q})*){q}", statement)
    if not match:
        return None
    return match.group(1).replace(quote + quote, quote)
