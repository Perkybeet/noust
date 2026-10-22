# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a database backup policy covers: one database or all of an engine's, and what it leaves out.

Until 3.4 a policy named one database. Two things were missing that every
other tool offers: a policy that follows an engine's databases as they come
and go (so a database created next month is protected without anyone
remembering to schedule it), and a way to leave out what is not worth keeping
(a cache table, a log collection, a scratch schema).

A policy covers all databases when its ``db_name`` is :data:`ALL_DATABASES`.
That is not a database name any engine accepts, so it can never collide with a
real one, and a policy row, a timer and a CLI call all carry it unchanged.

Exclusions are plain names, validated where they will be spelled into a dump
command, and handed to the engine as separate arguments (never joined into a
string, never through a shell):

- ``databases``: only for an all-databases policy; skipped whole.
- ``schemas``: PostgreSQL schemas, ``pg_dump --exclude-schema``.
- ``tables``: PostgreSQL tables (``schema.table`` or ``table``,
  ``pg_dump --exclude-table``), MySQL and MariaDB tables
  (``mysqldump --ignore-table=<database>.<table>``) and MongoDB collections
  (``mongodump --excludeCollection``).

A dump that leaves something out restores a database without it: the policy
view says so, and so does ``noust db backups``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import ValidationError

__all__ = [
    "ALL_DATABASES",
    "SCOPE_ALL",
    "SCOPE_DATABASE",
    "Exclusions",
    "timer_label",
]

#: The ``db_name`` of a policy that covers every database of its engine.
ALL_DATABASES = "*"

#: A policy's scope: one database, or every database of the engine.
SCOPE_DATABASE = "database"
SCOPE_ALL = "all"

#: Most names one kind of exclusion may list: a policy is a short document.
MAX_EXCLUSIONS = 100

#: A database name, as every engine's own validator will check again.
_DATABASE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_$-]{0,127}")

#: A PostgreSQL schema.
_SCHEMA = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_$]{0,62}")

#: A table or a collection: letters, digits and ``_ $ . -``; ``*`` and ``?``
#: are pg_dump's own pattern characters. Nothing that could be read as an
#: option (a leading ``-``) or as a path.
_TABLE = re.compile(r"[A-Za-z0-9_*?][A-Za-z0-9_$.*?-]{0,127}")


def timer_label(database: str) -> str:
    """
    Spell a policy's database where a unit name is built.

    Args:
        database: A database name or :data:`ALL_DATABASES`.

    Returns:
        The name itself, or ``-all``: a leading ``-`` is not a character a
        database name starts with, so the unit of the all-databases policy
        (``noust-backup-db-postgresql--all``) can never be a database's.
    """
    return "-all" if database == ALL_DATABASES else database


def _names(value: Any, pattern: re.Pattern[str], field_name: str, what: str) -> tuple[str, ...]:
    """
    Check one list of exclusions.

    Args:
        value: What the caller gave: None, or a sequence of strings.
        pattern: What each name must match.
        field_name: The field, for the error.
        what: What the names are, for the error.

    Returns:
        The names, de-duplicated, in the order given.

    Raises:
        ValidationError: When it is not a list of plain names.
    """
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValidationError(f"{field_name} must be a list of {what}", field=field_name)
    if len(value) > MAX_EXCLUSIONS:
        raise ValidationError(
            f"{field_name} may name at most {MAX_EXCLUSIONS} {what}",
            details="Keep a policy short: back up a smaller part, or split it in two.",
            field=field_name,
        )
    seen: dict[str, None] = {}
    for item in value:
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise ValidationError(
                f"{item!r} is not a valid name for {field_name}",
                details=f"Use plain names of {what}: letters, digits and _ $ . - only.",
                field=field_name,
            )
        seen.setdefault(item, None)
    return tuple(seen)


