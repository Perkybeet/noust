# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Shared machinery for every database engine manager.

Service control, package install and removal, dump and restore plumbing,
privilege whitelisting and identifier quoting live here. A concrete backend only
declares its packages, its client binaries and the statements its engine speaks.

Three rules are enforced in this module and must not be relaxed by subclasses:

- **No shell.** Dumps reach disk through
  :meth:`~noust.core.runner.CommandRunner.capture_to_file`. The contents of a
  database can never be reinterpreted as shell syntax, and a binary dump is
  never round-tripped through a string.
- **No secrets in argv.** Passwords travel through stdin, an environment
  variable or a 0600 option file. Anything on a command line is visible in
  ``ps`` to every account on the machine.
- **No unvalidated SQL fragments.** Privileges come from a per-engine whitelist
  and identifiers are quoted with the engine's own mechanism.

A fourth rule came with 3.1: **a restore never destroys without a safety
copy.** :meth:`BaseDatabaseManager.restore` is the template every engine's
restore runs through; it dumps what the target holds before anything is
dropped or overwritten, and puts it back when the restore fails. An engine
only says how its dumps are checked and loaded.
"""

from __future__ import annotations

import csv
import io
import os
import re
import secrets
import string
import time
from abc import abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn, TypeVar

from noust.central import require_server_role
from noust.core import paths
from noust.core.exceptions import (
    DatabaseAccessError,
    DatabaseBackupError,
    DatabaseEngineError,
    DatabaseError,
    DatabaseQueryError,
    DatabaseUserError,
)
from noust.core.fs import SECRET_MODE
from noust.core.runner import CommandResult, CommandRunner, get_runner
from noust.deployers.helpers.permissions import hand_over_file
from noust.managers.base_manager import BaseManager
from noust.managers.database.eol import support_notice
from noust.managers.database.flavours import FLAVOURS, InstallPlan, install_repository
from noust.managers.database.instances import (
    container_secret_values,
    engine_of,
    mask_container_log,
    storage_name,
)
from noust.managers.database.urls import connection_url

if TYPE_CHECKING:
    from noust.managers.database.instances import DatabaseInstance

#: What an engine can do, as the console decides which tabs to draw. The
#: console reads these from ``GET /api/databases/engines`` instead of testing
#: engine names: ``sql`` a SQL console, ``tables`` a table browser, ``keys`` a
#: key browser, ``documents`` a collection browser, ``read_only`` a session
#: the server itself holds read-only, ``users`` accounts, ``profiles`` the
#: owner / read-write / read-only access profiles, ``dump`` dumps and
#: restores, ``metrics`` engine statistics, ``pitr`` point-in-time recovery.
CAPABILITY_NAMES = frozenset(
    {
        "sql",
        "tables",
        "keys",
        "documents",
        "read_only",
        "users",
        "profiles",
        "dump",
        "metrics",
        "pitr",
    }
)

#: Access profiles a user can be given on one database.
Profile = Literal["owner", "read_write", "read_only"]

#: Every profile, in order of decreasing privilege.
PROFILES: tuple[Profile, ...] = ("owner", "read_write", "read_only")

#: Prefix of the least-privilege account the read-only console signs in as.
#: A legacy name kept through 3.x: servers already hold roles called this.
READ_ONLY_ACCOUNT_PREFIX = "wasm_ro_"

#: Where apt is found. Engines are only installed through it; see install().
APT_GET = "apt-get"

#: Deadline for a query or any other short-lived client invocation.
QUERY_TIMEOUT = 120

#: Rows kept in a structured console result before it is reported truncated.
#: The SQL console renders these into a table; an unbounded result would turn
#: one accidental ``SELECT *`` into megabytes of JSON in a browser tab.
DEFAULT_STRUCTURED_ROW_CAP = 1000

#: The statement timeouts, in seconds, the console and the explorer offer.
#: The longest stays well under the 300 seconds a central's proxy gives a
#: request, so a slow statement is cancelled by the server, not cut by a proxy.
STATEMENT_TIMEOUTS: tuple[int, ...] = (5, 30, 120)


def query_deadline(timeout_s: int | None) -> int:
    """
    Give a client invocation its deadline when the server has one of its own.

    The server cancels the statement at ``timeout_s``; the process gets a
    margin beyond it for the connection and the answer, so what the operator
    sees is the server's own "canceling statement due to statement timeout",
    not a killed client.

    Args:
        timeout_s: The statement timeout, or None for none.

    Returns:
        Seconds the client process may run.
    """
    if timeout_s is None:
        return QUERY_TIMEOUT
    return max(QUERY_TIMEOUT, timeout_s + 15)


#: Deadline for a systemctl verb. Stopping a busy engine can take a while.
SERVICE_TIMEOUT = 120

#: Deadline for apt. Package downloads are slow and must not be cut short.
PACKAGE_TIMEOUT = 1800

#: Deadline for a dump, a restore or a decompression.
TRANSFER_TIMEOUT = 3600

#: apt must never stop to ask a question on a server.
APT_ENV: Mapping[str, str] = {"DEBIAN_FRONTEND": "noninteractive"}

#: Backups are readable only by root, and so is the directory holding them.
BACKUP_DIR_MODE = 0o750

#: Staging directory for restores: traversable so the engine account can open
#: the file it owns, unlistable so it cannot enumerate other backups.
STAGING_DIR_MODE = 0o711

# Every pattern below ends in \Z, never in $. In Python '$' also matches just
# before a final newline, so "shop\n" satisfies a '$'-anchored whitelist: the
# check would cover the first line and wave the rest through, which is the
# opposite of what an allowlist is for. \Z matches the end of the string and
# nothing else.

#: Database and user names accepted by Noust. Deliberately narrower than what the
#: engines accept: names come from HTTP requests and CLI arguments, and a name
#: that needs quoting to be safe is a name nobody wants to type.
NAME_PATTERN = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_$-]*\Z")

#: Privileges are keywords, optionally multi-word ("ALL PRIVILEGES"). Anything
#: with punctuation is an injection attempt, not a privilege.
PRIVILEGE_PATTERN = re.compile(r"\A[A-Z]+(?: [A-Z]+)*\Z")


def quote_identifier(value: str, quote: str) -> str:
    """
    Quote a SQL identifier with the engine's quoting character.

    Args:
        value: Raw identifier, such as a database or user name.
        quote: The engine's identifier quote character (a backtick for MySQL, a
            double quote for PostgreSQL).

    Returns:
        The identifier wrapped in the quote character, with every embedded
        occurrence of that character doubled.
    """
    return f"{quote}{value.replace(quote, quote * 2)}{quote}"


def validate_name(value: str, *, kind: str, engine: str, max_length: int) -> str:
    """
    Check that a database or user name is one Noust is willing to handle.

    Quoting alone would be enough for the SQL layer, but names also end up in
    file names, service names and connection strings, so they are constrained
    once, here.

    Args:
        value: Candidate name.
        kind: What the name designates, used in the error message.
        engine: Engine name, used in the error message.
        max_length: Longest name the engine accepts.

    Returns:
        The name, unchanged.

    Raises:
        DatabaseError: When the name is empty, too long or contains a character
            outside ``[A-Za-z0-9_$-]``, a trailing newline included.
    """
    if not isinstance(value, str) or not value:
        raise DatabaseError(
            f"Empty {engine} {kind} name",
            details=f"Provide a {kind} name of 1 to {max_length} characters.",
        )
    if len(value) > max_length:
        raise DatabaseError(
            f"{engine} {kind} name is too long: {len(value)} characters",
            details=f"{engine} accepts at most {max_length} characters for a {kind} name.",
        )
    if not NAME_PATTERN.match(value):
        raise DatabaseError(
            f"Invalid {engine} {kind} name: {value!r}",
            details=(
                f"A {kind} name must start with a letter, a digit or an underscore and may "
                "only contain letters, digits and the characters _ $ -."
            ),
        )
    return value


def validate_privileges(
    privileges: Sequence[str] | None,
    *,
    allowed: frozenset[str],
    engine: str,
    default: Sequence[str],
) -> tuple[str, ...]:
    """
    Reduce a caller-supplied privilege list to a whitelisted, normalised tuple.

    Privileges are the one part of a GRANT that cannot be quoted: they are SQL
    keywords. The only safe treatment is an exact-match whitelist, which is what
    this function is.

    Args:
        privileges: Privileges requested by the caller, or None for the default.
        allowed: The engine's whitelist, upper case.
        engine: Engine name, used in the error message.
        default: Privileges to use when the caller supplied none.

    Returns:
        Normalised privileges, upper case, in the order given, without repeats.

    Raises:
        DatabaseUserError: When any entry is not a plain whitelisted keyword.
    """
    requested = list(privileges) if privileges else list(default)

    seen: list[str] = []
    for raw in requested:
        if not isinstance(raw, str):
            raise DatabaseUserError(
                f"Invalid {engine} privilege: {raw!r}",
                details=f"Privileges must be strings. Allowed: {', '.join(sorted(allowed))}.",
            )
        candidate = " ".join(raw.split()).upper()
        if not PRIVILEGE_PATTERN.match(candidate) or candidate not in allowed:
            raise DatabaseUserError(
                f"Invalid {engine} privilege: {raw!r}",
                details=(
                    f"Allowed {engine} privileges: {', '.join(sorted(allowed))}. "
                    "Pass one privilege per list entry, without punctuation."
                ),
            )
        if candidate not in seen:
            seen.append(candidate)

    if not seen:
        raise DatabaseUserError(
            f"No {engine} privileges given",
            details=f"Allowed {engine} privileges: {', '.join(sorted(allowed))}.",
        )
    return tuple(seen)


#: What each engine's client prints when it refuses to sign Noust in: MySQL's
#: 1045, PostgreSQL's 28P01 and peer refusal, Redis's NOAUTH and WRONGPASS,
#: MongoDB's authentication and authorization failures.
_ACCESS_REFUSED = re.compile(
    r"ERROR 1045|ERROR 1698|28P01|password authentication failed|Peer authentication failed"
    r"|no password supplied|NOAUTH|WRONGPASS|Authentication failed|requires authentication"
    r"|not authorized on admin",
    re.IGNORECASE,
)


@dataclass
class DatabaseInfo:
    """Information about a database."""

    name: str
    engine: str
    size: str | None = None
    tables: int | None = None
    owner: str | None = None
    encoding: str | None = None
    created: datetime | None = None
    keys: int | None = None
    connection_string: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the database information as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return {
            "name": self.name,
            "engine": self.engine,
            "size": self.size,
            "tables": self.tables,
            "keys": self.keys,
            "owner": self.owner,
            "encoding": self.encoding,
            "created": self.created.isoformat() if self.created else None,
            "connection_string": self.connection_string,
            **self.extra,
        }


@dataclass
class UserInfo:
    """Information about a database user."""

    username: str
    engine: str
    host: str = "localhost"
    databases: list[str] = field(default_factory=list)
    privileges: list[str] = field(default_factory=list)
    created: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the user information as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return {
            "username": self.username,
            "engine": self.engine,
            "host": self.host,
            "databases": self.databases,
            "privileges": self.privileges,
            "created": self.created.isoformat() if self.created else None,
            **self.extra,
        }


def backup_format(path: Path) -> str:
    """
    Name a dump's format from its file name.

    Args:
        path: The dump.

    Returns:
        ``custom`` (``pg_dump -Fc``), ``plain`` (SQL text), ``tar``, ``rdb``,
        ``aof``, ``archive`` (a mongodump tarball) or ``unknown``.
    """
    name = path.name.removesuffix(".gz")
    for suffix, kind in (
        (".dump", "custom"),
        (".sql", "plain"),
        (".tar", "archive" if path.name.endswith(".tar.gz") else "tar"),
        (".rdb", "rdb"),
        (".aof", "aof"),
    ):
        if name.endswith(suffix):
            return kind
    return "unknown"


@dataclass
class BackupInfo:
    """Information about a database backup."""

    path: Path
    database: str
    engine: str
    size: int
    created: datetime
    compressed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Render the backup information as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return {
            "path": str(self.path),
            "name": self.path.name,
            "database": self.database,
            "engine": self.engine,
            "size": self.size,
            "size_human": format_size(self.size),
            "created": self.created.isoformat(),
            "compressed": self.compressed,
            "format": backup_format(self.path),
        }


@dataclass(frozen=True)
class RestoreOutcome:
    """
    What :meth:`BaseDatabaseManager.restore` did.

    Attributes:
        database: The database restored into.
        source: The dump that was loaded.
        safety_copy: The dump of what the database held before, or None when
            it did not exist (nothing to lose) or the caller waived the copy
            of a restore that overwrote nothing.
        replaced: Whether the database was dropped and recreated first.
    """

    database: str
    source: Path
    safety_copy: Path | None
    replaced: bool


@dataclass(frozen=True)
class AccessEntry:
    """
    One account's access to one database, as the engine reports it.

    Attributes:
        username: The account.
        host: Where it may connect from (MySQL); ``localhost`` elsewhere.
        profile: ``owner``, ``read_write``, ``read_only``, or ``custom`` for
            grants that match none of the three.
        privileges: The grants behind the profile, in the engine's words.
        internal: The account belongs to the engine or to Noust itself (the
            read-only console's ``wasm_ro_`` account, the superuser).
    """

    username: str
    host: str = "localhost"
    profile: str = "custom"
    privileges: tuple[str, ...] = ()
    internal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Render the entry as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return {
            "username": self.username,
            "host": self.host,
            "profile": self.profile,
            "privileges": list(self.privileges),
            "internal": self.internal,
        }


@dataclass(frozen=True)
class ListenAddress:
    """
    Where an engine's server says it listens.

    Attributes:
        setting: The engine's own name for the setting (``listen_addresses``,
            ``bind-address``, ``bind``, ``net.bindIp``).
        addresses: The addresses it holds, as configured.
        loopback_only: Whether every address is a loopback one.
    """

    setting: str
    addresses: tuple[str, ...]
    loopback_only: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Render the addresses as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return {
            "setting": self.setting,
            "addresses": list(self.addresses),
            "loopback_only": self.loopback_only,
        }


#: Addresses that only accept connections from this machine.
LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})


def is_loopback(address: str) -> bool:
    """
    Tell whether a listen address only accepts local connections.

    Args:
        address: An address as an engine or ``ss`` prints it.

    Returns:
        True for ``127.0.0.0/8``, ``::1`` and ``localhost``.
    """
    value = address.strip().strip("[]").lower()
    return value in LOOPBACK_ADDRESSES or value.startswith("127.")


def listen_address(setting: str, raw: str, *, separator: str | None = None) -> ListenAddress:
    """
    Build a :class:`ListenAddress` from a setting's raw value.

    Args:
        setting: The engine's name for the setting.
        raw: Its value, such as ``localhost`` or ``127.0.0.1 -::1``.
        separator: What separates addresses; whitespace when None.

    Returns:
        The parsed addresses. ``*``, ``0.0.0.0`` and ``::`` are kept as they
        are, and are not loopback.
    """
    parts = raw.split(separator) if separator else raw.split()
    # Redis prefixes an address with '-' to say "skip it when unavailable".
    addresses = tuple(part.strip().lstrip("-") for part in parts if part.strip())
    return ListenAddress(
        setting=setting,
        addresses=addresses,
        loopback_only=bool(addresses) and all(is_loopback(a) for a in addresses),
    )


def format_size(size: float) -> str:
    """
    Render a byte count in the largest unit that keeps it under 1024.

    Args:
        size: Number of bytes.

    Returns:
        A human readable size, such as ``"1.5 MB"``.
    """
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


@dataclass
class StructuredQueryResult:
    """
    Result of a console query, as :meth:`BaseDatabaseManager.execute_query_structured`
    hands it back.

    Attributes:
        output: The client's own output, verbatim - what the legacy plain-text
            console field has always shown.
        columns: Column names, in order. Empty when the engine has no tabular
            client output to parse (see the method's own docstring).
        rows: Data rows, each cell a string exactly as the client printed
            it - no type coercion - or None for a NULL where the client tells
            one from an empty string (psql does; MySQL's batch output prints
            ``NULL``, which is read as NULL).
        row_count: Number of rows in ``rows``, after any truncation.
        duration_ms: Wall-clock time the client invocation took.
        truncated: Whether rows beyond :data:`DEFAULT_STRUCTURED_ROW_CAP` (or
            the caller's own ``max_rows``) were dropped.
    """

    output: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[str | None]] = field(default_factory=list)
    row_count: int = 0
    duration_ms: float = 0.0
    truncated: bool = False


def parse_tabular_query_output(
    text: str, *, delimiter: str, max_rows: int = DEFAULT_STRUCTURED_ROW_CAP
) -> tuple[list[str], list[list[str]], bool]:
    """
    Parse a database client's header-plus-rows output into columns and rows.

    One implementation for every engine that can be asked to print its result
    with a header row and a fixed delimiter - PostgreSQL's ``psql --csv`` and
    MySQL's ``mysql --batch`` (with ``-N`` dropped) both qualify, with
    different delimiters, which is the only thing that differs between them.

    Args:
        text: The client's stdout: a header line followed by data lines.
        delimiter: Field separator the client used (``,`` for psql's CSV,
            ``\\t`` for mysql's batch mode).
        max_rows: Data rows kept before the rest are dropped.

    Returns:
        Column names, the (possibly truncated) rows, and whether rows were
        dropped to respect ``max_rows``. Empty column and row lists when the
        client printed nothing - a statement with no result set, such as a
        bare ``COMMIT``.
    """
    stripped = text.strip("\n")
    if not stripped:
        return [], [], False

    reader = csv.reader(io.StringIO(stripped), delimiter=delimiter)
    lines = list(reader)
    if not lines:
        return [], [], False

    columns, data = lines[0], lines[1:]
    truncated = len(data) > max_rows
    return columns, data[:max_rows], truncated


def console_statement(query: str, *, read_only: bool) -> str:
    """
    Check an operator's console text before any client sees it.

    The engine-neutral half of the console guard; each SQL manager adds the
    rule for its own client's command syntax. Read mode is held to one
    statement here, at the manager, and not only by the API and the CLI: a
    second statement can close the read-only transaction
    (``SELECT 1; COMMIT; DELETE ...``). The least-privilege account the
    session signs in as would still refuse the write, but each limit is
    kept whole on its own, and a guard kept in the callers has as many holes
    as there are callers.

    Args:
        query: The statement as the operator typed it.
        read_only: Whether the statement is run in read mode.

    Returns:
        The statement. In read mode, stripped and without its one optional
        trailing semicolon, so a wrapper can add its own terminator.

    Raises:
        DatabaseQueryError: When the text is empty, holds a NUL byte, or, in
            read mode, holds more than one statement.
    """
    if not query.strip():
        raise DatabaseQueryError("Empty statement", details="Send the statement to run.")
    if "\x00" in query:
        raise DatabaseQueryError(
            "The statement contains a NUL byte",
            details="Remove the NUL character; no SQL statement needs one.",
        )
    if not read_only:
        return query

    statement = query.strip().removesuffix(";").rstrip()
    # A ';' inside a string literal is refused too. Telling the two apart
    # needs the engine's own lexer, and a false refusal costs the operator a
    # rewrite while a false acceptance costs the read-only guarantee.
    if ";" in statement:
        raise DatabaseQueryError(
            "Read mode runs one statement at a time",
            details="Remove the embedded ';' and send the statements one by one.",
        )
    return statement


_Manager = TypeVar("_Manager", bound="BaseDatabaseManager")


class BaseDatabaseManager(BaseManager):
    """
    Base class for database engine managers.

    Subclasses declare the engine's packages, binaries and dialect; the workflow
    around them is implemented once, here.
    """

    #: Engine identifier used in the registry, in file names and in the API.
    ENGINE_NAME: str = ""
    #: Human readable engine name, used in messages.
    DISPLAY_NAME: str = ""
    #: Port the engine listens on by default.
    DEFAULT_PORT: int = 0
    #: systemd unit that runs the engine.
    SERVICE_NAME: str = ""
    #: Packages installed by :meth:`install`.
    PACKAGE_NAMES: tuple[str, ...] = ()
    #: Binary whose presence means the engine's client is installed.
    CLIENT_BINARY: str = ""
    #: Command that prints the engine version.
    VERSION_ARGV: tuple[str, ...] = ()
    #: Pattern whose first group is the version inside that command's output.
    VERSION_PATTERN: str = r"(\d+\.\d+\.\d+)"
    #: Paths removed by ``uninstall(purge=True)``.
    PURGE_PATHS: tuple[str, ...] = ()
    #: Extension given to a backup file before any ``.gz``.
    BACKUP_SUFFIX: str = ".sql"
    #: Longest database name the engine accepts.
    MAX_DATABASE_NAME_LENGTH: int = 63
    #: Longest user name the engine accepts.
    MAX_USER_NAME_LENGTH: int = 63
    #: Privileges :meth:`grant_privileges` and :meth:`revoke_privileges` accept.
    VALID_PRIVILEGES: frozenset[str] = frozenset()
    #: Privileges used when the caller names none.
    DEFAULT_PRIVILEGES: tuple[str, ...] = ()
    #: Whether execute_query_structured() has been overridden with a real
    #: parser for this engine's client output, rather than the base fallback.
    SUPPORTS_STRUCTURED_QUERY: bool = False
    #: What the engine can do; see :data:`CAPABILITY_NAMES`.
    CAPABILITIES: frozenset[str] = frozenset()
    #: Units the engine may run as, preferred first. Empty means
    #: :attr:`SERVICE_NAME` alone; see :meth:`service_unit`.
    SERVICE_CANDIDATES: tuple[str, ...] = ()
    #: Family the end-of-life dates are published for. The engine name when
    #: empty; MariaDB and MySQL share a manager and not a lifecycle.
    EOL_FAMILY: str = ""
    #: Accounts that belong to the engine, which Noust never alters or drops.
    INTERNAL_USERS: frozenset[str] = frozenset()

    #: Where backups are written when the caller gives no path.
    BACKUP_DIR = paths.backup_dir() / "databases"

    #: The container this manager drives, or None for the host's engine.
    #: Set by :meth:`bind`; see :mod:`noust.managers.database.instances`.
    instance: DatabaseInstance | None = None

    # ==================== Instances ====================

    def bind(self: _Manager, instance: DatabaseInstance) -> _Manager:
        """
        Make this manager drive a database container instead of the host's engine.

        Nothing else changes: every statement, listing, dump and restore is
        the same code, and :meth:`_exec` runs it inside the container. The
        manager's :attr:`ENGINE_NAME` becomes the instance key, which is what
        the store, the audit trail and the file names use, so a container's
        databases, links and dumps never mix with the host's; code that needs
        the engine itself asks :attr:`engine_type`.

        Args:
            instance: The container, as discovery found it.

        Returns:
            This manager.
        """
        self.instance = instance
        self.ENGINE_NAME = instance.key
        self._bind_names(instance)
        return self

    def _bind_names(self, instance: DatabaseInstance) -> None:
        """
        Name the engine after what the container's image runs.

        Args:
            instance: The container.
        """

    @property
    def engine_type(self) -> str:
        """
        The engine this manager speaks (``postgresql``, ``mysql``, ``redis``, ``mongodb``).

        The same for the host's engine and for a container: what engine
        specific code compares, where :attr:`ENGINE_NAME` is the key.
        """
        return engine_of(type(self).ENGINE_NAME)

    def refuse_in_container(self, action: str) -> None:
        """
        Refuse what a container's image decides, not Noust.

        Args:
            action: What was asked, as a verb ("install").

        Raises:
            DatabaseEngineError: Always, when this manager drives a container.
        """
        if self.instance is None:
            return
        where = (
            f"the {self.instance.service} service of the Compose project {self.instance.project}"
            if self.instance.project
            else f"the container {self.instance.container}"
        )
        raise DatabaseEngineError(
            f"Noust does not {action} {self.DISPLAY_NAME} inside {self.instance.container}",
            details=(
                f"The engine of {where} is what its image ({self.instance.image}) makes it. "
                "Change the image or its settings in the project's compose file and recreate "
                "the container."
            ),
        )

    def _interactive(self, argv: Sequence[str], *, user: str | None = None) -> list[str]:
        """
        Open an interactive client where the engine is.

        Args:
            argv: The client command as it runs on the host.
            user: The account it runs as inside a container (``-u``).

        Returns:
            ``argv`` for the host's engine; for a container, the
            ``docker exec -it`` that runs it there, signed in the way every
            other command is.
        """
        if self.instance is None:
            return list(argv)
        return self.instance.exec_argv(argv, user=user, interactive=True)

    # ==================== Process execution ====================

    @property
    def runner(self) -> CommandRunner:
        """
        The process runner. Resolved per call so tests can swap it in.

        Every command an engine manager runs - a query, a dump, a restore, the
        package manager, even the check for the client binary - asks for the
        runner here, so this is where a hub refuses local databases: a
        central on a NAS has none, and a subclass cannot add a path around it.

        Raises:
            RoleError: When this Noust is a hub.
        """
        require_server_role("Databases")
        return get_runner()

    def _exec(
        self,
        argv: Sequence[str],
        *,
        input: str | None = None,
        stdin_path: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = QUERY_TIMEOUT,
        secrets: Sequence[str] = (),
        user: str | None = None,
    ) -> CommandResult:
        """
        Run a command through the audited runner.

        Args:
            argv: Program and arguments. Never a shell string.
            input: Data written to the process stdin. Statements carrying a
                password go here instead of into argv.
            stdin_path: A file given to the process as its stdin, for a dump
                the client must read as data rather than name in a command.
            env: Extra environment variables.
            timeout: Deadline in seconds.
            secrets: Values to keep out of the logs.
            user: Run as this account instead of root. This is how a client
                that only authenticates over its engine's local peer socket -
                PostgreSQL's ``postgres`` superuser - gets invoked, without
                ``sudo``: Noust already runs as root, so the runner wraps the
                command in ``runuser`` instead.

        A manager bound to a container (:meth:`bind`) runs the same command
        inside it: ``docker exec -i``, ``user`` as ``-u`` and every variable
        of ``env`` forwarded by name, never by value
        (:meth:`~noust.managers.database.instances.DatabaseInstance.exec_argv`).

        Returns:
            The command outcome.
        """
        if self.instance is not None:
            return self.runner.run(
                self.instance.exec_argv(argv, user=user, env=env),
                input=input,
                stdin_path=stdin_path,
                env=env,
                timeout=timeout,
                secrets=secrets,
            )
        return self.runner.run(
            argv,
            input=input,
            stdin_path=stdin_path,
            env=env,
            timeout=timeout,
            secrets=secrets,
            user=user,
        )

    # ==================== Passwords ====================

    @staticmethod
    def generate_password(length: int = 32) -> str:
        """
        Generate a secure random password that is safe inside a URL.

        Letters and digits only. The symbols this used to mix in (``@``,
        ``#``, ``%``, ``&``) are what a connection string splits on, and a
        password is generated to end up in one; 32 alphanumeric characters
        carry about 190 bits, more than the 24 characters with symbols did.

        Args:
            length: Password length, at least 3.

        Returns:
            A password containing at least one lower case letter, one upper
            case letter and one digit.
        """
        alphabet = string.ascii_letters + string.digits
        password = [
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.digits),
        ]
        password += [secrets.choice(alphabet) for _ in range(max(length, 3) - 3)]
        secrets.SystemRandom().shuffle(password)
        return "".join(password)

    # ==================== Validation ====================

    @classmethod
    def validate_database_name(cls, name: str) -> str:
        """
        Check a database name against the engine's rules.

        Args:
            name: Candidate database name.

        Returns:
            The name, unchanged.

        Raises:
            DatabaseError: When the name is not acceptable.
        """
        return validate_name(
            name,
            kind="database",
            engine=cls.DISPLAY_NAME,
            max_length=cls.MAX_DATABASE_NAME_LENGTH,
        )

    @classmethod
    def validate_user_name(cls, username: str) -> str:
        """
        Check a user name against the engine's rules.

        Args:
            username: Candidate user name.

        Returns:
            The name, unchanged.

        Raises:
            DatabaseError: When the name is not acceptable.
        """
        return validate_name(
            username,
            kind="user",
            engine=cls.DISPLAY_NAME,
            max_length=cls.MAX_USER_NAME_LENGTH,
        )

    @classmethod
    def validate_privileges(cls, privileges: Sequence[str] | None) -> tuple[str, ...]:
        """
        Reduce requested privileges to the engine's whitelist.

        Args:
            privileges: Privileges requested by the caller, or None.

        Returns:
            Normalised, whitelisted privileges.

        Raises:
            DatabaseUserError: When a privilege is not whitelisted.
        """
        return validate_privileges(
            privileges,
            allowed=cls.VALID_PRIVILEGES,
            engine=cls.DISPLAY_NAME,
            default=cls.DEFAULT_PRIVILEGES,
        )

    # ==================== Engine Management ====================

    def is_installed(self) -> bool:
        """
        Report whether the engine's client is installed.

        Returns:
            True when the client binary is on PATH; always for a container,
            whose image is the installation.
        """
        if self.instance is not None:
            return True
        return bool(self.CLIENT_BINARY) and self.runner.exists(self.CLIENT_BINARY)

    def server_port(self) -> int:
        """
        Return the TCP port this engine's server listens on.

        Engines that can ask their server override this; the rest answer the
        port they install with. Every place that shows or records a port asks
        here, so none of them assumes the default on its own.

        Returns:
            The port. For a container, the host port its engine is published
            on, or its own port when it is not published.
        """
        if self.instance is not None:
            return self.instance.port
        return self.DEFAULT_PORT

    def get_version(self) -> str | None:
        """
        Read the engine version from its own ``--version`` output.

        Returns:
            The version string, or None when the engine is absent or silent.
        """
        if not self.VERSION_ARGV:
            return None
        result = self._exec(list(self.VERSION_ARGV))
        if not result.success:
            return None
        match = re.search(self.VERSION_PATTERN, result.stdout)
        return match.group(1) if match else None

    def _package_sets(self) -> tuple[list[str], ...]:
        """
        Return the package sets to try, in order of preference.

        Returns:
            One list of package names per candidate flavour of the engine.
        """
        return (list(self.PACKAGE_NAMES),)

    def default_install_plan(self) -> InstallPlan:
        """
        Describe what an install that names no flavour and no version does.

        This is the install of 3.2 and before, kept for every caller that
        still asks for it (an API request with no body, a script): the
        package sets of :meth:`_package_sets`, from the distribution.

        Returns:
            The plan.
        """
        return InstallPlan(
            flavour=self.ENGINE_NAME,
            engine=self.ENGINE_NAME,
            version=None,
            source="distribution",
            package_sets=tuple(tuple(packages) for packages in self._package_sets()),
        )

    def installed_flavour(self) -> str | None:
        """
        Name the flavour of this engine that is installed.

        Engines with two flavours (MySQL and MariaDB, Redis and Valkey)
        override this; the rest have one, named after the engine.

        Returns:
            A key of :data:`~noust.managers.database.flavours.FLAVOURS`, or
            None when the engine is not installed.
        """
        return self.ENGINE_NAME if self.is_installed() else None

    def _pre_install(self) -> None:
        """Prepare the system before apt runs. Engines that need it override this."""

    def _post_install(self) -> None:
        """Harden the fresh installation. Engines that need it override this."""

    def _on_packages_installed(self, packages: Sequence[str]) -> None:
        """
        React to the package set that actually installed.

        Args:
            packages: The package names that installed successfully.
        """

    def install(self, plan: InstallPlan | None = None) -> None:
        """
        Install the engine, enable its unit and start it.

        Only through apt, the one package manager whose package names and
        post-install behaviour this module knows. On a dnf or zypper system
        the refusal comes first and says what to do, instead of an
        ``apt-get: command not found`` halfway through: Noust manages an
        engine installed with the distribution's own tool just the same.

        Args:
            plan: The flavour and version to install, as
                :func:`~noust.managers.database.flavours.plan_install` built
                it; :meth:`default_install_plan` when None.

        Raises:
            DatabaseEngineError: When apt is absent, the repository cannot be
                added, or apt or the unit fails; always for a container, whose
                image decides its engine.
            ValidationError: When the default plan cannot be had on this
                distribution (MongoDB on a release it does not publish for).
        """
        self.refuse_in_container("install")
        if not self.runner.exists(APT_GET):
            raise DatabaseEngineError(
                f"Noust installs {self.DISPLAY_NAME} with apt, which this system does not have",
                details=(
                    f"Install {self.DISPLAY_NAME} with your distribution's package manager "
                    "(dnf or zypper), enable and start its service, then run "
                    f"'noust db status {self.ENGINE_NAME}': Noust manages it from there."
                ),
            )
        chosen = plan if plan is not None else self.default_install_plan()
        what = self.DISPLAY_NAME
        if plan is not None and plan.flavour in FLAVOURS:
            what = f"{FLAVOURS[plan.flavour].display_name} {plan.version or ''}".strip()
        self.logger.info(f"Installing {what}...")

        self._pre_install()
        if chosen.repository is not None:
            install_repository(
                chosen.repository, runner=self.runner, fs=self.fs, log=self.logger.info
            )

        result = self._exec(["apt-get", "update"], env=APT_ENV, timeout=PACKAGE_TIMEOUT)
        if not result.success:
            raise DatabaseEngineError(
                "Failed to update the package list",
                details=result.stderr.strip() or "Check the apt sources in /etc/apt.",
            )

        failures: list[str] = []
        for packages in chosen.package_sets:
            result = self._exec(
                ["apt-get", "install", "-y", *packages],
                env=APT_ENV,
                timeout=PACKAGE_TIMEOUT,
            )
            if result.success:
                self._on_packages_installed(packages)
                break
            failures.append(f"{' '.join(packages)}: {result.stderr.strip()}")
        else:
            raise DatabaseEngineError(
                f"Failed to install {what}",
                details="\n".join(failures) or "apt-get install returned no output.",
            )

        self.enable()
        self.start()
        self._post_install()

    def uninstall(self, purge: bool = False) -> None:
        """
        Remove the engine's packages, and its data when purging.

        Args:
            purge: Also delete the data and configuration directories.

        Raises:
            DatabaseEngineError: When every package set fails to be removed;
                always for a container.
        """
        self.refuse_in_container("uninstall")
        self.logger.info(f"Uninstalling {self.DISPLAY_NAME}...")

        try:
            self.stop()
        except DatabaseEngineError as exc:
            self.logger.warning(f"Could not stop {self.DISPLAY_NAME} before removal: {exc}")

        action = "purge" if purge else "remove"
        failures: list[str] = []
        for packages in self._package_sets():
            result = self._exec(
                ["apt-get", action, "-y", *packages],
                env=APT_ENV,
                timeout=PACKAGE_TIMEOUT,
            )
            if not result.success:
                failures.append(f"{' '.join(packages)}: {result.stderr.strip()}")

        if len(failures) == len(self._package_sets()):
            raise DatabaseEngineError(
                f"Failed to remove {self.DISPLAY_NAME}",
                details="\n".join(failures) or f"Run: apt-get {action} -y manually.",
            )

        if purge:
            for path in self.PURGE_PATHS:
                self._exec(["rm", "-rf", path], timeout=SERVICE_TIMEOUT)

    def service_unit(self) -> str:
        """
        Name the systemd unit this engine runs as on this server.

        Engines whose unit is named differently by different packages (Redis
        is ``redis-server`` on Debian, ``redis`` on Fedora, and Valkey's
        ``valkey-server`` or ``valkey``) list the candidates in
        :attr:`SERVICE_CANDIDATES`; the first one systemd has loaded wins,
        and is remembered on the instance. When none is, the default stays:
        the engine is not installed yet, and that is the unit its package
        will bring.

        Returns:
            The unit name; for a container, the container's name.
        """
        if self.instance is not None:
            return self.instance.container
        if len(self.SERVICE_CANDIDATES) < 2 or getattr(self, "_unit_detected", False):
            return self.SERVICE_NAME
        for candidate in self.SERVICE_CANDIDATES:
            result = self._exec(
                ["systemctl", "show", "--property=LoadState", "--value", candidate],
                timeout=SERVICE_TIMEOUT,
            )
            if result.success and result.stdout.strip() == "loaded":
                self.SERVICE_NAME = candidate
                break
        self._unit_detected = True
        return self.SERVICE_NAME

    def _systemctl(self, action: str) -> CommandResult:
        """
        Apply a systemd verb to the engine's unit.

        Args:
            action: The systemctl verb.

        Returns:
            The command outcome.
        """
        return self._exec(["systemctl", action, self.service_unit()], timeout=SERVICE_TIMEOUT)

    def _service_action(self, action: str) -> None:
        """
        Apply a systemd verb and turn a failure into an actionable error.

        Args:
            action: The systemctl verb.

        Raises:
            DatabaseEngineError: When systemctl reports failure. For a
                container, ``start``, ``stop`` and ``restart`` are Docker's
                and the rest is refused.
        """
        if self.instance is not None:
            self._container_action(self.instance, action)
            return
        result = self._systemctl(action)
        if not result.success:
            raise DatabaseEngineError(
                f"Failed to {action} {self.DISPLAY_NAME}",
                details=(
                    f"{result.stderr.strip()}\n"
                    f"Inspect the unit with: journalctl -u {self.service_unit()} -n 50"
                ).strip(),
            )

    def _container_action(self, instance: DatabaseInstance, action: str) -> None:
        """
        Start, stop or restart the container this manager drives.

        Args:
            instance: The container.
            action: ``start``, ``stop`` or ``restart``; ``enable`` and
                ``disable`` are the container's restart policy, which its
                compose file decides.

        Raises:
            DatabaseEngineError: When Docker refuses, with its own words, or
                for any other action.
        """
        if action not in ("start", "stop", "restart"):
            self.refuse_in_container(action)
        result = self.runner.run(["docker", action, instance.container], timeout=SERVICE_TIMEOUT)
        if not result.success:
            raise DatabaseEngineError(
                f"Failed to {action} the container {instance.container}",
                details=(
                    "Docker's own message follows. Inspect the container with: "
                    f"docker logs --tail 50 {instance.container}"
                ),
                output=(result.stderr or result.stdout).strip(),
            )

    def container_logs(self, lines: int) -> str:
        """
        Read the last lines a container's engine wrote.

        A database container's log is not shown verbatim: MySQL and MariaDB
        print the root password they generated into it, and a logged
        statement may carry an account's. Every secret value of the
        container's environment, every generated password and every password
        in a statement is masked (:func:`mask_container_log`).

        Args:
            lines: How many lines.

        Returns:
            What ``docker logs`` printed, both streams, secrets masked.

        Raises:
            DatabaseEngineError: For the host's engine, whose log is its
                unit's journal, or when Docker refuses.
            DatabaseQueryError: When the container's environment cannot be
                read, so the log could not be masked.
        """
        if self.instance is None:
            raise DatabaseEngineError(
                f"{self.DISPLAY_NAME} runs on the host, not in a container",
                details=f"Read its journal with: journalctl -u {self.service_unit()}",
            )
        result = self.runner.run(
            ["docker", "logs", "--tail", str(int(lines)), self.instance.container],
            timeout=SERVICE_TIMEOUT,
        )
        if not result.success:
            raise DatabaseEngineError(
                f"Docker did not show the log of {self.instance.container}",
                details="Docker's own message follows.",
                output=(result.stderr or result.stdout).strip(),
            )
        known = container_secret_values(
            self.instance.container, self.runner, extra=(self.instance.command_password,)
        )
        # The engines log to stderr; docker logs keeps the two streams apart.
        text = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        return mask_container_log(text, known)

    def start(self) -> None:
        """
        Start the engine's service.

        Raises:
            DatabaseEngineError: When the unit fails to start.
        """
        self._service_action("start")

    def stop(self) -> None:
        """
        Stop the engine's service.

        Raises:
            DatabaseEngineError: When the unit fails to stop.
        """
        self._service_action("stop")

    def restart(self) -> None:
        """
        Restart the engine's service.

        Raises:
            DatabaseEngineError: When the unit fails to restart.
        """
        self._service_action("restart")

    def enable(self) -> None:
        """
        Enable the engine's service at boot.

        Raises:
            DatabaseEngineError: When the unit cannot be enabled.
        """
        self._service_action("enable")

    def disable(self) -> None:
        """
        Disable the engine's service at boot.

        Raises:
            DatabaseEngineError: When the unit cannot be disabled.
        """
        self._service_action("disable")

    def is_running(self) -> bool:
        """
        Report whether the engine's service is active.

        Returns:
            True when systemd reports the unit as active, or Docker the
            container as running.
        """
        if self.instance is not None:
            return self.instance.running
        return self._systemctl("is-active").output == "active"

    def get_status(self) -> dict[str, Any]:
        """
        Summarise the engine's state.

        Returns:
            A dictionary describing installation, version, service state,
            capabilities, upstream support and anything the operator must
            know about the installation (``warnings``).
        """
        installed = self.is_installed()
        running = self.is_running() if installed else False
        # A stopped container cannot be asked: its client lives inside it.
        asked = installed and (running or self.instance is None)
        version = self.get_version() if asked else None
        status: dict[str, Any] = {
            "engine": self.ENGINE_NAME,
            "display_name": self.DISPLAY_NAME,
            "installed": installed,
            "version": version,
            "running": running,
            "port": self.instance.port if self.instance is not None else self.DEFAULT_PORT,
            "service": self.service_unit(),
            "capabilities": sorted(self.CAPABILITIES),
            "support": self.support(version).to_dict() if installed else None,
            "warnings": self.warnings() if running else [],
            "kind": "host",
        }
        if self.instance is not None:
            status.update(
                kind="container",
                container=self.instance.container,
                project=self.instance.project,
                compose_service=self.instance.service,
                image=self.instance.image,
                app=self.instance.app,
                access=self.instance.access,
            )
        return status

    def support(self, version: str | None = None) -> Any:
        """
        Say where the installed version stands in its upstream support.

        Args:
            version: The version, when the caller already asked for it.

        Returns:
            A :class:`~noust.managers.database.eol.SupportNotice`.
        """
        return support_notice(
            self.EOL_FAMILY or self.engine_type,
            version if version is not None else self.get_version(),
        )

    def warnings(self) -> list[str]:
        """
        List what an operator must know about this installation.

        Returns:
            Sentences in English, empty when there is nothing to say. The
            base has nothing; MongoDB warns when authorization is off.
        """
        return []

    def access_hint(self) -> str:
        """
        Say how to give Noust a way in when the engine refuses it.

        Returns:
            The command that stores the administrative credentials; for a
            container, the variables of its environment Noust reads.
        """
        if self.instance is not None:
            sources = self.instance.launch(self.CLIENT_BINARY).candidates
            account = self.instance.admin_user
            where = f" ({', '.join(sources)}, or the file a *_FILE variable names)"
            return (
                f"Noust signs in to the container {self.instance.container}"
                + (f" as {account}" if account else "")
                + " with what its environment carries"
                + (where if sources else "")
                + ". Set it in the service's environment in the compose file and recreate "
                "the container."
            )
        return (
            f"Store the account Noust signs in with: 'noust db config --engine "
            f"{self.ENGINE_NAME} --user <user> --password', or from the console, on "
            "the engine's card."
        )

    def _listing_failed(self, what: str, output: str) -> NoReturn:
        """
        Raise the reason a listing could not be read, instead of an empty list.

        An empty answer would read as "this engine has nothing": the console
        showed a MySQL that refused root as a server with no databases and no
        users, and the store's tracked rows as gone.

        Args:
            what: What was being listed, in plural ("databases", "users").
            output: The client's own output, verbatim.

        Raises:
            DatabaseAccessError: When the engine refused to sign Noust in.
            DatabaseQueryError: For any other failure.
        """
        text = output.strip()
        if _ACCESS_REFUSED.search(text):
            raise DatabaseAccessError(
                f"{self.DISPLAY_NAME} does not let Noust sign in, so its {what} cannot be listed",
                details=self.access_hint(),
                output=text,
            )
        raise DatabaseQueryError(
            f"{self.DISPLAY_NAME} did not list its {what}",
            details=f"{self.DISPLAY_NAME}'s own message follows.",
            output=text,
        )

    # ==================== Database Management ====================

    @abstractmethod
    def create_database(
        self,
        name: str,
        owner: str | None = None,
        encoding: str | None = None,
        **kwargs,
    ) -> DatabaseInfo:
        """
        Create a new database.

        Args:
            name: Database name.
            owner: Owner user, when the engine has the concept.
            encoding: Character encoding.
            **kwargs: Engine-specific options.

        Returns:
            Information about the new database.

        Raises:
            DatabaseExistsError: When the database already exists.
            DatabaseError: When creation fails.
        """

    @abstractmethod
    def drop_database(self, name: str, force: bool = False) -> None:
        """
        Drop a database.

        Args:
            name: Database name.
            force: Drop even if the database is in use, and stay silent when it
                does not exist.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseError: When the drop fails.
        """

    @abstractmethod
    def database_exists(self, name: str) -> bool:
        """
        Report whether a database exists.

        Args:
            name: Database name.

        Returns:
            True when the database exists.
        """

    @abstractmethod
    def list_databases(self) -> list[DatabaseInfo]:
        """
        List the databases the engine holds.

        Returns:
            One entry per non-system database.
        """

    @abstractmethod
    def get_database_info(self, name: str) -> DatabaseInfo:
        """
        Describe one database.

        Args:
            name: Database name.

        Returns:
            Information about the database.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
        """

    # ==================== User Management ====================

    @abstractmethod
    def create_user(
        self,
        username: str,
        password: str | None = None,
        host: str = "localhost",
        **kwargs,
    ) -> tuple[UserInfo, str]:
        """
        Create a database user.

        Args:
            username: User name.
            password: Password. Generated when omitted.
            host: Host restriction, for engines that have one.
            **kwargs: Engine-specific options.

        Returns:
            The user and its password.

        Raises:
            DatabaseUserError: When creation fails.
        """

    @abstractmethod
    def drop_user(self, username: str, host: str = "localhost") -> None:
        """
        Drop a database user.

        Args:
            username: User name.
            host: Host restriction, for engines that have one.

        Raises:
            DatabaseUserError: When the drop fails.
        """

    @abstractmethod
    def user_exists(self, username: str, host: str = "localhost") -> bool:
        """
        Report whether a user exists.

        Args:
            username: User name.
            host: Host restriction, for engines that have one.

        Returns:
            True when the user exists.
        """

    @abstractmethod
    def list_users(self) -> list[UserInfo]:
        """
        List the engine's users.

        Returns:
            One entry per user.
        """

    @abstractmethod
    def grant_privileges(
        self,
        username: str,
        database: str,
        privileges: Sequence[str] | None = None,
        host: str = "localhost",
    ) -> None:
        """
        Grant privileges on a database to a user.

        Args:
            username: User name.
            database: Database name.
            privileges: Privileges to grant. Engine default when omitted.
            host: Host restriction, for engines that have one.

        Raises:
            DatabaseUserError: When a privilege is not whitelisted or the grant
                fails.
        """

    @abstractmethod
    def revoke_privileges(
        self,
        username: str,
        database: str,
        privileges: Sequence[str] | None = None,
        host: str = "localhost",
    ) -> None:
        """
        Revoke privileges on a database from a user.

        Args:
            username: User name.
            database: Database name.
            privileges: Privileges to revoke. Engine default when omitted.
            host: Host restriction, for engines that have one.

        Raises:
            DatabaseUserError: When a privilege is not whitelisted or the revoke
                fails.
        """

    def set_user_password(self, username: str, password: str, host: str = "localhost") -> None:
        """
        Give an existing account a new password.

        Every engine keeps the password off argv: PostgreSQL receives a
        SCRAM verifier on stdin, MySQL an ``ALTER USER`` on stdin, Redis a
        SHA-256 digest, MongoDB a script on stdin.

        Args:
            username: The account.
            password: Its new password.
            host: Host restriction, for engines that have one.

        Raises:
            DatabaseUserError: When the engine cannot change it, the account
                does not exist, or the engine refuses.
        """
        raise DatabaseUserError(
            f"{self.DISPLAY_NAME} passwords cannot be changed by Noust",
            details="Change it with the engine's own client ('noust db connect').",
        )

    def is_internal_user(self, username: str) -> bool:
        """
        Tell whether an account belongs to the engine or to Noust itself.

        Such an account is listed but never altered or dropped through Noust:
        the superuser, the engine's maintenance accounts, and the read-only
        console's ``wasm_ro_`` accounts, which Noust re-provisions on its own.
        In a container, the account Noust signs in as is one too: the image
        created it, and its compose file holds its password.

        Args:
            username: The account.

        Returns:
            True for an internal account.
        """
        if self.instance is not None and username == self.instance.admin_user:
            return True
        return username in self.INTERNAL_USERS or username.startswith(READ_ONLY_ACCOUNT_PREFIX)

    def apply_profile(
        self, username: str, database: str, profile: str, host: str = "localhost"
    ) -> None:
        """
        Give an account exactly one access profile on a database.

        Profiles replace grants rather than add to them, so moving an account
        from read-write to read-only takes the writes away.

        Args:
            username: The account.
            database: The database.
            profile: ``owner``, ``read_write`` or ``read_only``.
            host: Host restriction, for engines that have one.

        Raises:
            DatabaseUserError: When the engine has no profiles, the profile is
                unknown, or the engine refuses.
        """
        raise DatabaseUserError(
            f"{self.DISPLAY_NAME} has no per-database access profiles",
            details="Use 'noust db grant' with the engine's own privileges instead.",
        )

    def list_access(self, database: str) -> list[AccessEntry]:
        """
        List who can reach a database, and with which profile.

        The base answers from :meth:`list_users`: every account whose listing
        names the database, with the grants it holds and no profile inferred.
        Engines with profiles compute the effective one from the grants.

        Args:
            database: The database.

        Returns:
            One entry per account.
        """
        return [
            AccessEntry(
                username=user.username,
                host=user.host,
                profile="custom",
                privileges=tuple(user.privileges),
                internal=self.is_internal_user(user.username),
            )
            for user in self.list_users()
            if database in user.databases
        ]

    def drop_read_only_account(self, database: str) -> None:
        """
        Remove the read-only console's account of a database that is gone.

        PostgreSQL roles and MySQL accounts are cluster-wide: dropping the
        database leaves ``wasm_ro_<database>`` behind, and a database created
        later under the same name would inherit it. Engines without such an
        account have nothing to do.

        Args:
            database: The dropped database.
        """

    def listen_addresses(self) -> ListenAddress | None:
        """
        Ask the running server where it listens.

        Returns:
            The configured addresses, or None when the engine cannot say.
        """
        return None

    # ==================== Backup & Restore ====================

    def _ensure_directory(self, path: Path, mode: int = BACKUP_DIR_MODE) -> Path:
        """
        Create a directory the caller chose, without touching an existing one.

        This is for destinations Noust does not own, such as the parent of a
        ``--output`` path: an existing directory keeps its permissions, because
        chmod-ing ``/tmp`` or a user's home would be a worse bug than a lax
        backup directory. Directories Noust owns go through
        :meth:`_ensure_private_directory` instead.

        Args:
            path: Directory to create.
            mode: Permissions for directories this call creates.

        Returns:
            The directory path.

        Raises:
            DatabaseBackupError: When the directory cannot be created.
        """
        try:
            # mkdir applies the mode at creation, so the directory is never
            # briefly world readable the way a create-then-chmod leaves it.
            # exist_ok keeps an existing directory exactly as it was.
            path.mkdir(parents=True, exist_ok=True, mode=mode)
        except OSError as exc:
            raise DatabaseBackupError(
                f"Cannot create the backup directory {path}",
                details=f"{exc}. Check ownership and free space, then retry.",
            ) from exc
        return path

    def _ensure_private_directory(self, path: Path, mode: int = BACKUP_DIR_MODE) -> Path:
        """
        Create or adopt a directory Noust owns, with its mode enforced.

        Anything Noust writes as root into a directory it owns has to be sure the
        directory is really the one it means: not a symlink pointing somewhere
        else, not another account's, and not left group or world writable by an
        earlier version or by whoever got there first. The mode is applied
        through the open descriptor, so the inode that was inspected is the
        inode that is modified and then written to.

        Args:
            path: Directory to create or adopt.
            mode: Permissions the directory must end up with.

        Returns:
            The directory path.

        Raises:
            DatabaseBackupError: When the path is a symlink, is not a directory,
                belongs to another account, or cannot be created.
        """
        self._ensure_directory(path.parent)
        try:
            try:
                os.mkdir(path, mode)
            except FileExistsError:
                pass
            # O_NOFOLLOW turns "someone replaced this with a symlink" into an
            # error instead of a redirect; O_DIRECTORY does the same for a file.
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                owner = os.fstat(fd).st_uid
                if owner != os.geteuid():
                    raise DatabaseBackupError(
                        f"The directory {path} belongs to uid {owner}",
                        details=(
                            "Noust refuses to write backups into a directory it does not own, "
                            f"because whoever owns it decides who reads them. Remove {path} "
                            "and retry."
                        ),
                    )
                os.fchmod(fd, mode)
            finally:
                os.close(fd)
        except OSError as exc:
            raise DatabaseBackupError(
                f"Cannot use the directory {path}",
                details=(
                    f"{exc}. It must be a real directory owned by this account, "
                    "not a symlink or a file."
                ),
            ) from exc
        return path

    def _ensure_backup_directory(self, destination: Path) -> None:
        """
        Prepare the directory a backup is about to be written into.

        Args:
            destination: The backup file that is about to be created.
        """
        if destination.parent == self.BACKUP_DIR:
            self._ensure_private_directory(destination.parent, BACKUP_DIR_MODE)
        else:
            self._ensure_directory(destination.parent)

    def _backup_path(
        self,
        database: str,
        output_path: Path | None,
        compress: bool,
        *,
        label: str | None = None,
        suffix: str | None = None,
    ) -> Path:
        """
        Decide where a backup is written.

        Args:
            database: Database the backup belongs to.
            output_path: Caller-supplied destination, used verbatim when given.
            compress: Whether the file will be gzipped.
            label: Overrides the database name in the generated file name.
            suffix: Overrides :attr:`BACKUP_SUFFIX`.

        Returns:
            The destination path.
        """
        if output_path is not None:
            return Path(output_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = (
            f"{self._backup_prefix()}{label or database}-{timestamp}{suffix or self.BACKUP_SUFFIX}"
        )
        if compress:
            filename += ".gz"
        return self.BACKUP_DIR / filename

    def _backup_prefix(self) -> str:
        """
        Start the name of every backup file this engine writes.

        ``postgresql-`` for the host's engine, as it always was;
        ``postgresql.project.service.`` for a container. A database name
        never holds a dot, so what follows the prefix is the database, and a
        service whose name starts with another's (``db`` and ``db-2``) never
        claims its dumps.

        Returns:
            The prefix.
        """
        if self.instance is None:
            return f"{self.ENGINE_NAME}-"
        return f"{storage_name(self.ENGINE_NAME)}."

    def _dump_to_file(
        self,
        argv: Sequence[str],
        destination: Path,
        *,
        database: str,
        compress: bool,
        env: Mapping[str, str] | None = None,
        secrets: Sequence[str] = (),
        timeout: int = TRANSFER_TIMEOUT,
        user: str | None = None,
    ) -> BackupInfo:
        """
        Run a dump command and stream its stdout straight into a file.

        The dump never passes through a shell and never through a Python string,
        which is what makes a binary dump or a dump containing quotes survive.

        Args:
            argv: The dump command.
            destination: File to write.
            database: Database being dumped, for messages and metadata.
            compress: Pipe the dump through gzip.
            env: Extra environment variables, for credentials.
            secrets: Values to keep out of the logs.
            timeout: Deadline in seconds.
            user: Run as this account instead of root. The destination file is
                still opened, created and owned by root before the dump starts:
                only the write end of the pipe is handed to the other account.

        Returns:
            Information about the backup that was written.

        Raises:
            DatabaseBackupError: When the dump command fails or writes nothing.
        """
        self._ensure_backup_directory(destination)
        if self.instance is not None:
            result = self.runner.capture_to_file(
                self.instance.exec_argv(argv, user=user, env=env),
                destination,
                compress=compress,
                env=env,
                timeout=timeout,
                secrets=secrets,
            )
        else:
            result = self.runner.capture_to_file(
                argv,
                destination,
                compress=compress,
                env=env,
                timeout=timeout,
                secrets=secrets,
                user=user,
            )
        if not result.success:
            raise DatabaseBackupError(
                f"Failed to back up '{database}'",
                details=(
                    result.stderr.strip()
                    or f"{argv[0]} exited with code {result.exit_code} and said nothing."
                ),
            )
        if not destination.exists():
            raise DatabaseBackupError(
                f"Backup of '{database}' produced no file",
                details=f"{argv[0]} reported success but {destination} does not exist.",
            )

        self.logger.info(f"Created backup: {destination}")
        return BackupInfo(
            path=destination,
            database=database,
            engine=self.ENGINE_NAME,
            size=destination.stat().st_size,
            created=datetime.now(),
            compressed=compress,
        )

    @contextmanager
    def _staged_backup(
        self,
        source: Path,
        staged_name: str,
        *,
        owner: str | None = None,
    ) -> Iterator[Path]:
        """
        Make a backup readable by the account that will restore it.

        Backups are written 0600 and owned by root, and a client such as psql
        opens the file itself, as the engine's own account. The file is therefore
        copied (or decompressed) into a traversable staging directory and handed
        to that account for the duration of the restore.

        The staging directory is adopted, never merely reused: it is traversable
        by design, so whoever gets there first must not be able to leave a
        symlink behind it or a symlink inside it and turn a root copy into a
        write of their choosing.

        Args:
            source: The backup file, plain or gzipped.
            staged_name: File name to use inside the staging directory.
            owner: Account that must be able to read the staged file.

        Yields:
            The path of the staged, plain-text copy.

        Raises:
            DatabaseBackupError: When staging fails.
        """
        self._ensure_private_directory(self.BACKUP_DIR, BACKUP_DIR_MODE)
        staging_dir = self._ensure_private_directory(self.BACKUP_DIR / ".staging", STAGING_DIR_MODE)
        staged = staging_dir / staged_name
        # Whatever is at the staged name is ours to remove: a leftover from a
        # crashed restore, or a symlink someone planted to catch the copy.
        try:
            staged.unlink(missing_ok=True)
        except OSError as exc:
            raise DatabaseBackupError(
                f"Cannot clear the staging path {staged}",
                details=f"{exc}. Remove it by hand and retry.",
            ) from exc
        try:
            if source.suffix == ".gz":
                result = self.runner.capture_to_file(
                    ["gzip", "-dc", str(source)],
                    staged,
                    timeout=TRANSFER_TIMEOUT,
                )
            else:
                result = self._exec(["cp", str(source), str(staged)], timeout=TRANSFER_TIMEOUT)
            if not result.success:
                raise DatabaseBackupError(
                    f"Failed to stage the backup {source}",
                    details=result.stderr.strip() or "Check free space in the backup directory.",
                )
            if owner and not hand_over_file(
                staged,
                user=owner,
                group=owner,
                mode=SECRET_MODE,
                runner=self.runner,
                logger=self.logger,
            ):
                raise DatabaseBackupError(
                    f"Could not hand the staged backup over to {owner}",
                    details=(
                        f"{staged} may still be owned by root; {owner} would not be "
                        "able to read it."
                    ),
                )
            yield staged
        finally:
            staged.unlink(missing_ok=True)

    @abstractmethod
    def backup(
        self,
        database: str,
        output_path: Path | None = None,
        compress: bool = True,
        **kwargs,
    ) -> BackupInfo:
        """
        Back up a database.

        Args:
            database: Database name.
            output_path: Custom output path.
            compress: Compress the backup with gzip.
            **kwargs: Engine-specific options.

        Returns:
            Information about the backup.

        Raises:
            DatabaseBackupError: When the backup fails.
        """

    def restore(
        self,
        database: str,
        backup_path: Path,
        drop_existing: bool = False,
        *,
        safety_backup: bool = True,
        on_safety_copy: Callable[[Path], None] | None = None,
        **kwargs,
    ) -> RestoreOutcome:
        """
        Restore a database from a backup, never losing what it held.

        The order is what makes it safe:

        1. The dump is checked (:meth:`_check_backup`) before anything is
           touched, so a refused dump costs nothing.
        2. When the database exists, what it holds is dumped first: the
           safety copy. It is mandatory when ``drop_existing`` asks to
           replace the database, and taken by default otherwise too, since
           loading a dump over live data overwrites rows as surely.
        3. The database is dropped and recreated (with its owner) when asked,
           then the dump is loaded (:meth:`_load_backup`).
        4. When loading fails and there is a safety copy, the database is
           dropped, recreated and loaded from the safety copy, and the error
           carries both tools' own words. The copy is kept either way.

        The previous version dropped the database and then loaded the dump,
        so a truncated file left an empty database and no way back.

        Args:
            database: Target database name.
            backup_path: Path to the backup file.
            drop_existing: Drop and recreate the database before loading.
            safety_backup: Take the safety copy when nothing is dropped. A
                replace always takes it, whatever this says.
            on_safety_copy: Called with the safety copy as soon as it exists,
                before anything is dropped or loaded: the caller records it
                there, so a process killed mid-restore still leaves the way
                back on record.
            **kwargs: Engine-specific options, handed to the checks and the
                loader (``format`` for PostgreSQL; ``isolated`` for a load
                beside a database, which must reach no other).

        Returns:
            What was done, the safety copy included.

        Raises:
            DatabaseBackupError: When the file is missing or refused, the
                safety copy cannot be taken, or the restore fails (the
                previous contents are back when the error says so).
        """
        self.validate_database_name(database)
        backup_path = Path(backup_path)
        if not backup_path.exists():
            raise DatabaseBackupError(
                f"Backup file not found: {backup_path}",
                details="Run 'noust db backups' to list the backups Noust knows about.",
            )
        self._check_backup(backup_path, **kwargs)

        exists = self.database_exists(database)
        safety: Path | None = None
        owner: str | None = None
        if exists and (safety_backup or drop_existing):
            owner = self._database_owner(database)
            safety = self.backup(database).path
            self.logger.info(f"Safety copy of '{database}' taken before the restore: {safety}")
            if on_safety_copy is not None:
                on_safety_copy(safety)

        replaced = exists and drop_existing
        if replaced:
            self.drop_database(database, force=True)
        if replaced or not exists:
            self._create_for_restore(database, owner)

        try:
            self._load_backup(database, backup_path, **kwargs)
        except DatabaseBackupError as exc:
            if safety is None:
                raise
            raise self._put_back(database, safety, owner, exc) from exc

        self.logger.info(f"Restored database: {database} from {backup_path}")
        return RestoreOutcome(
            database=database, source=backup_path, safety_copy=safety, replaced=replaced
        )

    def _put_back(
        self, database: str, safety: Path, owner: str | None, failure: DatabaseBackupError
    ) -> DatabaseBackupError:
        """
        Load the safety copy back after a failed restore.

        Args:
            database: The database the restore failed on.
            safety: The safety copy taken before it.
            owner: The owner the database had, to recreate it with.
            failure: What the failed restore raised.

        Returns:
            The error to raise, which says whether the previous contents are
            back and carries every tool's own output.
        """
        said = failure.details or str(failure)
        try:
            self.drop_database(database, force=True)
            self._create_for_restore(database, owner)
            self._load_backup(database, safety)
        except DatabaseError as again:
            return DatabaseBackupError(
                f"Restoring '{database}' failed, and putting its previous contents back failed too",
                details=(
                    f"The restore said:\n{said}\n\nLoading the safety copy said:\n"
                    f"{again.details or again}\n\nThe safety copy is kept at {safety}. "
                    f"Load it by hand with: noust db restore {database} {safety} "
                    f"--engine {self.ENGINE_NAME} --drop"
                ),
            )
        return DatabaseBackupError(
            f"Restoring '{database}' failed; its previous contents were put back",
            details=f"{said}\n\nThe safety copy it was put back from is kept at {safety}.",
        )

    def _check_backup(self, backup_path: Path, **kwargs: Any) -> None:
        """
        Refuse a dump before the database is touched.

        Args:
            backup_path: The dump.
            **kwargs: The restore's engine-specific options.

        Raises:
            DatabaseBackupError: When the dump must not be loaded.
        """

    def _database_owner(self, database: str) -> str | None:
        """
        Name the owner a recreated database must get back.

        Args:
            database: An existing database.

        Returns:
            The owning account, as :meth:`get_database_info` reports it, or
            None for an engine with no such concept.
        """
        try:
            return self.get_database_info(database).owner
        except DatabaseError as exc:
            self.logger.warning(f"Could not read the owner of '{database}': {exc}")
            return None

    def _create_for_restore(self, database: str, owner: str | None) -> None:
        """
        Create the empty database a dump is loaded into.

        Args:
            database: The database.
            owner: The owner it had before, if any.

        Raises:
            DatabaseError: When it cannot be created.
        """
        self.create_database(database, owner=owner)

    @abstractmethod
    def _load_backup(self, database: str, backup_path: Path, **kwargs: Any) -> None:
        """
        Load a dump into a database that exists.

        Args:
            database: The database.
            backup_path: The dump, plain or gzipped.
            **kwargs: The restore's engine-specific options.

        Raises:
            DatabaseBackupError: When the engine's loader fails; the error
                carries its output verbatim.
        """

    def list_backups(self, database: str | None = None) -> list[BackupInfo]:
        """
        List the backups this engine has written.

        Args:
            database: Only list backups of this database.

        Returns:
            Backups, newest first.
        """
        if not self.BACKUP_DIR.exists():
            return []

        # engine-database-YYYYmmdd_HHMMSS.ext, where the database name itself may
        # contain dashes, so the timestamp is what anchors the split. A
        # container's prefix ends in a dot, which no database name holds.
        prefix = self._backup_prefix()
        name_pattern = "[^.]+" if self.instance is not None else ".+"
        pattern = re.compile(
            rf"\A{re.escape(prefix)}(?P<database>{name_pattern})-\d{{8}}_\d{{6}}(?P<ext>\..+)?\Z"
        )

        backups: list[BackupInfo] = []
        for path in sorted(self.BACKUP_DIR.glob(f"{prefix}*")):
            match = pattern.match(path.name)
            if not match:
                continue
            db_name = match.group("database")
            if database and db_name != database:
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                self.logger.debug(f"Skipping unreadable backup {path}: {exc}")
                continue
            backups.append(
                BackupInfo(
                    path=path,
                    database=db_name,
                    engine=self.ENGINE_NAME,
                    size=stat.st_size,
                    created=datetime.fromtimestamp(stat.st_mtime),
                    compressed=path.suffix == ".gz",
                )
            )

        return sorted(backups, key=lambda backup: backup.created, reverse=True)

    # ==================== Query Execution ====================

    @abstractmethod
    def execute_query(
        self,
        database: str,
        query: str,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Execute a statement against a database.

        Args:
            database: Database name.
            query: Statement to execute.
            **kwargs: Engine-specific options.

        Returns:
            Whether the statement succeeded, and its output.

        Raises:
            DatabaseQueryError: When the statement fails.
        """

    def execute_query_structured(
        self,
        database: str,
        query: str,
        *,
        read_only: bool = False,
        max_rows: int = DEFAULT_STRUCTURED_ROW_CAP,
        timeout_s: int | None = None,
    ) -> StructuredQueryResult:
        """
        Execute a statement and, where the engine's client supports it, parse
        its result into columns and rows.

        The default falls back to :meth:`execute_query`'s plain text and
        leaves ``columns``/``rows`` empty: Redis and MongoDB have no tabular
        client output to parse from a generic invocation the way ``psql
        --csv`` or ``mysql --batch`` do, so the SQL consoles override this and
        those two engines document the gap by leaving it at the default. This
        never runs the query a second time - :meth:`execute_query` already is
        the one execution - so a write statement is never applied twice.

        Args:
            database: Database name.
            query: Statement to execute.
            read_only: Whether the statement must be refused if it writes.
            max_rows: Most rows kept in ``rows`` before truncation. Unused
                here: the fallback has no rows to cap.
            timeout_s: Seconds the server may spend on the statement. Unused
                here: an engine with no statement timeout has none to set.

        Returns:
            The plain output wrapped in :class:`StructuredQueryResult`, timed
            around the one call this makes.

        Raises:
            DatabaseQueryError: When the statement fails.
        """
        start = time.perf_counter()
        _, output = self.execute_query(database, query, read_only=read_only)
        duration_ms = (time.perf_counter() - start) * 1000
        return StructuredQueryResult(output=output, duration_ms=duration_ms)

    def run_sql(
        self, database: str, sql: str, *, read_only: bool, timeout_s: int | None = None
    ) -> str:
        """
        Run a statement Noust built (the data explorer's, the row editor's).

        Only the SQL engines have one; see their overrides.

        Args:
            database: The database.
            sql: The statement.
            read_only: Run it as the database's read-only account.
            timeout_s: Seconds the server may spend on it.

        Returns:
            What the engine printed.

        Raises:
            DatabaseQueryError: Always, here: the engine has no tables.
        """
        raise DatabaseQueryError(
            f"{self.DISPLAY_NAME} has no tables to browse",
            details="The data explorer and the row editor work on PostgreSQL and MySQL/MariaDB.",
        )

    def explain(
        self, database: str, statement: str, *, analyze: bool = False, timeout_s: int | None = None
    ) -> str:
        """
        Show how the engine would run a statement.

        Only the SQL engines have one; see their overrides.

        Args:
            database: The database.
            statement: The operator's statement.
            analyze: Execute it and report real timings.
            timeout_s: Seconds the server may spend on it.

        Returns:
            The plan.

        Raises:
            DatabaseQueryError: Always, here: the engine has no planner to ask.
        """
        raise DatabaseQueryError(
            f"EXPLAIN is not available for {self.DISPLAY_NAME}",
            details="EXPLAIN works on PostgreSQL and MySQL/MariaDB.",
        )

    def get_connection_string(
        self,
        database: str,
        username: str,
        password: str,
        host: str = "localhost",
    ) -> str:
        """
        Build a connection string for an application.

        Every engine builds it the one way (:func:`connection_url`): user and
        password percent-encoded, on the port the server really listens on.

        Args:
            database: Database name.
            username: User name.
            password: Password.
            host: Host to connect to.

        Returns:
            The connection string.
        """
        return connection_url(
            self.engine_type,
            database=database,
            user=username,
            password=password,
            host=host,
            port=self.server_port(),
            options=self._url_options(username),
        )

    def _url_options(self, username: str) -> dict[str, str] | None:
        """
        Add engine-specific parameters to a connection string.

        Args:
            username: The user the string is for.

        Returns:
            Query parameters, or None.
        """
        return None

    def get_interactive_command(
        self,
        database: str | None = None,
        username: str | None = None,
    ) -> list[str]:
        """
        Build the command that opens an interactive client.

        Args:
            database: Database to connect to.
            username: User to connect as.

        Returns:
            The argument vector to execute.

        Raises:
            NotImplementedError: When the engine does not define one.
        """
        raise NotImplementedError("Subclass must implement get_interactive_command")
