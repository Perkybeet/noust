# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PostgreSQL extensions of a database: what the server offers, what is installed, add and remove (4.0, C-10).

PostGIS, pgvector, TimescaleDB and the rest of PostgreSQL's extensions are loaded into **one
database** with ``CREATE EXTENSION``. Whether one *can* be loaded depends on what the server has
on disk: an extension's files come from a package on the host (``postgresql-17-pgvector``) or
from the image of an instance, so the list this module reads is the server's own
(``pg_available_extensions``), never a catalogue of Noust's.

**Nothing is spelled from the caller's text.** The name asked for must be one of the names that
list returns, and it reaches the statement quoted as an identifier; a name the server does not
offer is refused with where an extension comes from, before any statement is sent.

**The statement runs as the cluster's superuser**, because most extensions that matter are not
*trusted* (PostgreSQL lets a database owner load only the ones whose control file says so). That
is the reason the route asks the caller to see the whole server for an extension that is not
trusted: loading one runs the extension's script with the superuser's rights, and a team that
owns a database does not own that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from noust.core.exceptions import DatabaseNotFoundError, ValidationError
from noust.managers.database.instances import engine_of
from noust.managers.database.service import DatabaseService

__all__ = ["ExtensionInfo", "ExtensionList", "Extensions"]

#: The databases that belong to PostgreSQL itself: an extension is not loaded into them.
SYSTEM_DATABASES = frozenset({"postgres", "template0", "template1"})

#: What an extension's name may look like before it is compared with the server's list.
_NAME = re.compile(r"[A-Za-z0-9_-]{1,63}")

#: Between the columns of a row: a control character no extension's comment holds.
SEPARATOR = "\x1f"

#: The query behind the list. ``trusted`` is a column from PostgreSQL 13.
_LIST_SQL = (
    "SELECT e.name || chr(31) || e.default_version || chr(31) || "
    "COALESCE(e.installed_version, '') || chr(31) || "
    "COALESCE(replace(replace(e.comment, E'\\n', ' '), E'\\r', ' '), '') || chr(31) || "
    "{trusted} || chr(31) || COALESCE(array_to_string(v.requires, ','), '') "
    "FROM pg_available_extensions e "
    "LEFT JOIN pg_available_extension_versions v "
    "ON v.name = e.name AND v.version = e.default_version "
    "ORDER BY e.name;"
)


@dataclass(frozen=True)
class ExtensionInfo:
    """
    One extension the server can load.

    Attributes:
        name: Its name, as ``CREATE EXTENSION`` takes it.
        default_version: The version a new load gets.
        installed_version: The version loaded in this database, or None.
        comment: What the extension's control file says it is.
        trusted: Whether a database owner may load it; otherwise only a superuser.
        requires: The extensions it needs loaded first.
    """

    name: str
    default_version: str
    installed_version: str | None = None
    comment: str = ""
    trusted: bool = False
    requires: tuple[str, ...] = ()

    @property
    def installed(self) -> bool:
        """Whether it is loaded in the database."""
        return self.installed_version is not None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "name": self.name,
            "default_version": self.default_version,
            "installed_version": self.installed_version,
            "installed": self.installed,
            "comment": self.comment,
            "trusted": self.trusted,
            "requires": list(self.requires),
        }


@dataclass
class ExtensionList:
    """
    The extensions a database could hold, and the ones it does.

    Attributes:
        engine: The engine key.
        database: The database.
        server_version: PostgreSQL's version, when it said.
        extensions: Every extension the server offers.
        source: Where more come from, for a person who wants one that is not here.
    """

    engine: str
    database: str
    server_version: str | None = None
    extensions: list[ExtensionInfo] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "engine": self.engine,
            "database": self.database,
            "server_version": self.server_version,
            "extensions": [item.to_dict() for item in self.extensions],
            "source": self.source,
        }


