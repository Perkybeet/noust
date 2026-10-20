# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading a ``pg_dumpall`` file: its accounts, and one dump per database.

``pg_dumpall`` writes the cluster in one script: the roles and their
memberships, then every database as ``CREATE DATABASE``, a ``\\connect`` and the
database's own dump. psql would run all of it as one session as whoever signs
in, switching database on the way, and the roles it creates would be whatever
the file says (a superuser among them). None of that can be handed to a client
as it is, so this module reads the file and gives back things Noust can judge
one at a time (checklist H-18):

- **The accounts** (:class:`RoleSpec`), as data: name, whether it can sign in,
  the attributes that are safe to carry, and the password hash. Nothing of
  the file's own text is run afterwards: :func:`render_role` writes the
  statement from the data, so a role named ``x; DROP ...`` or an attribute
  nobody listed is a name or a note, never SQL. Superuser, ``CREATEROLE``,
  ``REPLICATION`` and ``BYPASSRLS`` are not carried (they are reported), a
  password is carried only as a SCRAM or MD5 hash of the shape the server
  stores, and a membership is kept only when both roles are in the dump.
- **One dump per database** (:class:`DatabaseSpec`): the lines after each
  ``\\connect``, written to a private file in the scratch directory. They are
  ordinary ``pg_dump`` plain dumps, which the one import
  (:meth:`~noust.managers.database.imports.DatabaseImports.run`) loads like any
  other: checked for meta-commands, loaded as the database's owner.

Where a database starts is decided the way psql reads the script
(:class:`~noust.managers.database.psql_script.StatementTracker`): a ``\\connect``
or a ``-- Database "x" dump`` inside COPY data, a string or a function body is
data, not a boundary.

