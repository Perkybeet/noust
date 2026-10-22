# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reading a ``mysqldump --all-databases`` and a file of accounts (checklist H-18).

The same idea as :mod:`~noust.managers.database.cluster_dump` for MySQL and MariaDB: nothing of
the file's text is handed to a client as it is, but cut into what Noust can judge one at a time.

- **One file per database.** ``mysqldump`` writes the server's databases one after another, each
  under ``-- Current Database: `name``` and, because views are written twice (a stand-in in the data
  pass, the real one after every table exists), each database comes back in a second pass under a
  second marker. A database's file is its parts in the order they appear, without the statements that
  name the database (``CREATE DATABASE``, ``USE``, ``DROP DATABASE``: the importer makes the database
  and loads into it), after the header the dump set up and before the footer it restored, so it reads
  as an ordinary dump of one database. Only line starts are looked at: ``mysqldump`` escapes the
  newlines inside a value, so a line that starts ``--`` or ``USE`` is a statement, never data (the
  rest of Noust's MySQL code relies on the same fact).
- **The server's own schemas are not loaded.** ``mysql`` holds the server's accounts and grants;
  loading it would overwrite them, and the root account with them.
- **The accounts come from a file of their own**, because a ``mysqldump`` does not carry them as
  statements: ``CREATE USER`` and ``GRANT`` lines, one per line, as ``SHOW CREATE USER`` and
  ``SHOW GRANTS`` print them (or ``pt-show-grants``). They are read into data and written again by
  :func:`render_account`: a password only as the ``mysql_native_password`` hash the server stores; a
  privilege only when it is about the data of a database or below, on a database or a table, and
  never ``WITH GRANT OPTION``; everything else is named as not carried. A ``caching_sha2_password``
  hash holds raw bytes a text file cannot carry exactly, so such an account is made locked, for the
  operator to give a password.
"""

from __future__ import annotations

import gzip
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from noust.core.exceptions import DatabaseBackupError
from noust.core.fs import SECRET_MODE
from noust.managers.database.cluster_dump import ClusterDump, DatabaseSpec

__all__ = [
    "SYSTEM_SCHEMAS",
    "MysqlAccount",
    "MysqlGrant",
    "is_mysql_cluster",
    "read_accounts",
    "read_mysql_cluster",
    "render_account",
    "render_account_create",
    "render_account_grants",
]

#: The schemas that are the server's, never loaded from a dump of all databases.
SYSTEM_SCHEMAS = frozenset({"mysql", "sys", "performance_schema", "information_schema"})

_MARKER = re.compile(r"-- Current Database: `((?:[^`]|``)+)`\s*")
_CREATE_DATABASE = re.compile(
    r"CREATE DATABASE\b.*?`((?:[^`]|``)+)`(.*);\s*", re.IGNORECASE | re.DOTALL
)
_CHARSET = re.compile(r"DEFAULT CHARACTER SET\s+([A-Za-z0-9_]+)", re.IGNORECASE)
_NAMES_THE_DATABASE = re.compile(
    r"(?:USE\s+`|CREATE\s+DATABASE\b|/\*!\d+\s+DROP\s+DATABASE\b|DROP\s+DATABASE\b)", re.IGNORECASE
)

#: Where a dump restores what it set up, once, at its very end.
_FOOTER = "/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */"

#: A name Noust writes into a statement.
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_$-]{0,79}")

#: A host part: a name, an address, a pattern.
_HOST = re.compile(r"[A-Za-z0-9_.%:/-]{1,255}")

#: ``mysql_native_password``'s hash, the one shape a text file carries exactly.
_NATIVE_HASH = re.compile(r"\*[0-9A-Fa-f]{40}")

#: The privileges about the data of a database or below, which are the ones carried.
DATA_PRIVILEGES = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "DROP",
        "ALTER",
        "INDEX",
        "REFERENCES",
        "CREATE TEMPORARY TABLES",
        "LOCK TABLES",
        "EXECUTE",
        "CREATE VIEW",
        "SHOW VIEW",
        "CREATE ROUTINE",
        "ALTER ROUTINE",
        "EVENT",
        "TRIGGER",
    }
)

_QUOTED = r"(?:`(?P<{n}b>[^`]+)`|'(?P<{n}q>[^']+)')"
_ACCOUNT = _QUOTED.format(n="u") + "@" + _QUOTED.format(n="h")
_CREATE_USER = re.compile(
    rf"CREATE\s+USER\s+(?:IF\s+NOT\s+EXISTS\s+)?{_ACCOUNT}(?P<rest>.*);\s*",
    re.IGNORECASE | re.DOTALL,
)
_GRANT = re.compile(
    rf"GRANT\s+(?P<privs>.+?)\s+ON\s+(?P<object>\S+?)\s+TO\s+{_ACCOUNT}(?P<rest>.*);\s*",
    re.IGNORECASE | re.DOTALL,
)
_HASH_CLAUSES = (
    re.compile(r"IDENTIFIED\s+BY\s+PASSWORD\s+'(?P<hash>[^']*)'", re.IGNORECASE),
    re.compile(r"IDENTIFIED\s+WITH\s+'?(?P<plugin>\w+)'?\s+AS\s+'(?P<hash>[^']*)'", re.IGNORECASE),
    re.compile(r"IDENTIFIED\s+VIA\s+(?P<plugin>\w+)\s+USING\s+'(?P<hash>[^']*)'", re.IGNORECASE),
)
_OBJECT = re.compile(r"(?:`(?P<db>[^`]+)`|\*)\.(?:`(?P<table>[^`]+)`|\*)")


@dataclass(frozen=True)
class MysqlGrant:
    """
    ``GRANT privileges ON database.table``.

    Attributes:
        privileges: The privileges, upper-cased.
        database: The database.
        table: The table, or None for every table of the database.
    """

    privileges: tuple[str, ...]
    database: str
    table: str | None = None


@dataclass(frozen=True)
class MysqlAccount:
    """
    An account of the dumped server.

    Attributes:
        user: The user name.
        host: The host part.
        password_hash: Its ``mysql_native_password`` hash; None when it has none or it cannot be
            carried.
        locked: It is made locked: the source had it so, or its password cannot be carried.
        grants: What it may do on a database or below.
        dropped: What the source gave it that is not carried, one phrase each.
    """

    user: str
    host: str
    password_hash: str | None = None
    locked: bool = False
    grants: tuple[MysqlGrant, ...] = ()
    dropped: tuple[str, ...] = field(default=())

    @property
    def name(self) -> str:
        """``user@host``, how the server tells accounts apart."""
        return f"{self.user}@{self.host}"

    @property
    def login(self) -> bool:
        """Whether the account can sign in."""
        return not self.locked

    def to_dict(self) -> dict[str, object]:
        """
        Returns:
            The account as plain data, without its password hash.
        """
        return {
            "name": self.name,
            "login": self.login,
            "createdb": False,
            "has_password": self.password_hash is not None,
            "valid_until": None,
            "dropped": list(self.dropped),
            "grants": [
                f"{', '.join(g.privileges)} ON {g.database}.{g.table or '*'}" for g in self.grants
            ],
        }


# ------------------------------------------------------------------- detecting


def is_mysql_cluster(head: bytes) -> bool:
    """
    Tell a ``mysqldump`` of several databases from its first bytes.

    Args:
        head: The start of the (decompressed) file.

    Returns:
        True when it is a MySQL or MariaDB dump that names a database under a
        ``-- Current Database`` marker (one taken with ``--databases`` or ``--all-databases``).
    """
    text = head[:8192].decode("latin-1")
    return ("-- MySQL dump" in text or "-- MariaDB dump" in text) and bool(
        re.search(r"^-- Current Database: `", text, re.MULTILINE)
    )


# --------------------------------------------------------------------- reading


def _open(source: Path) -> IO[bytes]:
    return gzip.open(source, "rb") if source.suffix == ".gz" else source.open("rb")  # type: ignore[return-value]


def _create_private(path: Path, *, append: bool = False) -> IO[bytes]:
    flags = os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | (os.O_APPEND if append else os.O_EXCL)
    return os.fdopen(os.open(path, flags, SECRET_MODE), "ab" if append else "wb")


class _Cutter:
    """One pass over a mysqldump: a file per user database."""

    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.result = ClusterDump()
        self._header: list[bytes] = []
        self._specs: dict[str, DatabaseSpec] = {}
        self._current: DatabaseSpec | None = None
        self._out: IO[bytes] | None = None
        self._head_of_section = False
        self._footer: list[bytes] = []
        self._in_footer = False
        self._seen_marker = False

    def feed(self, raw: bytes) -> None:
        """
        Take one line.

        Args:
            raw: The line, newline included.
        """
        text = raw.decode("utf-8", errors="replace")
        if self._in_footer:
            self._footer.append(raw)
            return
        if text.startswith(_FOOTER):
            self._close()
            self._in_footer = True
            self._footer.append(raw)
            return
        # A marker is the comment line '--' above it, the line itself and the '--' below.
        marker = _MARKER.fullmatch(text.rstrip("\r\n"))
        if marker is not None:
            self._start_section(marker.group(1).replace("``", "`"))
            return
        if not self._seen_marker:
            self._header.append(raw)
            return
        if self._current is None:
            return
        if self._head_of_section and self._names_the_database(text):
            self._read_create(text)
            return
        if self._head_of_section and text.strip() and not text.startswith("--"):
            self._head_of_section = False
        if self._out is not None:
            self._out.write(raw)

    def _names_the_database(self, text: str) -> bool:
        return _NAMES_THE_DATABASE.match(text.lstrip()) is not None

    def _read_create(self, text: str) -> None:
        if self._current is None:
            return
        found = _CREATE_DATABASE.search(text)
        if found is not None:
            charset = _CHARSET.search(found.group(2))
            if charset:
                self._current.encoding = charset.group(1)

    def _start_section(self, name: str) -> None:
        self._close()
        self._seen_marker = True
        spec = self._specs.get(name)
        if spec is None:
            spec = DatabaseSpec(name=name, system=name.lower() in SYSTEM_SCHEMAS)
            self._specs[name] = spec
            self.result.databases.append(spec)
        self._current = spec
        self._head_of_section = True
        if spec.system:
            return
        if spec.path is None:
            spec.path = self.scratch / f"database-{len(self._specs):03d}.sql"
            self._out = _create_private(spec.path)
            for line in self._header:
                self._out.write(line)
        else:
            self._out = _create_private(spec.path, append=True)

    def close(self) -> None:
        """Close the file being written."""
        self._close()

    def _close(self) -> None:
        if self._out is not None:
            self._out.close()
        self._out = None

    def finish(self) -> ClusterDump:
        """
        Close the files, with the footer the dump restored at its end.

        Returns:
            What the file held.
        """
        self._close()
        footer = b"".join(self._footer) or b"-- Dump completed\n"
        for spec in self.result.databases:
            if spec.path is None:
                continue
            with _create_private(spec.path, append=True) as out:
                out.write(footer)
            spec.size = spec.path.stat().st_size
        return self.result


def read_mysql_cluster(source: Path, scratch: Path) -> ClusterDump:
    """
    Read a ``mysqldump`` of several databases, writing each user database into ``scratch``.

    Args:
        source: The file, plain or gzipped.
        scratch: A private directory the caller removes.

    Returns:
        The databases, in the order they first appear; the server's own have no file.

    Raises:
        DatabaseBackupError: When the file cannot be read.
    """
    cutter = _Cutter(scratch)
    try:
        with _open(source) as handle:
            for raw in handle:
                cutter.feed(raw)
    except (OSError, EOFError) as exc:
        raise DatabaseBackupError(
            f"Could not read the dump {source.name}",
            details=f"{exc}. Check that the file is complete and not damaged.",
        ) from exc
    finally:
        cutter.close()
    return cutter.finish()


# -------------------------------------------------------------------- accounts


def _pick(match: re.Match[str], stem: str) -> str:
    return match.group(f"{stem}b") or match.group(f"{stem}q") or ""


def _identification(rest: str) -> tuple[str | None, str | None]:
    """
    Read how an account is identified.

    Args:
        rest: What follows the account in its ``CREATE USER`` or ``GRANT``.

    Returns:
        The plugin the dump names (None when it names none) and the hash it gives.
    """
    for pattern in _HASH_CLAUSES:
        found = pattern.search(rest)
        if found is not None:
            groups = found.groupdict()
            return groups.get("plugin"), groups.get("hash")
    return None, None


@dataclass
class _Draft:
    """An account while its statements are being read."""

    user: str
    host: str
    password_hash: str | None = None
    locked: bool = False
    grants: list[MysqlGrant] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    def note(self, what: str) -> None:
        """
        Record something the source gave the account that is not carried.

        Args:
            what: A phrase naming it.
        """
        if what not in self.dropped:
            self.dropped.append(what)

    def done(self) -> MysqlAccount:
        """
        Returns:
            The account as it was read.
        """
        return MysqlAccount(
            user=self.user,
            host=self.host,
            password_hash=self.password_hash,
            locked=self.locked,
            grants=tuple(self.grants),
            dropped=tuple(self.dropped),
        )


def read_accounts(source: Path) -> tuple[list[MysqlAccount], list[str]]:
    """
    Read a file of ``CREATE USER`` and ``GRANT`` statements, one per line.

    Args:
        source: The file, plain or gzipped.

    Returns:
        The accounts, and one sentence for every statement that was not read into one.

    Raises:
        DatabaseBackupError: When the file cannot be read.
    """
    drafts: dict[str, _Draft] = {}
    ignored: list[str] = []

    def draft(user: str, host: str, *, make: bool) -> _Draft | None:
        if not _NAME.fullmatch(user) or not _HOST.fullmatch(host):
            return None
        key = f"{user}@{host}"
        if key not in drafts and make:
            drafts[key] = _Draft(user, host)
        return drafts.get(key)

    try:
        with _open(source) as handle:
            lines = [raw.decode("utf-8", errors="replace").strip() for raw in handle]
    except (OSError, EOFError) as exc:
        raise DatabaseBackupError(
            f"Could not read {source.name}",
            details=f"{exc}. Check that the file is complete and not damaged.",
        ) from exc

    for line in lines:
        if not line or line.startswith(("--", "#", "/*")):
            continue
        created = _CREATE_USER.fullmatch(line)
        if created is not None:
            account = draft(_pick(created, "u"), _pick(created, "h"), make=True)
            if account is None:
                ignored.append(f"Not an account Noust makes: {line[:80]}")
                continue
            _identify(account, created.group("rest"))
            continue
        granted = _GRANT.fullmatch(line)
        if granted is not None:
            # An older format has no CREATE USER: the first GRANT (USAGE) makes the account.
            usage = granted.group("privs").strip().upper() == "USAGE"
            account = draft(_pick(granted, "u"), _pick(granted, "h"), make=usage)
            if account is None:
                ignored.append(f"A grant to an account the file does not create: {line[:80]}")
                continue
            _grant(account, granted)
            continue
        ignored.append(f"Not carried: {line[:80]}")

    return [item.done() for item in drafts.values()], ignored


def _identify(account: _Draft, rest: str) -> None:
    """
    Read what a ``CREATE USER`` says about the account itself.

    Args:
        account: The account being built.
        rest: What follows the account.
    """
    plugin, digest = _identification(rest)
    if digest is not None:
        if _NATIVE_HASH.fullmatch(digest):
            account.password_hash = digest.upper()
        else:
            account.locked = True
            account.note(
                f"its {plugin or 'password'} hash holds bytes a text file cannot carry: "
                "the account is made locked, give it a password"
            )
    if re.search(r"\bACCOUNT\s+LOCK\b", rest, re.IGNORECASE):
        account.locked = True
    if re.search(r"\bREQUIRE\s+(?!NONE\b)\w+", rest, re.IGNORECASE):
        account.note("REQUIRE (a TLS requirement of the source)")
    if re.search(r"\bWITH\s+MAX_\w+", rest, re.IGNORECASE):
        account.note("resource limits")


def _grant(account: _Draft, granted: re.Match[str]) -> None:
    """
    Read one ``GRANT`` into what is carried and what is not.

    Args:
        account: The account being built.
        granted: The statement.
    """
    privileges = [p.strip().upper() for p in granted.group("privs").split(",") if p.strip()]
    rest = granted.group("rest")
    if re.search(r"\bWITH\s+GRANT\s+OPTION\b", rest, re.IGNORECASE):
        account.note("GRANT OPTION")
    _plugin, digest = _identification(rest)
    if digest is not None and account.password_hash is None and _NATIVE_HASH.fullmatch(digest):
        account.password_hash = digest.upper()
    target = _OBJECT.fullmatch(granted.group("object"))
    if target is None:
        account.note(f"{', '.join(privileges)} ON {granted.group('object')}")
        return
    database, table = target.group("db"), target.group("table")
    if database is None:
        if privileges != ["USAGE"]:
            account.note(
                "ALL PRIVILEGES ON *.*"
                if privileges in (["ALL PRIVILEGES"], ["ALL"])
                else f"{', '.join(privileges)} ON *.*"
            )
        return
    if not _NAME.fullmatch(database) or (table is not None and not _NAME.fullmatch(table)):
        account.note(f"{', '.join(privileges)} ON {database}.{table or '*'}")
        return
    kept: list[str] = []
    for privilege in privileges:
        if privilege in DATA_PRIVILEGES:
            kept.append(privilege)
        elif privilege in ("ALL", "ALL PRIVILEGES"):
            kept.extend(sorted(DATA_PRIVILEGES))
            account.note("ALL PRIVILEGES became the data privileges")
        elif privilege != "USAGE":
            account.note(privilege)
    if kept:
        account.grants.append(MysqlGrant(tuple(dict.fromkeys(kept)), database, table))


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def render_account(account: MysqlAccount, *, mariadb: bool) -> str:
    """
    Write the statements that make an account, from what was read, never from the file's text.

    Args:
        account: The account.
        mariadb: The server is MariaDB (its words for a password hash differ from MySQL's).

    Returns:
        ``CREATE USER`` and one ``GRANT`` per database or table, one statement per line.
    """
    return render_account_create(account, mariadb=mariadb) + render_account_grants(account)


def render_account_create(account: MysqlAccount, *, mariadb: bool) -> str:
    """
    Write the statement that makes an account.

    Args:
        account: The account.
        mariadb: The server is MariaDB (its words for a password hash differ from MySQL's).

    Returns:
        One ``CREATE USER`` line.
    """
    who = f"{_quote(account.user)}@{_quote(account.host)}"
    create = f"CREATE USER {who}"
    if account.password_hash is not None:
        if mariadb:
            create += f" IDENTIFIED BY PASSWORD {_quote(account.password_hash)}"
        else:
            create += f" IDENTIFIED WITH mysql_native_password AS {_quote(account.password_hash)}"
    if account.locked:
        create += " ACCOUNT LOCK"
    return create + ";\n"


def render_account_grants(account: MysqlAccount) -> str:
    """
    Write the statements that give an account its privileges.

    They are run after the databases are loaded: a grant on a table needs the table.

    Args:
        account: The account.

    Returns:
        One ``GRANT`` line per database or table; empty for an account that has none.
    """
    who = f"{_quote(account.user)}@{_quote(account.host)}"
    lines: list[str] = []
    for grant in account.grants:
        target = f"`{grant.database}`." + (f"`{grant.table}`" if grant.table else "*")
        lines.append(f"GRANT {', '.join(grant.privileges)} ON {target} TO {who};")
    return "".join(f"{line}\n" for line in lines)