class Extensions:
    """
    Read, load and unload a database's PostgreSQL extensions.

    Args:
        service: The database service, whose engines run the statements.
    """

    def __init__(self, service: DatabaseService) -> None:
        self.service = service

    # --------------------------------------------------------------- questions

    def available(self, engine: str, database: str) -> ExtensionList:
        """
        List the extensions the server offers, marking those loaded in a database.

        Args:
            engine: The engine key (``postgresql``, ``postgresql@pg17``).
            database: The database.

        Returns:
            The list.

        Raises:
            ValidationError: The engine is not PostgreSQL.
            DatabaseNotFoundError: The database does not exist.
            DatabaseQueryError: The server refused the query.
        """
        manager = self._postgres(engine)
        self._require_database(manager, database)
        version_num = manager._server_version_num()
        trusted = "COALESCE(v.trusted, false)::text" if version_num >= 130000 else "'false'"
        output = manager.run_sql(database, _LIST_SQL.format(trusted=trusted), read_only=False)
        found = ExtensionList(
            engine=manager.ENGINE_NAME,
            database=database,
            server_version=manager.get_version(),
            source=self._source(manager),
        )
        for line in output.splitlines():
            parts = line.split(SEPARATOR)
            if len(parts) != 6 or not parts[0].strip():
                continue
            name, default, installed, comment, trusted_text, requires = parts
            found.extensions.append(
                ExtensionInfo(
                    name=name.strip(),
                    default_version=default.strip(),
                    installed_version=installed.strip() or None,
                    comment=comment.strip(),
                    trusted=trusted_text.strip() == "true",
                    requires=tuple(item for item in requires.strip().split(",") if item),
                )
            )
        return found

    def get(self, engine: str, database: str, name: str) -> ExtensionInfo:
        """
        Find one extension in the server's list.

        Args:
            engine: The engine key.
            database: The database.
            name: The extension.

        Returns:
            What the server says of it.

        Raises:
            ValidationError: Not the shape of an extension's name.
            ValidationError: The server does not offer it; the error says where one comes from.
        """
        wanted = self._name(name)
        listing = self.available(engine, database)
        for item in listing.extensions:
            if item.name == wanted:
                return item
        raise ValidationError(
            f"PostgreSQL has no extension called {wanted}",
            details=listing.source,
            field="name",
        )

    # ----------------------------------------------------------------- change

    def add(self, engine: str, database: str, name: str, *, cascade: bool = False) -> ExtensionInfo:
        """
        Load an extension into a database.

        Args:
            engine: The engine key.
            database: The database.
            name: The extension, one the server offers.
            cascade: Also load the extensions it needs.

        Returns:
            The extension as it is afterwards.

        Raises:
            ValidationError: Not the shape of an extension's name, or the database is PostgreSQL's own.
            ValidationError: The server does not offer it.
            DatabaseQueryError: The server refused the statement; its own message is the output.
        """
        info = self.get(engine, database, name)
        manager = self._postgres(engine)
        statement = (
            f"CREATE EXTENSION IF NOT EXISTS {manager._escape_identifier(info.name)}"
            f"{' CASCADE' if cascade else ''};"
        )
        manager.run_sql(database, statement, read_only=False)
        self.service.audit(
            "db.extension",
            f"{manager.ENGINE_NAME}/{database}",
            extension=info.name,
            action="add",
            version=info.default_version,
        )
        return self.get(engine, database, name)

    def remove(
        self, engine: str, database: str, name: str, *, cascade: bool = False
    ) -> ExtensionInfo:
        """
        Unload an extension from a database.

        Without ``cascade`` PostgreSQL refuses while anything depends on the extension (a
        column of one of its types, a function of its own), and says what.

        Args:
            engine: The engine key.
            database: The database.
            name: The extension.
            cascade: Also drop what depends on it.

        Returns:
            The extension as it is afterwards (no longer installed).

        Raises:
            ValidationError: Not the shape of an extension's name, or the database is PostgreSQL's own.
            ValidationError: The server does not offer it.
            DatabaseNotFoundError: It is not loaded in the database.
            DatabaseQueryError: The server refused; its message names what depends on it.
        """
        info = self.get(engine, database, name)
        if not info.installed:
            raise DatabaseNotFoundError(
                f"{info.name} is not loaded in {database}", details="There is nothing to remove."
            )
        manager = self._postgres(engine)
        statement = (
            f"DROP EXTENSION {manager._escape_identifier(info.name)}"
            f"{' CASCADE' if cascade else ''};"
        )
        manager.run_sql(database, statement, read_only=False)
        self.service.audit(
            "db.extension",
            f"{manager.ENGINE_NAME}/{database}",
            extension=info.name,
            action="remove",
            cascade=cascade,
        )
        return self.get(engine, database, name)

    # -------------------------------------------------------------- for a move

    @staticmethod
    def loaded_in(manager: Any, database: str) -> list[str]:
        """
        Name the extensions a database has loaded, besides PostgreSQL's own language.

        Args:
            manager: A running PostgreSQL's manager.
            database: The database.

        Returns:
            The names, sorted.

        Raises:
            DatabaseQueryError: The server refused the query.
        """
        output = manager.run_sql(
            database,
            "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY extname;",
            read_only=False,
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    @staticmethod
    def offered_by(manager: Any) -> set[str]:
        """
        Name the extensions a PostgreSQL could load.

        Args:
            manager: A running PostgreSQL's manager.

        Returns:
            The names the server's ``pg_available_extensions`` lists.

        Raises:
            DatabaseQueryError: The server refused the query.
        """
        output = manager.run_sql(
            "postgres", "SELECT name FROM pg_available_extensions;", read_only=False
        )
        return {line.strip() for line in output.splitlines() if line.strip()}

    # ----------------------------------------------------------------- helpers

    def _postgres(self, engine: str) -> Any:
        """
        Resolve a running PostgreSQL.

        Args:
            engine: The engine key.

        Returns:
            Its manager.

        Raises:
            ValidationError: It is another engine.
        """
        if engine_of(engine) != "postgresql":
            raise ValidationError(
                "Extensions are PostgreSQL's",
                details=f"{engine} is not PostgreSQL. MySQL, MariaDB, Redis and MongoDB load "
                "their plugins and modules some other way.",
                field="engine",
            )
        return self.service.running(engine)

    @staticmethod
    def _require_database(manager: Any, database: str) -> None:
        """
        Check the database exists and is not one of PostgreSQL's own.

        Args:
            manager: The engine.
            database: The database.

        Raises:
            ValidationError: PostgreSQL's own database.
            DatabaseNotFoundError: It does not exist.
        """
        if database in SYSTEM_DATABASES:
            raise ValidationError(
                f"{database} is one of PostgreSQL's own databases",
                details="Load extensions into the databases of your applications.",
                field="database",
            )
        if not manager.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")

    @staticmethod
    def _name(name: str) -> str:
        """
        Accept the shape of an extension's name.

        Args:
            name: What the caller gave.

        Returns:
            The name.

        Raises:
            ValidationError: It cannot be an extension's name.
        """
        if not isinstance(name, str) or _NAME.fullmatch(name) is None:
            raise ValidationError(
                f"{name!r} is not the name of an extension",
                details="Extension names are letters, digits, dashes and underscores: "
                "pgvector's is 'vector', PostGIS's is 'postgis'.",
                field="name",
            )
        return name

    @staticmethod
    def _source(manager: Any) -> str:
        """
        Say where an extension that is not in the list would come from.

        Args:
            manager: The engine.

        Returns:
            A sentence for the host's engine or for an instance.
        """
        if manager.instance is not None:
            return (
                "An instance offers the extensions of its image. Make the instance from an image "
                "that has the one you need ('noust db instance catalog' lists them)."
            )
        major = (manager.get_version() or "").split(".")[0]
        suffix = f"postgresql-{major}-<extension>" if major else "postgresql-<major>-<extension>"
        return (
            "The host offers the extensions its packages installed. Install the extension's "
            f"package first (apt install {suffix}, from the distribution or PGDG), then try again."
        )