What is not carried is listed in :attr:`ClusterDump.ignored`, so the operator
sees what the import did not do: tablespaces, per-role settings, ``DROP``
statements, and every grant that names a role outside the dump.
"""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Protocol

from noust.core.exceptions import DatabaseBackupError
from noust.core.fs import SECRET_MODE
from noust.managers.database.base import LOAD_ACCOUNT_PREFIX, READ_ONLY_ACCOUNT_PREFIX
from noust.managers.database.psql_script import StatementTracker

__all__ = [
    "BUILTIN_ROLES_CARRIED",
    "SYSTEM_DATABASES",
    "AccountSpec",
    "ClusterDump",
    "DatabaseSpec",
    "MembershipSpec",
    "RoleSpec",
    "is_cluster_dump",
    "read_cluster",
    "render_membership",
    "render_role",
]

#: Databases that belong to the cluster. Never loaded from a cluster dump unless named.
SYSTEM_DATABASES = frozenset({"template0", "template1", "postgres"})

#: Built-in roles whose membership is safe to carry: they read statistics and settings.
#: The ones that read or write files, run programs or end sessions are never granted.
BUILTIN_ROLES_CARRIED = frozenset(
    {
        "pg_monitor",
        "pg_read_all_settings",
        "pg_read_all_stats",
        "pg_stat_scan_tables",
        "pg_read_all_data",
        "pg_write_all_data",
    }
)

#: Role names that are the server's or Noust's own, never created from a dump.
_RESERVED_PREFIXES = ("pg_", READ_ONLY_ACCOUNT_PREFIX, LOAD_ACCOUNT_PREFIX)

#: A role or database name Noust will write into a statement. Narrower than PostgreSQL's.
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_$-]{0,62}")

#: The head of the pg_dumpall file.
_CLUSTER_HEADER = b"PostgreSQL database cluster dump"

#: What each database's file starts with: the comment a pg_dump starts with, which the engine's check
#: reads a plain dump by (the part after ``\\connect`` has the closing comment only).
SECTION_HEAD = b"--\n-- PostgreSQL database dump\n--\n\n"

_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'

_CREATE_ROLE = re.compile(rf"\s*CREATE\s+ROLE\s+({_IDENT})\s*;\s*", re.IGNORECASE)
_ALTER_ROLE = re.compile(
    rf"\s*ALTER\s+ROLE\s+({_IDENT})\s+WITH\s+(.*?)\s*;\s*", re.IGNORECASE | re.DOTALL
)
_ALTER_ROLE_ANY = re.compile(rf"\s*ALTER\s+ROLE\s+({_IDENT})\b", re.IGNORECASE)
_GRANT_ROLE = re.compile(
    rf"\s*GRANT\s+({_IDENT})\s+TO\s+({_IDENT})((?:\s+WITH\s+[A-Z ,]+?)?)"
    rf"(?:\s+GRANTED\s+BY\s+{_IDENT})?\s*;\s*",
    re.IGNORECASE,
)
_CREATE_DATABASE = re.compile(
    rf"\s*CREATE\s+DATABASE\s+({_IDENT})\b(.*?);\s*", re.IGNORECASE | re.DOTALL
)
_ALTER_DB_OWNER = re.compile(
    rf"\s*ALTER\s+DATABASE\s+({_IDENT})\s+OWNER\s+TO\s+({_IDENT})\s*;\s*", re.IGNORECASE
)
_ENCODING = re.compile(r"\bENCODING\s*=?\s*'([A-Za-z0-9_-]+)'", re.IGNORECASE)
_MARKER = re.compile(r'--\s*Database\s+"((?:[^"]|"")+)"\s+dump\s*', re.IGNORECASE)
_CONNECT = re.compile(
    rf"\\(?:connect|c)\b[ \t]*(?:-reuse-previous=(?:on|off)[ \t]*)?"
    rf"(?:({_IDENT})|\"dbname=(?:'((?:[^']|\\\\')+)'|([^ ']+))[^\"]*\")[ \t]*\r?\n?",
    re.IGNORECASE,
)

#: Role attributes in the order pg_dumpall writes them, one token each.
_ATTRIBUTE = re.compile(
    r"\s*(?:(?P<flag>NOSUPERUSER|SUPERUSER|NOINHERIT|INHERIT|NOCREATEROLE|CREATEROLE|"
    r"NOCREATEDB|CREATEDB|NOLOGIN|LOGIN|NOREPLICATION|REPLICATION|NOBYPASSRLS|BYPASSRLS)"
    r"|CONNECTION\s+LIMIT\s+(?P<limit>-?\d+)"
    r"|PASSWORD\s+(?:NULL|'(?P<password>(?:[^']|'')*)')"
    r"|VALID\s+UNTIL\s+'(?P<until>(?:[^']|'')*)')",
    re.IGNORECASE,
)

#: The hash shapes the server stores a password in; a plaintext one is never carried.
_HASH = re.compile(
    r"SCRAM-SHA-256\$\d{1,7}:[A-Za-z0-9+/=]{16,64}\$[A-Za-z0-9+/=]{40,64}:[A-Za-z0-9+/=]{40,64}"
    r"|md5[0-9a-f]{32}"
)

#: A timestamp the server wrote for ``VALID UNTIL``.
_UNTIL = re.compile(
    r"infinity|\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}(?::?\d{2})?)?"
)


class AccountSpec(Protocol):
    """What an import needs to know of an account of a dump, whichever engine's it is."""

    @property
    def name(self) -> str:
        """The account's name: a role's, or ``user@host``."""
        ...

    @property
    def login(self) -> bool:
        """Whether it can sign in."""
        ...

    @property
    def dropped(self) -> tuple[str, ...]:
        """What the source gave it that is not carried."""
        ...

    @property
    def password_hash(self) -> str | None:
        """Its password as the server stores it, or None."""
        ...


@dataclass(frozen=True)
class RoleSpec:
    """
    An account of the dumped cluster.

    Attributes:
        name: The role.
        login: It can sign in.
        inherit: It uses the privileges of the roles it belongs to.
        createdb: It may create databases.
        connection_limit: Concurrent sessions, -1 for no limit.
        password_hash: Its password as the server stores it; None when the dump
            holds none, or one that is not a hash.
        valid_until: When its password stops working, as the dump writes it.
        dropped: The attributes the dump gives it that are not carried.
    """

    name: str
    login: bool = False
    inherit: bool = True
    createdb: bool = False
    connection_limit: int = -1
    password_hash: str | None = None
    valid_until: str | None = None
    dropped: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """
        Returns:
            The account as plain data, without its password hash.
        """
        return {
            "name": self.name,
            "login": self.login,
            "createdb": self.createdb,
            "has_password": self.password_hash is not None,
            "valid_until": self.valid_until,
            "dropped": list(self.dropped),
        }