@dataclass(frozen=True)
class Exclusions:
    """
    What a policy leaves out of its dumps.

    Attributes:
        databases: Databases an all-databases policy skips.
        schemas: PostgreSQL schemas left out of every dump.
        tables: Tables (PostgreSQL, MySQL, MariaDB) or collections (MongoDB)
            left out of every dump.
    """

    databases: tuple[str, ...] = ()
    schemas: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()

    @classmethod
    def of(
        cls,
        *,
        databases: Sequence[str] | None = None,
        schemas: Sequence[str] | None = None,
        tables: Sequence[str] | None = None,
    ) -> Exclusions:
        """
        Build exclusions from what an operator typed, checking every name.

        Args:
            databases: Databases to skip (all-databases policies).
            schemas: PostgreSQL schemas to leave out.
            tables: Tables or collections to leave out.

        Returns:
            The checked exclusions.

        Raises:
            ValidationError: When a name is not a plain one.
        """
        return cls(
            databases=_names(databases, _DATABASE, "exclude_databases", "databases"),
            schemas=_names(schemas, _SCHEMA, "exclude_schemas", "schemas"),
            tables=_names(tables, _TABLE, "exclude_tables", "tables or collections"),
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> Exclusions:
        """
        Read the object a policy row stores.

        Args:
            data: ``{"databases": [...], "schemas": [...], "tables": [...]}``,
                any key optional; None for none.

        Returns:
            The exclusions. Names that no longer validate are dropped rather
            than failing a timer that has to run.
        """
        data = data or {}

        def keep(key: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
            raw = data.get(key)
            if not isinstance(raw, list):
                return ()
            return tuple(item for item in raw if isinstance(item, str) and pattern.fullmatch(item))

        return cls(
            databases=keep("databases", _DATABASE),
            schemas=keep("schemas", _SCHEMA),
            tables=keep("tables", _TABLE),
        )

    def to_json(self) -> dict[str, list[str]]:
        """
        Returns:
            The object a policy row stores; keys with nothing in them are left out.
        """
        data = {
            "databases": list(self.databases),
            "schemas": list(self.schemas),
            "tables": list(self.tables),
        }
        return {key: value for key, value in data.items() if value}

    def __bool__(self) -> bool:
        """Whether anything is left out."""
        return bool(self.databases or self.schemas or self.tables)

    def per_dump(self) -> Exclusions | None:
        """
        What one database's dump leaves out: the policy's, minus the databases.

        Returns:
            The schemas and tables, or None when there are none (a dump that
            leaves nothing out takes the engine's plain path).
        """
        if not (self.schemas or self.tables):
            return None
        return Exclusions(schemas=self.schemas, tables=self.tables)

    def check_for(self, engine_type: str, *, scope: str) -> None:
        """
        Refuse what the engine cannot leave out.

        Args:
            engine_type: ``postgresql``, ``mysql``, ``mongodb`` or ``redis``.
            scope: :data:`SCOPE_DATABASE` or :data:`SCOPE_ALL`.

        Raises:
            ValidationError: When the engine has no such thing to leave out, or
                a database is excluded from a policy that covers one.
        """
        if self.databases and scope != SCOPE_ALL:
            raise ValidationError(
                "Databases can only be excluded from a policy that covers all of them",
                details="A policy for one database simply does not name the others.",
                field="exclude_databases",
            )
        if self.schemas and engine_type != "postgresql":
            raise ValidationError(
                "Only PostgreSQL has schemas to leave out",
                details="Leave tables or collections out with exclude_tables instead.",
                field="exclude_schemas",
            )
        if (self.tables or self.schemas) and engine_type == "clickhouse":
            raise ValidationError(
                "A ClickHouse backup cannot leave tables out yet",
                details="It is the whole database, taken by ClickHouse itself.",
                field="exclude_tables",
            )
        if (self.tables or self.schemas) and engine_type == "redis":
            raise ValidationError(
                "A Redis snapshot cannot leave anything out",
                details="It is the whole instance, taken by Redis itself.",
                field="exclude_tables",
            )