@dataclass(frozen=True)
class MembershipSpec:
    """
    ``GRANT role TO member``.

    Attributes:
        role: The role granted.
        member: Who gets it.
        admin_option: The member may grant it on.
    """

    role: str
    member: str
    admin_option: bool = False


@dataclass
class DatabaseSpec:
    """
    A database of the dumped cluster, cut out of the file.

    Attributes:
        name: The database.
        owner: Its owner in the dump, if it says.
        encoding: Its encoding, if it says.
        path: The dump of its contents (a plain pg_dump), in the scratch directory.
        size: That file's size in bytes.
        system: It belongs to the cluster (``postgres``, ``template1``).
    """

    name: str
    owner: str | None = None
    encoding: str | None = None
    path: Path | None = None
    size: int = 0
    system: bool = False

    def to_dict(self) -> dict[str, object]:
        """
        Returns:
            The database as plain data.
        """
        return {
            "name": self.name,
            "owner": self.owner,
            "encoding": self.encoding,
            "size": self.size,
            "system": self.system,
        }


@dataclass
class ClusterDump:
    """
    What a pg_dumpall file holds.

    Attributes:
        roles: The accounts, in the file's order.
        memberships: The role-to-role grants.
        databases: The databases, each cut out into a file.
        ignored: What the file holds that is not carried, one sentence each.
    """

    roles: list[AccountSpec] = field(default_factory=list)
    memberships: list[MembershipSpec] = field(default_factory=list)
    databases: list[DatabaseSpec] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- detecting


def is_cluster_dump(head: bytes) -> bool:
    """
    Tell a pg_dumpall file from its first bytes.

    Args:
        head: The start of the (decompressed) file.

    Returns:
        True when it announces itself as a database cluster dump.
    """
    return _CLUSTER_HEADER in head[:2048]


# --------------------------------------------------------------------- reading


def _unquote(identifier: str) -> str:
    """
    Args:
        identifier: A SQL identifier as pg_dumpall writes it.

    Returns:
        Its name: quotes removed, doubled quotes undone, unquoted names lower-cased as
        the server reads them.
    """
    if identifier.startswith('"'):
        return identifier[1:-1].replace('""', '"')
    return identifier.lower()


def _name_ok(name: str) -> bool:
    """
    Args:
        name: A role or database name from the dump.

    Returns:
        Whether Noust will write it into a statement.
    """
    return _NAME.fullmatch(name) is not None


def _open(source: Path) -> IO[bytes]:
    """
    Args:
        source: The dump, plain or gzipped.

    Returns:
        A binary stream of the plain text.
    """
    return gzip.open(source, "rb") if source.suffix == ".gz" else source.open("rb")  # type: ignore[return-value]


def _role_from(name: str, attributes: str, previous: RoleSpec | None) -> RoleSpec:
    """
    Read the attributes of an ``ALTER ROLE ... WITH``.

    Args:
        name: The role.
        attributes: Everything after ``WITH``.
        previous: What the dump said about it before, if anything.

    Returns:
        The role, with what is carried and a list of what is not.
    """
    role = previous or RoleSpec(name=name)
    login, inherit, createdb = role.login, role.inherit, role.createdb
    limit, password, until = role.connection_limit, role.password_hash, role.valid_until
    dropped = list(role.dropped)
    position = 0
    while position < len(attributes):
        match = _ATTRIBUTE.match(attributes, position)
        if match is None or match.end() == position:
            break
        position = match.end()
        if (flag := match.group("flag")) is not None:
            flag = flag.upper()
            if flag in ("SUPERUSER", "CREATEROLE", "REPLICATION", "BYPASSRLS"):
                if flag not in dropped:
                    dropped.append(flag)
            elif flag == "LOGIN":
                login = True
            elif flag == "NOLOGIN":
                login = False
            elif flag == "INHERIT":
                inherit = True
            elif flag == "NOINHERIT":
                inherit = False
            elif flag == "CREATEDB":
                createdb = True
            elif flag == "NOCREATEDB":
                createdb = False
        elif match.group("limit") is not None:
            limit = int(match.group("limit"))
        elif match.group("until") is not None:
            candidate = match.group("until").replace("''", "'")
            until = candidate if _UNTIL.fullmatch(candidate) else until
        elif match.group("password") is not None:
            candidate = match.group("password")
            password = candidate if _HASH.fullmatch(candidate) else None
            if password is None and "PASSWORD" not in dropped:
                dropped.append("PASSWORD")
    return RoleSpec(
        name=name,
        login=login,
        inherit=inherit,
        createdb=createdb,
        connection_limit=limit,
        password_hash=password,
        valid_until=until,
        dropped=tuple(dropped),
    )


class _Reader:
    """One pass over a pg_dumpall file: statements in the globals, files for the databases."""

    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.result = ClusterDump()
        self._roles: dict[str, RoleSpec] = {}
        self._tracker = StatementTracker()
        self._statement: list[str] = []
        self._current: DatabaseSpec | None = None
        self._pending: dict[str, DatabaseSpec] = {}
        self._header_for: str | None = None
        self._out: IO[bytes] | None = None

    # ---- lines

    def feed(self, raw: bytes) -> None:
        """
        Take one line of the file.

        Args:
            raw: The line, newline included.
        """
        text = raw.decode("latin-1")
        head = self._tracker.at_statement_head
        if head:
            marker = _MARKER.fullmatch(text.strip())
            if marker is not None:
                self._end_body()
                self._header_for = _unquote(f'"{marker.group(1)}"')
                self._statement = []
                self._tracker = StatementTracker()
                self._tracker.feed(text)
                return
            connect = _CONNECT.fullmatch(text)
            if connect is not None:
                self._start_body(connect)
                return
        if self._out is not None:
            self._out.write(raw)
        self._tracker.feed(text)
        if self._out is None:
            self._statement.append(text)
            if self._tracker.at_statement_head:
                self._handle("".join(self._statement))
                self._statement = []

    # ---- databases

    def _database(self, name: str) -> DatabaseSpec:
        found = self._pending.get(name)
        if found is None:
            found = DatabaseSpec(name=name, system=name in SYSTEM_DATABASES)
            self._pending[name] = found
            self.result.databases.append(found)
        return found

    def _start_body(self, connect: re.Match[str]) -> None:
        """Begin the file of the database a ``\\connect`` names."""
        raw = connect.group(1) or connect.group(2) or connect.group(3) or ""
        name = _unquote(raw) if connect.group(1) else raw.replace("\\'", "'")
        self._end_body()
        spec = self._database(name)
        if spec.path is not None:
            raise DatabaseBackupError(
                f"The dump connects to '{name}' twice",
                details="A pg_dumpall file holds each database once; this one was edited or joined.",
            )
        spec.path = self.scratch / f"database-{len(self.result.databases):03d}.sql"
        descriptor = _create_private(spec.path)
        descriptor.write(SECTION_HEAD)
        self._out = descriptor
        self._current = spec
        self._statement = []
        self._tracker = StatementTracker()

    def _end_body(self) -> None:
        if self._out is not None and self._current is not None:
            self._out.close()
            self._current.size = self._current.path.stat().st_size if self._current.path else 0
        self._out = None
        self._current = None

    def close(self) -> None:
        """Close the file being written, whether or not the whole file was read."""
        self._end_body()

    def finish(self) -> ClusterDump:
        """
        Gather the roles.

        Returns:
            What the file held.
        """
        self.result.roles = list(self._roles.values())
        return self.result

    # ---- statements outside a database's own dump

    def _handle(self, text: str) -> None:
        stripped = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("--")
        ).strip()
        if not stripped or stripped.startswith("\\"):
            return
        if self._header_for is None:
            self._globals(stripped)
        else:
            self._header(stripped)

    def _globals(self, text: str) -> None:
        if match := _CREATE_ROLE.fullmatch(text + " "):
            name = _unquote(match.group(1))
            self._roles.setdefault(name, RoleSpec(name=name))
        elif match := _ALTER_ROLE.fullmatch(text + " "):
            name = _unquote(match.group(1))
            self._roles[name] = _role_from(name, match.group(2), self._roles.get(name))
        elif _ALTER_ROLE_ANY.match(text):
            self.result.ignored.append(
                f"A per-role setting was not carried: {text.splitlines()[0][:80]}"
            )
        elif match := _GRANT_ROLE.fullmatch(text + " "):
            self.result.memberships.append(
                MembershipSpec(
                    role=_unquote(match.group(1)),
                    member=_unquote(match.group(2)),
                    admin_option="ADMIN" in (match.group(3) or "").upper(),
                )
            )
        elif re.match(r"(?:SET|SELECT)\b", text, re.IGNORECASE):
            return
        elif re.match(r"CREATE\s+TABLESPACE\b", text, re.IGNORECASE):
            self.result.ignored.append("A tablespace was not created: Noust does not make them")
        elif re.match(r"DROP\b", text, re.IGNORECASE):
            self.result.ignored.append(f"A DROP statement was not run: {text.splitlines()[0][:60]}")
        else:
            self.result.ignored.append(f"Not carried: {text.splitlines()[0][:80]}")

    def _header(self, text: str) -> None:
        """What comes between ``-- Database "x" dump`` and its ``\\connect``."""
        name = self._header_for or ""
        if match := _CREATE_DATABASE.fullmatch(text + " "):
            spec = self._database(_unquote(match.group(1)))
            encoding = _ENCODING.search(match.group(2))
            spec.encoding = encoding.group(1) if encoding else spec.encoding
        elif match := _ALTER_DB_OWNER.fullmatch(text + " "):
            self._database(_unquote(match.group(1))).owner = _unquote(match.group(2))
        elif re.match(r"(?:SET|SELECT)\b", text, re.IGNORECASE):
            return
        else:
            self.result.ignored.append(f"Not carried for '{name}': {text.splitlines()[0][:80]}")


def _create_private(path: Path) -> IO[bytes]:
    """
    Args:
        path: A file to make, owner-only and never through a link.

    Returns:
        It, open for writing.
    """
    import os

    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, SECRET_MODE)
    return os.fdopen(descriptor, "wb")


def read_cluster(source: Path, scratch: Path) -> ClusterDump:
    """
    Read a pg_dumpall file, writing each database's dump into ``scratch``.

    Args:
        source: The file, plain or gzipped.
        scratch: A private directory the caller removes.

    Returns:
        The accounts, the memberships and the databases.

    Raises:
        DatabaseBackupError: When the file cannot be read, holds a psql
            meta-command other than ``\\connect`` and the ``\\restrict`` pair, or
            names one database twice.
    """
    reader = _Reader(scratch)
    try:
        with _open(source) as handle:
            for raw in handle:
                reader.feed(raw)
    except (OSError, EOFError) as exc:
        raise DatabaseBackupError(
            f"Could not read the dump {source.name}",
            details=f"{exc}. Check that the file is complete and not damaged.",
        ) from exc
    finally:
        reader.close()
    return reader.finish()


# ------------------------------------------------------------------- rendering


def _ident(name: str) -> str:
    """
    Args:
        name: A name that passed :func:`_name_ok`.

    Returns:
        It quoted as a PostgreSQL identifier.
    """
    return '"' + name.replace('"', '""') + '"'


def usable_name(name: str) -> bool:
    """
    Args:
        name: A role name from a dump.

    Returns:
        Whether Noust will create an account of that name from a dump.
    """
    return _name_ok(name) and not name.startswith(_RESERVED_PREFIXES) and name != "postgres"


def render_role(role: RoleSpec) -> str:
    """
    Write the statement that creates an account, from what was read, never from the file's text.

    Args:
        role: The account.

    Returns:
        ``CREATE ROLE`` with only the attributes Noust carries.
    """
    parts = [
        "LOGIN" if role.login else "NOLOGIN",
        "INHERIT" if role.inherit else "NOINHERIT",
        "CREATEDB" if role.createdb else "NOCREATEDB",
        "NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS",
    ]
    if role.connection_limit >= 0:
        parts.append(f"CONNECTION LIMIT {int(role.connection_limit)}")
    if role.password_hash is not None:
        parts.append("PASSWORD '" + role.password_hash.replace("'", "''") + "'")
    if role.valid_until is not None:
        parts.append("VALID UNTIL '" + role.valid_until.replace("'", "''") + "'")
    return f"CREATE ROLE {_ident(role.name)} WITH {' '.join(parts)};\n"


def render_membership(grant: MembershipSpec) -> str:
    """
    Args:
        grant: A role-to-role grant between two accounts Noust is creating.

    Returns:
        The ``GRANT`` that makes it.
    """
    option = " WITH ADMIN OPTION" if grant.admin_option else ""
    return f"GRANT {_ident(grant.role)} TO {_ident(grant.member)}{option};\n"
