# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PostgreSQL manager.

Statements Noust builds are fed to ``psql`` on stdin, never with ``-c``: a
CREATE ROLE carries a password, and everything in argv is visible in ``ps``.
The console's statement is the one exception, and goes the other way for a
reason: psql reads stdin as a script and runs its meta-commands (``\\!`` is a
shell) wherever they appear, while a ``-c`` string is sent to the server as it
is. See :meth:`PostgresManager._console_argv`. Dumps are streamed to disk by
the runner, so a dump containing quotes or binary bytes arrives intact.

The read-only console is the one connection that does not run as the cluster
superuser. It signs in over TCP as ``wasm_ro_<database>``, a role that holds
nothing but ``SELECT``: a superuser session that merely switched roles can
switch back from inside a single SELECT (``set_config('role', ...)``), so the
limit has to be the login itself. See :meth:`PostgresManager._ensure_read_only_role`.

That TCP login, and every connection string Noust shows, uses the port of the
cluster Noust administers, found by :meth:`PostgresManager.server_port`: the
superuser session reaches that cluster through Debian's pg_wrapper, which
picks the cluster's port by itself, but a TCP client has to be told, and a
fixed 5432 on a server whose cluster listens on 5433 signs in to whatever else
answers on 5432 (a container publishing it, a second cluster).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import os
import re
import secrets
import stat
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from noust.core.config import secure_write
from noust.core.exceptions import (
    DatabaseBackupError,
    DatabaseError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    DatabaseUserError,
)
from noust.core.fs import FileSystem
from noust.core.runner import CommandResult, CommandRunner, runuser_prefix
from noust.managers.database.base import (
    DEFAULT_STRUCTURED_ROW_CAP,
    PROFILES,
    QUERY_TIMEOUT,
    AccessEntry,
    BackupInfo,
    BaseDatabaseManager,
    DatabaseInfo,
    ListenAddress,
    StructuredQueryResult,
    UserInfo,
    console_statement,
    format_size,
    listen_address,
    load_failure,
    parse_tabular_query_output,
    query_deadline,
    quote_identifier,
    restore_timeout,
    validate_name,
)
from noust.managers.database.instances import storage_name
from noust.managers.database.psql_script import check_plain_dump
from noust.managers.database.registry import DatabaseRegistry

#: Privileges PostgreSQL accepts on a DATABASE object.
DATABASE_PRIVILEGES = frozenset(
    {
        "ALL",
        "ALL PRIVILEGES",
        "CONNECT",
        "CREATE",
        "TEMP",
        "TEMPORARY",
    }
)

#: Privileges PostgreSQL accepts on a TABLE object.
TABLE_PRIVILEGES = frozenset(
    {
        "ALL",
        "ALL PRIVILEGES",
        "DELETE",
        "INSERT",
        "MAINTAIN",
        "REFERENCES",
        "SELECT",
        "TRIGGER",
        "TRUNCATE",
        "UPDATE",
    }
)

#: pg_dump output formats that can be streamed to stdout. "directory" cannot.
DUMP_FORMATS = frozenset({"plain", "custom", "tar"})

#: The format new dumps are taken in: compressed by pg_dump itself, checked by
#: ``pg_restore --list`` and restored selectively. Plain ``.sql`` dumps taken
#: before 3.1 are still recognised and restored the way they always were.
DEFAULT_DUMP_FORMAT = "custom"

#: File name suffix per dump format.
DUMP_SUFFIXES: Mapping[str, str] = {"plain": ".sql", "custom": ".dump", "tar": ".tar"}

#: The first bytes of a pg_dump custom-format archive.
CUSTOM_FORMAT_SIGNATURE = b"PGDMP"

#: Where a tar archive says so: "ustar" at offset 257 of its first header.
_TAR_MAGIC_OFFSET = 257

#: Relation kinds re-owned by fix-owner, with the statement each takes.
_RELATION_KINDS: Mapping[str, str] = {
    "r": "TABLE",
    "p": "TABLE",
    "v": "VIEW",
    "m": "MATERIALIZED VIEW",
    "S": "SEQUENCE",
    "f": "FOREIGN TABLE",
}

#: Schemas that belong to the cluster, never touched by a profile or fix-owner.
_SYSTEM_SCHEMA_FILTER = (
    "nspname NOT IN ('pg_catalog', 'information_schema') AND nspname NOT LIKE 'pg\\_%'"
)

#: Prefix of the least-privilege role the read-only console connects as.
_READ_ONLY_ROLE_PREFIX = "wasm_ro_"

#: PostgreSQL's NAMEDATALEN limit for an identifier, minus the terminator.
_IDENTIFIER_MAX_LENGTH = 63

#: The only address the read-only console signs in from. Password logins are
#: scoped to it in pg_hba.conf, so nothing but a local process can use them.
READ_ONLY_HOST = "127.0.0.1"

#: Where each read-only role's password is kept, under the store's directory:
#: ``/var/lib/noust/secrets/postgresql`` on a server.
_READ_ONLY_SECRETS_PATH = ("secrets", "postgresql")

#: What :func:`secrets.token_urlsafe` produces. A stored value that is anything
#: else was not written by Noust and is replaced rather than used.
_STORED_PASSWORD = re.compile(r"[A-Za-z0-9_-]{32,128}")

#: psql's exit status when it could not connect (EXIT_BADCONN), as opposed to 3
#: for a statement that failed under ON_ERROR_STOP.
_PSQL_EXIT_BADCONN = 2

#: How libpq words a connection attempt that failed: ``connection to server at
#: "127.0.0.1", port 5432 failed: FATAL: ...`` since PostgreSQL 14, ``could
#: not connect to server`` or a bare ``psql: FATAL:`` before it.
_SIGN_IN_FAILURE = re.compile(
    r"connection to server at .* failed|could not connect to server|^psql: FATAL:", re.M
)

#: libpq relaying a server that has no role of that name.
_ROLE_MISSING = re.compile(r'role ".*" does not exist')

#: Where the configured port lives, named in the errors that suggest it.
_PORT_SETTING = "databases.credentials.postgresql.port"

#: SCRAM-SHA-256 iteration count, PostgreSQL's own default (scram_iterations).
_SCRAM_ITERATIONS = 4096

#: What a read-only session always runs with.
_READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"

#: How long a read-only role provisioned by this process is trusted as it is.
#: The data explorer reads several times per screen; re-granting every schema
#: before each read (research/3.1/databases.md, B12) doubled the processes
#: per page. A read that finds the password or the grants stale provisions
#: again and retries, so the window only saves work, it never serves stale
#: access.
_PROVISION_REUSE_SECONDS = 60.0

#: When this process last provisioned each database's read-only role.
_provisioned_at: dict[str, float] = {}

#: What the server says when a role lacks a grant, such as on a table created
#: since the role was last provisioned.
_PERMISSION_DENIED = "permission denied for"

#: What psql prints for a NULL in the console's CSV (``-P null=``), so a NULL
#: and an empty string, which --csv prints alike, can be told apart. Control
#: characters no value a console shows is likely to be.
NULL_MARKER = "\x1enoust-null\x1e"


def _session_options(timeout_s: int | None, *, read_only: bool) -> str | None:
    """
    Build the PGOPTIONS a console or explorer session runs with.

    Options travel in the environment, not in argv or in the SQL, so they
    apply before the first statement and nothing the operator typed can
    reach them.

    Args:
        timeout_s: The statement timeout, or None for the server's own.
        read_only: Whether the session must be read-only.

    Returns:
        The options, or None when there are none to set.
    """
    options = [_READ_ONLY_OPTIONS] if read_only else []
    if timeout_s is not None:
        options.append(f"-c statement_timeout={int(timeout_s) * 1000}")
    return " ".join(options) or None


def _read_only_role_name(database: str) -> str:
    """
    Build the deterministic, length-safe role name for a database's console.

    A role assembled by simple concatenation collides silently once it
    overflows PostgreSQL's 63-character identifier limit: the server accepts
    the CREATE ROLE statement and truncates, so two long, similarly prefixed
    database names could end up sharing one read-only role without either
    creation failing. A short hash of the full name keeps every role
    distinct even when it has to be shortened.

    Args:
        database: The database the role is scoped to.

    Returns:
        ``wasm_ro_<database>``, unchanged when it fits within the limit;
        otherwise truncated and suffixed with an 8-character digest of the
        full database name.
    """
    candidate = f"{_READ_ONLY_ROLE_PREFIX}{database}"
    if len(candidate) <= _IDENTIFIER_MAX_LENGTH:
        return candidate
    digest = hashlib.sha256(database.encode()).hexdigest()[:8]
    budget = _IDENTIFIER_MAX_LENGTH - len(_READ_ONLY_ROLE_PREFIX) - len(digest) - 1
    return f"{_READ_ONLY_ROLE_PREFIX}{database[:budget]}_{digest}"


def _scram_sha256_verifier(password: str) -> str:
    """
    Hash a password into the SCRAM-SHA-256 verifier PostgreSQL stores.

    ``ALTER ROLE ... PASSWORD`` stores a value in this format as it is, so the
    statement that sets the read-only role's password never carries the
    password itself: with ``log_statement = 'ddl'`` the server log, and
    ``pg_stat_activity`` while it runs, only ever show the verifier, which
    cannot be turned back into the password. The algorithm is RFC 5802 with
    the RFC 7677 hash, the same one libpq's ``PQencryptPasswordConn`` runs.
    SASLprep is the identity for the ASCII passwords Noust generates.

    Args:
        password: The plaintext password.

    Returns:
        ``SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>``.
    """
    salt = os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _SCRAM_ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def encode(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    return (
        f"SCRAM-SHA-256${_SCRAM_ITERATIONS}:{encode(salt)}"
        f"${encode(stored_key)}:{encode(server_key)}"
    )


def _dollar_tag(stem: str, *values: str) -> str:
    """
    Pick a dollar-quote tag that none of the interpolated values contains.

    A database or role name may contain ``$`` (Noust's names allow it), and
    one containing ``$tag$`` would close a ``DO $tag$ ... $tag$`` block early
    and have the rest of its text run as SQL, as the cluster superuser. A
    random suffix is added until the tag appears in none of the values.

    Args:
        stem: A readable stem for the tag.
        *values: Everything interpolated into the block's body.

    Returns:
        ``$<stem>_<suffix>$``.
    """
    while True:
        tag = f"${stem}_{secrets.token_hex(4)}$"
        if not any(tag in value for value in values):
            return tag


@dataclass(frozen=True)
class OwnerPlan:
    """
    What ``noust db fix-owner`` would change, or changed, on one database.

    Attributes:
        database: The database.
        current_owner: Its owner before the change.
        new_owner: The owner it is given.
        objects: The objects re-owned with it, as ``KIND schema.name``: the
            relations and schemas the current owner holds outside the
            system schemas. Objects another role owns are left alone.
        statements: The exact statements, in order.
        applied: Whether they were run, or only shown.
    """

    database: str
    current_owner: str | None
    new_owner: str
    objects: tuple[str, ...]
    statements: tuple[str, ...]
    applied: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Render the plan as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        data = asdict(self)
        data["objects"] = list(self.objects)
        data["statements"] = list(self.statements)
        return data


def _parse_port(value: object) -> int | None:
    """
    Read a TCP port from a configuration value or a server's answer.

    Args:
        value: What was configured, or what ``SHOW port`` printed.

    Returns:
        The port, or None when the value is not one.
    """
    try:
        port = int(str(value).strip())
    except ValueError:
        return None
    return port if 0 < port < 65536 else None


def _sign_in_failed(result: CommandResult) -> bool:
    """
    Tell a psql that never got a session apart from a statement that failed.

    Args:
        result: psql's result.

    Returns:
        True when psql exited with EXIT_BADCONN and libpq reported the
        connection attempt itself failing: refused by pg_hba.conf, a rejected
        password, nothing listening. A session lost halfway also exits 2, but
        says so differently and is reported as a failed query.
    """
    return result.exit_code == _PSQL_EXIT_BADCONN and bool(_SIGN_IN_FAILURE.search(result.stderr))


class PostgresManager(BaseDatabaseManager):
    """Manager for PostgreSQL databases."""

    ENGINE_NAME = "postgresql"
    DISPLAY_NAME = "PostgreSQL"
    DEFAULT_PORT = 5432
    SERVICE_NAME = "postgresql"
    PACKAGE_NAMES = ("postgresql", "postgresql-contrib")
    CLIENT_BINARY = "psql"
    VERSION_ARGV = ("psql", "--version")
    VERSION_PATTERN = r"(\d+\.\d+)"
    PURGE_PATHS = ("/var/lib/postgresql", "/etc/postgresql")
    MAX_DATABASE_NAME_LENGTH = 63
    MAX_USER_NAME_LENGTH = 63
    VALID_PRIVILEGES = DATABASE_PRIVILEGES | TABLE_PRIVILEGES
    DEFAULT_PRIVILEGES = ("ALL PRIVILEGES",)
    SUPPORTS_STRUCTURED_QUERY = True
    BACKUP_SUFFIX = DUMP_SUFFIXES[DEFAULT_DUMP_FORMAT]
    CAPABILITIES = frozenset({"sql", "tables", "read_only", "users", "profiles", "dump", "metrics"})
    INTERNAL_USERS = frozenset({"postgres"})

    #: The account that owns the cluster and can authenticate by peer.
    SUPERUSER = "postgres"

    #: Databases that belong to the cluster, not to a user.
    SYSTEM_DATABASES = frozenset({"postgres", "template0", "template1"})

    def __init__(
        self,
        verbose: bool = False,
        runner: CommandRunner | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        """
        Initialize the manager.

        Args:
            verbose: Enable verbose logging.
            runner: Command runner to execute with.
            fs: Filesystem to write through.
        """
        super().__init__(verbose=verbose, runner=runner, fs=fs)
        # The port and where it came from, once known; see server_port().
        self._port: tuple[int, str] | None = None

    # ==================== SQL text ====================

    @staticmethod
    def _escape_identifier(value: str) -> str:
        """
        Quote an identifier the way PostgreSQL does.

        Args:
            value: Raw identifier.

        Returns:
            The identifier in double quotes, with embedded double quotes doubled.
        """
        return quote_identifier(value, '"')

    @staticmethod
    def _escape_literal(value: str) -> str:
        """
        Quote a string literal the way PostgreSQL does.

        Args:
            value: Raw string.

        Returns:
            The value in single quotes, with embedded single quotes doubled.
        """
        return "'" + value.replace("'", "''") + "'"

    def _psql_argv(self, database: str, *tail: str) -> list[str]:
        """
        Build a psql invocation that fails loudly and prints only data.

        Runs as :data:`SUPERUSER`, applied by the caller through ``_exec``'s
        ``user=`` rather than baked in here: PostgreSQL's peer authentication
        only accepts a connection from the OS account of the same name, and
        Noust runs as root, not ``postgres``.

        Args:
            database: Database to connect to.
            *tail: Arguments describing where the SQL comes from.

        Returns:
            The argument vector.
        """
        return [
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            database,
            "-t",
            "-A",
            # No psqlrc: a startup file is a script psql would run first.
            "-X",
            *tail,
        ]

    def _execute_sql(
        self,
        sql: str,
        database: str = "postgres",
        *,
        secrets: Sequence[str] = (),
        timeout: int = QUERY_TIMEOUT,
        env: Mapping[str, str] | None = None,
    ) -> tuple[bool, str]:
        """
        Run SQL as the cluster superuser, passing the statement on stdin.

        Args:
            sql: The statement.
            database: Database to connect to.
            secrets: Values the statement carries that must not be logged.
            timeout: Deadline in seconds.
            env: Extra environment for psql, such as PGOPTIONS.

        Returns:
            Whether psql succeeded, and its output or its error text.
        """
        result = self._exec(
            self._psql_argv(database, "-f", "-"),
            input=sql,
            timeout=timeout,
            secrets=secrets,
            env=env,
            user=self.SUPERUSER,
        )
        return result.success, result.stdout if result.success else result.stderr

    def _show(self, setting: str) -> str | None:
        """
        Ask the cluster Noust administers for one of its settings.

        Args:
            setting: A setting name Noust chose, never input.

        Returns:
            The value as the server prints it, or None when the superuser
            session could not be opened.
        """
        success, output = self._execute_sql(f"SHOW {setting};")
        return output.strip() if success else None

    # ==================== Port ====================

    def server_port(self) -> int:
        """
        Return the TCP port of the PostgreSQL Noust administers.

        What every TCP path uses: the read-only console's login and the
        connection strings shown to operators. Precedence:

        1. ``databases.credentials.postgresql.port``, when configured: the
           operator's word, for a server the superuser session cannot
           describe (pg_hba.conf answers, but TCP is forwarded elsewhere).
        2. ``SHOW port`` over the superuser session, which reaches the cluster
           Noust administers however it was found (Debian's pg_wrapper picks
           the cluster's port for ``psql``; a TCP client has to be told).
        3. :data:`DEFAULT_PORT`, when neither answers. Not remembered, so a
           server started later is asked on the next call.

        Only ``port`` is asked. ``listen_addresses`` only matters when the
        login is refused, and is asked then (:meth:`_sign_in_refused`).
        ``unix_socket_directories`` is not needed because the login stays on
        TCP: the default Debian pg_hba.conf authenticates every ``local``
        connection by ``peer``, which refuses a role with no OS account of the
        same name before any password is asked, while its ``host all all
        127.0.0.1/32 scram-sha-256`` line already admits this login.

        Returns:
            The port, remembered for the manager's lifetime once found.

        Raises:
            DatabaseQueryError: When the configured port is not a TCP port.
        """
        if self.instance is not None:
            return self.instance.port
        return self._server_port()[0]

    def _server_port(self) -> tuple[int, str]:
        """
        Resolve :meth:`server_port`, with where the answer came from.

        Returns:
            The port and a phrase naming its source, for the errors that
            have to say which port was used and why.

        Raises:
            DatabaseQueryError: When the configured port is not a TCP port.
        """
        if self._port is not None:
            return self._port
        if self.instance is not None:
            # The read-only login runs inside the container, where the
            # engine listens on its own port whatever the host publishes,
            # and the host's configuration says nothing about it.
            self._port = (self.instance.default_port, "the container's own port")
            return self._port

        settings = self.config.get("databases", {}).get("credentials", {}).get("postgresql", {})
        configured = settings.get("port") if isinstance(settings, dict) else None
        if configured is not None and configured != "":
            port = _parse_port(configured)
            if port is None:
                raise DatabaseQueryError(
                    f"Invalid PostgreSQL port in the configuration: {configured!r}",
                    details=(
                        f"Set {_PORT_SETTING} in /etc/noust/config.yaml to the port "
                        "PostgreSQL listens on, or remove it to ask the server."
                    ),
                )
            self._port = (port, f"configured in {_PORT_SETTING}")
            return self._port

        answer = self._show("port")
        port = _parse_port(answer) if answer is not None else None
        if port is None:
            self.logger.debug(
                f"PostgreSQL did not report its port ({answer!r}); using {self.DEFAULT_PORT}"
            )
            return self.DEFAULT_PORT, "PostgreSQL's default, because the server did not report one"
        self._port = (port, "the one the server reports (SHOW port)")
        return self._port

    def get_status(self) -> dict[str, Any]:
        """
        Summarise the engine's state, with the port it actually listens on.

        Returns:
            The base summary; ``port`` is :meth:`server_port` while the
            service is running.
        """
        status = super().get_status()
        if status["running"]:
            status["port"] = self.server_port()
        return status

    # ==================== Database Management ====================

    def create_database(
        self,
        name: str,
        owner: str | None = None,
        encoding: str | None = None,
        **kwargs,
    ) -> DatabaseInfo:
        """
        Create a database.

        Args:
            name: Database name.
            owner: Role that owns the database.
            encoding: Character encoding, UTF8 by default.
            **kwargs: Accepts ``template``.

        Returns:
            Information about the new database.

        Raises:
            DatabaseExistsError: When the database already exists.
            DatabaseError: When the name is invalid or creation fails.
        """
        self.validate_database_name(name)
        if self.database_exists(name):
            raise DatabaseExistsError(
                f"Database '{name}' already exists",
                details="Drop it first, or pick another name.",
            )

        encoding = encoding or "UTF8"
        template = kwargs.get("template", "template0")

        sql = (
            f"CREATE DATABASE {self._escape_identifier(name)} "
            f"ENCODING {self._escape_literal(encoding)} "
            f"TEMPLATE {self._escape_identifier(template)}"
        )
        if owner:
            self.validate_user_name(owner)
            sql += f" OWNER {self._escape_identifier(owner)}"
        sql += ";"

        success, output = self._execute_sql(sql)
        if not success:
            raise DatabaseError(f"Failed to create database '{name}'", details=output.strip())

        self.logger.info(f"Created database: {name}")
        return self.get_database_info(name)

    def drop_database(self, name: str, force: bool = False) -> None:
        """
        Drop a database.

        Args:
            name: Database name.
            force: Terminate open connections first, and accept a missing
                database as success.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseError: When the drop fails.
        """
        self.validate_database_name(name)
        if not self.database_exists(name):
            if force:
                return
            raise DatabaseNotFoundError(
                f"Database '{name}' does not exist",
                details="Run 'noust db list --engine postgresql' to see the databases.",
            )

        if force:
            self._execute_sql(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "  # noqa: S608 - quoted literal, not interpolated data
                f"WHERE datname = {self._escape_literal(name)};"
            )

        success, output = self._execute_sql(f"DROP DATABASE {self._escape_identifier(name)};")
        if not success:
            raise DatabaseError(
                f"Failed to drop database '{name}'",
                details=output.strip() or "Retry with --force to close open connections.",
            )

        self.logger.info(f"Dropped database: {name}")

    def database_exists(self, name: str) -> bool:
        """
        Report whether a database exists.

        Args:
            name: Database name.

        Returns:
            True when pg_database holds the name.
        """
        success, output = self._execute_sql(
            f"SELECT 1 FROM pg_database WHERE datname = {self._escape_literal(name)};"  # noqa: S608 - quoted literal, not interpolated data
        )
        return success and output.strip() == "1"

    def list_databases(self) -> list[DatabaseInfo]:
        """
        List the databases that do not belong to the cluster itself.

        Owner comes from the same round trip as size and encoding - the
        ``pg_roles`` join :meth:`get_database_info` already uses for one
        database, applied here to every row instead of a call per database.

        Returns:
            One entry per user database.
        """
        success, output = self._execute_sql(
            "SELECT datname, pg_encoding_to_char(encoding), pg_database_size(datname), r.rolname "
            "FROM pg_database d JOIN pg_roles r ON d.datdba = r.oid "
            "WHERE datistemplate = false;"
        )
        if not success:
            self._listing_failed("databases", output)

        databases = []
        for line in output.strip().splitlines():
            if not line:
                continue
            parts = line.split("|")
            name = parts[0]
            if name in self.SYSTEM_DATABASES:
                continue

            size = None
            if len(parts) >= 3 and parts[2].isdigit():
                size = format_size(int(parts[2]))

            databases.append(
                DatabaseInfo(
                    name=name,
                    engine=self.ENGINE_NAME,
                    encoding=parts[1] if len(parts) > 1 else None,
                    size=size,
                    owner=parts[3] if len(parts) > 3 else None,
                )
            )
        return databases

    def get_database_info(self, name: str) -> DatabaseInfo:
        """
        Describe one database.

        Args:
            name: Database name.

        Returns:
            Size, owner, encoding and table count.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
        """
        if not self.database_exists(name):
            raise DatabaseNotFoundError(f"Database '{name}' does not exist")

        literal = self._escape_literal(name)
        success, output = self._execute_sql(
            "SELECT datname, pg_encoding_to_char(encoding), pg_database_size(datname), r.rolname "  # noqa: S608 - quoted literal, not interpolated data
            "FROM pg_database d JOIN pg_roles r ON d.datdba = r.oid "
            f"WHERE datname = {literal};"
        )

        encoding: str | None = None
        size: str | None = None
        owner: str | None = None
        if success and output.strip():
            parts = output.strip().split("|")
            if len(parts) >= 4:
                encoding = parts[1]
                if parts[2].isdigit():
                    size = format_size(int(parts[2]))
                owner = parts[3]

        success, output = self._execute_sql(
            "SELECT COUNT(*) FROM information_schema.tables "  # noqa: S608 - quoted literal, not interpolated data
            f"WHERE table_schema = 'public' AND table_catalog = {literal};",
            database=name,
        )
        tables = int(output.strip()) if success and output.strip().isdigit() else 0

        return DatabaseInfo(
            name=name,
            engine=self.ENGINE_NAME,
            size=size,
            tables=tables,
            owner=owner,
            encoding=encoding,
        )

    # ==================== User Management ====================

    def create_user(
        self,
        username: str,
        password: str | None = None,
        host: str = "localhost",
        **kwargs,
    ) -> tuple[UserInfo, str]:
        """
        Create a login role.

        Args:
            username: Role name.
            password: Password. Generated when omitted.
            host: Ignored; PostgreSQL restricts hosts in pg_hba.conf.
            **kwargs: Accepts ``superuser``, ``createdb`` and ``createrole``.

        Returns:
            The role and its password.

        Raises:
            DatabaseUserError: When the role exists or creation fails.
        """
        self.validate_user_name(username)
        if self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' already exists",
                details="Drop the role first, or pick another name.",
            )

        password = password or self.generate_password()

        options = ["LOGIN"]
        if kwargs.get("superuser"):
            options.append("SUPERUSER")
        if kwargs.get("createdb"):
            options.append("CREATEDB")
        if kwargs.get("createrole"):
            options.append("CREATEROLE")

        sql = (
            f"CREATE ROLE {self._escape_identifier(username)} WITH {' '.join(options)} "
            f"PASSWORD {self._escape_literal(password)};"
        )
        success, output = self._execute_sql(sql, secrets=(password,))
        if not success:
            raise DatabaseUserError(f"Failed to create user '{username}'", details=output.strip())

        self.logger.info(f"Created user: {username}")
        return UserInfo(username=username, engine=self.ENGINE_NAME, host=host), password

    def drop_user(self, username: str, host: str = "localhost") -> None:
        """
        Drop a role.

        Args:
            username: Role name.
            host: Ignored; PostgreSQL restricts hosts in pg_hba.conf.

        Raises:
            DatabaseUserError: When the role is missing or still owns objects.
        """
        self.validate_user_name(username)
        if not self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Run 'noust db users --engine postgresql' to see the roles.",
            )

        success, output = self._execute_sql(f"DROP ROLE {self._escape_identifier(username)};")
        if not success:
            raise DatabaseUserError(
                f"Failed to drop user '{username}'",
                details=(
                    output.strip()
                    or "The role may still own objects; reassign them with REASSIGN OWNED."
                ),
            )

        self.logger.info(f"Dropped user: {username}")

    def user_exists(self, username: str, host: str = "localhost") -> bool:
        """
        Report whether a role exists.

        Args:
            username: Role name.
            host: Ignored; PostgreSQL restricts hosts in pg_hba.conf.

        Returns:
            True when pg_roles holds the name.
        """
        success, output = self._execute_sql(
            f"SELECT 1 FROM pg_roles WHERE rolname = {self._escape_literal(username)};"  # noqa: S608 - quoted literal, not interpolated data
        )
        return success and output.strip() == "1"

    def list_users(self) -> list[UserInfo]:
        """
        List the roles that are not internal to PostgreSQL.

        Which databases a role can connect to comes from the same query, one
        row per role rather than one query per role: ``has_database_privilege``
        is evaluated for every role/database pair and folded into a single
        comma-separated column with ``array_agg`` ... ``FILTER``, so a cluster
        with a hundred roles still costs one round trip, not a hundred.

        Returns:
            One entry per role, with its cluster-wide attributes and the
            databases it may connect to.
        """
        success, output = self._execute_sql(
            "SELECT r.rolname, r.rolsuper, r.rolcreatedb, r.rolcreaterole, "
            "COALESCE(array_to_string(array_agg(d.datname) "
            "FILTER (WHERE has_database_privilege(r.rolname, d.datname, 'CONNECT')), ','), '') "
            "FROM pg_roles r CROSS JOIN pg_database d "
            "WHERE r.rolname NOT LIKE 'pg\\_%' AND d.datistemplate = false "
            "GROUP BY r.rolname, r.rolsuper, r.rolcreatedb, r.rolcreaterole "
            "ORDER BY r.rolname;"
        )
        if not success:
            self._listing_failed("users", output)

        users = []
        for line in output.strip().splitlines():
            if not line:
                continue
            parts = line.split("|")
            attributes = []
            for index, name in ((1, "SUPERUSER"), (2, "CREATEDB"), (3, "CREATEROLE")):
                if len(parts) > index and parts[index] == "t":
                    attributes.append(name)
            databases = parts[4].split(",") if len(parts) > 4 and parts[4] else []
            users.append(
                UserInfo(
                    username=parts[0],
                    engine=self.ENGINE_NAME,
                    privileges=attributes,
                    databases=databases,
                )
            )
        return users

    def grant_privileges(
        self,
        username: str,
        database: str,
        privileges: Sequence[str] | None = None,
        host: str = "localhost",
    ) -> None:
        """
        Grant whitelisted privileges on a database and on its public schema.

        A privilege is applied where PostgreSQL accepts it: CONNECT and CREATE
        on the database, SELECT and friends on the tables.

        Args:
            username: Role name.
            database: Database name.
            privileges: Privileges to grant. ALL PRIVILEGES when omitted.
            host: Ignored; PostgreSQL restricts hosts in pg_hba.conf.

        Raises:
            DatabaseUserError: When a privilege is not whitelisted or the grant
                fails.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        granted = self.validate_privileges(privileges)

        role = self._escape_identifier(username)
        for statement, target_database in self._privilege_statements(
            "GRANT", granted, database, role, "TO"
        ):
            success, output = self._execute_sql(statement, database=target_database)
            if not success:
                raise DatabaseUserError(
                    f"Failed to grant privileges on '{database}' to '{username}'",
                    details=output.strip(),
                )

        self.logger.info(f"Granted {', '.join(granted)} on {database} to {username}")

    def revoke_privileges(
        self,
        username: str,
        database: str,
        privileges: Sequence[str] | None = None,
        host: str = "localhost",
    ) -> None:
        """
        Revoke whitelisted privileges on a database and on its public schema.

        Args:
            username: Role name.
            database: Database name.
            privileges: Privileges to revoke. ALL PRIVILEGES when omitted.
            host: Ignored; PostgreSQL restricts hosts in pg_hba.conf.

        Raises:
            DatabaseUserError: When a privilege is not whitelisted or the revoke
                fails.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        revoked = self.validate_privileges(privileges)

        role = self._escape_identifier(username)
        for statement, target_database in self._privilege_statements(
            "REVOKE", revoked, database, role, "FROM"
        ):
            success, output = self._execute_sql(statement, database=target_database)
            if not success:
                raise DatabaseUserError(
                    f"Failed to revoke privileges on '{database}' from '{username}'",
                    details=output.strip(),
                )

        self.logger.info(f"Revoked {', '.join(revoked)} on {database} from {username}")

    def _privilege_statements(
        self,
        verb: str,
        privileges: Sequence[str],
        database: str,
        role: str,
        preposition: str,
    ) -> list[tuple[str, str]]:
        """
        Split privileges over the object types that accept them.

        Args:
            verb: ``GRANT`` or ``REVOKE``.
            privileges: Already whitelisted privileges.
            database: Database name.
            role: Quoted role identifier.
            preposition: ``TO`` for a grant, ``FROM`` for a revoke.

        Returns:
            Pairs of statement and the database it must run against.
        """
        statements = []
        database_privileges = [p for p in privileges if p in DATABASE_PRIVILEGES]
        table_privileges = [p for p in privileges if p in TABLE_PRIVILEGES]

        if database_privileges:
            statements.append(
                (
                    f"{verb} {', '.join(database_privileges)} ON DATABASE "
                    f"{self._escape_identifier(database)} {preposition} {role};",
                    "postgres",
                )
            )
        if table_privileges:
            statements.append(
                (
                    f"{verb} {', '.join(table_privileges)} ON ALL TABLES IN SCHEMA public "
                    f"{preposition} {role};",
                    database,
                )
            )
        return statements

    # ==================== Backup & Restore ====================

    def backup(
        self,
        database: str,
        output_path: Path | None = None,
        compress: bool = True,
        **kwargs,
    ) -> BackupInfo:
        """
        Dump a database with pg_dump, in the custom format unless told otherwise.

        A custom-format dump is compressed by pg_dump itself, so a dump Noust
        names is not gzipped a second time; one written to a path the caller
        chose (an application backup's archive) is, when asked, since the
        caller named the file for it.

        Args:
            database: Database name.
            output_path: Custom destination.
            compress: Pipe the dump through gzip. Ignored for a custom-format
                dump Noust names itself.
            **kwargs: Accepts ``format`` (plain, custom or tar) and ``schemas``.

        Returns:
            Information about the backup.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseBackupError: When the dump fails.
        """
        self.validate_database_name(database)
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")

        schemas = kwargs.get("schemas")
        if schemas:
            backups = [
                self.backup_schema(database, schema, compress=compress) for schema in schemas
            ]
            return backups[-1]

        dump_format = kwargs.get("format") or DEFAULT_DUMP_FORMAT
        if dump_format not in DUMP_FORMATS:
            raise DatabaseBackupError(
                f"Unsupported pg_dump format: {dump_format}",
                details=f"Use one of: {', '.join(sorted(DUMP_FORMATS))}.",
            )
        if dump_format == "custom" and output_path is None:
            compress = False

        destination = self._backup_path(
            database, output_path, compress, suffix=DUMP_SUFFIXES[dump_format]
        )
        return self._dump_to_file(
            self._pg_dump_argv(database, dump_format),
            destination,
            database=database,
            compress=compress,
            user=self.SUPERUSER,
        )

    def _pg_dump_argv(
        self, database: str, dump_format: str, schema: str | None = None
    ) -> list[str]:
        """
        Build a pg_dump invocation that writes to stdout.

        Runs as :data:`SUPERUSER`, applied by the caller through
        ``_dump_to_file``'s ``user=``; see :meth:`_psql_argv` for why.

        Args:
            database: Database name.
            dump_format: One of :data:`DUMP_FORMATS`.
            schema: Restrict the dump to this schema.

        Returns:
            The argument vector.
        """
        argv = [
            "pg_dump",
            "--no-password",
            f"--format={dump_format}",
        ]
        if schema is not None:
            argv.append(f"--schema={schema}")
        argv.append(database)
        return argv

    def list_schemas(self, database: str) -> list[str]:
        """
        List the schemas a database holds, excluding the system ones.

        Args:
            database: Database name.

        Returns:
            Schema names, sorted.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
        """
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")

        success, output = self._execute_sql(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT LIKE 'pg\\_%' AND schema_name != 'information_schema' "
            "ORDER BY schema_name;",
            database=database,
        )
        if not success:
            self._listing_failed("schemas", output)
        return [line.strip() for line in output.strip().splitlines() if line.strip()]

    def backup_schema(
        self,
        database: str,
        schema: str,
        output_path: Path | None = None,
        compress: bool = True,
    ) -> BackupInfo:
        """
        Dump a single schema.

        Args:
            database: Database name.
            schema: Schema to dump.
            output_path: Custom destination.
            compress: Pipe the dump through gzip.

        Returns:
            Information about the backup.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseBackupError: When the dump fails.
        """
        self.validate_database_name(database)
        validate_name(schema, kind="schema", engine=self.DISPLAY_NAME, max_length=63)
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")

        destination = self._backup_path(
            database, output_path, compress, label=f"{database}-{schema}", suffix=".sql"
        )
        info = self._dump_to_file(
            self._pg_dump_argv(database, "plain", schema=schema),
            destination,
            database=database,
            compress=compress,
            user=self.SUPERUSER,
        )
        info.database = f"{database}/{schema}"
        return info

    def backup_all_schemas(
        self,
        database: str,
        output_dir: Path | None = None,
        compress: bool = True,
    ) -> list[BackupInfo]:
        """
        Dump every non-system schema of a database into its own file.

        Args:
            database: Database name.
            output_dir: Directory for the backup files.
            compress: Pipe each dump through gzip.

        Returns:
            One entry per schema that was dumped.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
        """
        schemas = self.list_schemas(database)
        if not schemas:
            self.logger.info(f"No user schemas found in {database}")
            return []

        backups = []
        for schema in schemas:
            destination = None
            if output_dir is not None:
                base = self._backup_path(
                    database, None, compress, label=f"{database}-{schema}", suffix=".sql"
                )
                destination = Path(output_dir) / base.name
            try:
                backups.append(
                    self.backup_schema(database, schema, output_path=destination, compress=compress)
                )
            except DatabaseBackupError as exc:
                self.logger.warning(f"Failed to backup schema {schema}: {exc}")
        return backups

    def _dump_format(self, backup_path: Path, requested: str | None = None) -> str:
        """
        Tell a custom-format or tar dump from a plain SQL one.

        By the file's own first bytes, not its name: an application backup
        written before 3.1 names a custom dump ``.sql.gz`` as readily as a
        plain one, and the name is only a hint. A gzipped file is read
        through gzip.

        Args:
            backup_path: The dump.
            requested: A format the caller insists on, which wins.

        Returns:
            ``plain``, ``custom`` or ``tar``.

        Raises:
            DatabaseBackupError: When the file cannot be read.
        """
        if requested in DUMP_FORMATS:
            return str(requested)
        try:
            opener = gzip.open if backup_path.suffix == ".gz" else open
            with opener(backup_path, "rb") as handle:
                head = handle.read(_TAR_MAGIC_OFFSET + 5)
        except (OSError, EOFError) as exc:
            raise DatabaseBackupError(
                f"Could not read the dump {backup_path}",
                details=f"{exc}. Check that the file is a complete dump.",
            ) from exc
        if head.startswith(CUSTOM_FORMAT_SIGNATURE):
            return "custom"
        if head[_TAR_MAGIC_OFFSET:].startswith(b"ustar"):
            return "tar"
        return "plain"

    def _check_backup(self, backup_path: Path, **kwargs: Any) -> None:
        """
        Refuse a plain dump in which psql would find a meta-command.

        A restore trusts the dump's SQL: it runs as :data:`SUPERUSER`, which
        is what ``ALTER ... OWNER TO``, ``CREATE EXTENSION`` and the rest of a
        real dump need, and which also includes ``COPY ... TO PROGRAM``. That
        is why it is an elevated, audited action. What it does not trust is
        the client: psql reads a plain dump with ``-f``, where it would run
        ``\\!``, ``\\o`` or ``\\set`` as happily as SQL, so the dump is checked
        by :func:`~noust.managers.database.psql_script.check_plain_dump` first,
        before the database is touched. pg_restore, for the custom and tar
        formats, has no such commands.

        Args:
            backup_path: The dump.
            **kwargs: Accepts ``format`` to override the detected one.

        Raises:
            DatabaseBackupError: When a plain dump holds a meta-command.
        """
        if self._dump_format(backup_path, kwargs.get("format")) == "plain":
            check_plain_dump(backup_path)

    @contextmanager
    def _restore_input(self, database: str, backup_path: Path, **kwargs: Any) -> Iterator[Path]:
        """
        Prepare a dump for psql or pg_restore, before anything is dropped.

        On the host they open the file themselves, as the postgres account,
        so the 0600 root-owned backup is staged for that account. Inside a
        container the dump is their stdin: a plain file is used where it is
        and a gzipped one decompressed on the host.

        Args:
            database: The database it will be loaded into.
            backup_path: The dump, plain or gzipped.
            **kwargs: Accepts ``format`` to override the detected one.

        Yields:
            The dump as the loader reads it.

        Raises:
            DatabaseBackupError: When it cannot be read or staged.
        """
        dump_format = self._dump_format(backup_path, kwargs.get("format"))
        staged_name = (
            f"{storage_name(self.ENGINE_NAME)}-restore-{database}{DUMP_SUFFIXES[dump_format]}"
        )
        owner = None if self.instance is not None else self.SUPERUSER
        with self._staged_backup(backup_path, staged_name, owner=owner) as staged:
            yield staged

    def _staged_format(self, path: Path) -> str | None:
        """
        Read the format :meth:`_restore_input` found back from the name it staged under.

        The staged copy is named after the format read from the original's
        first bytes (:data:`DUMP_SUFFIXES`), so it is not read twice.

        Args:
            path: The dump the loader is given.

        Returns:
            The format, or None for a file that is not a staged copy.
        """
        if path.parent != self.BACKUP_DIR / ".staging":
            return None
        return next(
            (kind for kind, suffix in DUMP_SUFFIXES.items() if path.name.endswith(suffix)), None
        )

    def _load_backup(self, database: str, backup_path: Path, **kwargs: Any) -> None:
        """
        Load a plain dump with psql or an archive with pg_restore.

        Args:
            database: The database to load into.
            backup_path: The dump as :meth:`_restore_input` prepared it.
            **kwargs: Accepts ``format`` to override the detected one.

        Raises:
            DatabaseBackupError: When psql or pg_restore fails or runs out of
                time; the error carries its output.
        """
        dump_format = self._dump_format(
            backup_path, kwargs.get("format") or self._staged_format(backup_path)
        )
        timeout = restore_timeout(backup_path)
        if self.instance is not None:
            # A client inside a container cannot open a file on the host: the
            # dump is its stdin, which psql (-f -) and pg_restore (no file)
            # both read.
            if dump_format == "plain":
                argv = self._psql_argv(database, "-f", "-")
            else:
                argv = ["pg_restore", "--no-password", "-d", database]
            result = self._exec(argv, stdin_path=backup_path, timeout=timeout, user=self.SUPERUSER)
        else:
            if dump_format == "plain":
                argv = self._psql_argv(database, "-f", str(backup_path))
            else:
                argv = ["pg_restore", "--no-password", "-d", database, str(backup_path)]
            result = self._exec(argv, timeout=timeout, user=self.SUPERUSER)

        if not result.success:
            raise load_failure(database, result, "The dump may be truncated or in another format.")

    # ==================== Query Execution ====================

    def execute_query(
        self,
        database: str,
        query: str,
        *,
        read_only: bool = False,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Run an arbitrary statement against a database.

        Args:
            database: Database name.
            query: The statement.
            read_only: Refuse anything that would change data. Enforcement is
                the server's, not a keyword allowlist: PostgreSQL lets a
                data-modifying CTE hide behind a leading ``WITH``, so
                ``WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x`` reads
                as a SELECT to any parser simple enough to be trustworthy. The
                session is signed in as ``wasm_ro_<database>``; see
                :meth:`_run_console`.
            **kwargs: Unused.

        Returns:
            Success and the statement's output.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the statement is refused before it runs
                (see :meth:`_console_statement`), the read-only role cannot
                sign in, or the statement fails.
        """
        result = self._run_console(database, query, read_only=read_only, csv=False)
        if not result.success:
            raise DatabaseQueryError("Query failed", details=result.stderr.strip())
        return True, result.stdout

    def _console_statement(self, database: str, query: str, *, read_only: bool) -> str:
        """
        Check an operator's console statement before any psql runs it.

        psql parses stdin, ``-f`` files and psqlrc as scripts, running a
        meta-command wherever one appears: ``\\! id`` is a shell even in the
        middle of a line, ``\\o`` writes a file, ``\\i`` reads one. A ``-c``
        string is different - psql sends it to the server as it is, with no
        meta-command or variable handling - unless its very first character
        is a backslash, in which case it is one meta-command. So the statement
        travels as its own ``-c``, a leading backslash is refused here, and
        ``-X`` keeps psqlrc out.

        The trade-off is that the statement is in argv, visible in ``ps`` for
        as long as it runs. That is acceptable for the operator's own query,
        which is not a credential. A statement that embeds a password
        (``ALTER ROLE ... PASSWORD``) is better sent through ``noust db user``,
        which keeps it on stdin.

        Args:
            database: Database the statement is for.
            query: The operator's statement.
            read_only: Whether it runs in read mode, which accepts one
                statement only (see :func:`console_statement`).

        Returns:
            The statement, normalised by :func:`console_statement`.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the statement is refused.
        """
        statement = console_statement(query, read_only=read_only)
        if statement.lstrip().startswith("\\"):
            raise DatabaseQueryError(
                "psql client commands are not accepted by the console",
                details=(
                    "A statement starting with a backslash is a psql meta-command "
                    "(\\! runs a shell, \\o writes a file), not SQL. Send SQL only; "
                    "use 'noust db connect' for an interactive psql session."
                ),
            )
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")
        return statement

    def _run_console(
        self,
        database: str,
        query: str,
        *,
        read_only: bool,
        csv: bool,
        timeout_s: int | None = None,
    ) -> CommandResult:
        """
        Run an operator's console statement, in write or read mode.

        Write mode runs the statement as :data:`SUPERUSER` over the peer
        socket, like every other operation here: it is sudo-gated, and it is
        meant to be able to do anything.

        Read mode never touches the superuser session. It signs in over TCP
        to :data:`READ_ONLY_HOST` as ``wasm_ro_<database>``, a role that is
        not a superuser and holds only ``CONNECT``, ``USAGE`` and ``SELECT``,
        and runs the statement in a read-only transaction, one ``-c`` per
        statement (psql before 15 prints only the last result of a
        multi-statement ``-c``, which would be COMMIT's empty one). An earlier
        build connected as the superuser and ran ``SET ROLE`` first, which
        is no limit at all: the session user is still a superuser, so
        ``SELECT set_config('role', 'postgres', true), query_to_xml('select
        pg_read_file(...)', ...)`` put superuser back inside one read-only
        SELECT, and ``RESET ROLE`` or ``SET SESSION AUTHORIZATION`` would
        have done the same. Signing in as the role leaves the server nothing
        to switch back to.

        The port is :meth:`server_port`'s. A password the server rejects is
        rotated once and the sign-in retried: that heals a stored password
        that no longer matches, such as after two first reads raced to set it.
        Any other refusal - pg_hba.conf allowing no password login on the
        loopback, the server not listening on TCP, another PostgreSQL
        answering on the port - is reported with psql's own output and the
        fix it points at, and never answered by falling back to the superuser
        session.

        Args:
            database: Database to connect to.
            query: The operator's statement.
            read_only: Run it in read mode.
            csv: Print CSV with a header row, for the structured result.
            timeout_s: Seconds the server may spend on the statement; the
                server's own setting when None.

        Returns:
            psql's result.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the statement is refused, the read-only
                role cannot be provisioned, or it cannot sign in.
        """
        statement = self._console_statement(database, query, read_only=read_only)
        if not read_only:
            argv = self._console_argv(database, ["-c", statement], csv=csv)
            options = _session_options(timeout_s, read_only=False)
            return self._exec(
                argv,
                env={"PGOPTIONS": options} if options else None,
                timeout=query_deadline(timeout_s),
                user=self.SUPERUSER,
            )

        tail = [
            arg for command in ("BEGIN READ ONLY", statement, "COMMIT") for arg in ("-c", command)
        ]
        # -q drops the BEGIN/COMMIT status lines the wrapper would otherwise
        # print around the rows (--csv carries its own).
        return self._read_only_exec(
            database, [*([] if csv else ["-q"]), *tail], csv=csv, timeout_s=timeout_s
        )

    def _read_only_exec(
        self,
        database: str,
        tail: Sequence[str],
        *,
        csv: bool,
        timeout_s: int | None = None,
        input: str | None = None,
        reuse: bool = False,
    ) -> CommandResult:
        """
        Sign in as the database's read-only role and run psql with ``tail``.

        The one login every read-only path takes: the console, the data
        explorer and EXPLAIN. The port is :meth:`server_port`'s. A password
        the server rejects is rotated once and the sign-in retried: that heals
        a stored password that no longer matches, such as after two first
        reads raced to set it. Any other refusal is reported with psql's own
        output and the fix it points at (:meth:`_sign_in_refused`), and never
        answered by falling back to the superuser session.

        Args:
            database: Database to connect to.
            tail: What follows the connection options: ``-c`` strings, or
                ``-f -`` with ``input``.
            csv: Print CSV with a header row.
            timeout_s: Seconds the server may spend on each statement.
            input: The script, when ``tail`` reads it from stdin.
            reuse: Skip provisioning when this process provisioned the role
                in the last :data:`_PROVISION_REUSE_SECONDS`. A login or a
                grant that turns out stale is provisioned again and retried.

        Returns:
            psql's result.

        Raises:
            DatabaseQueryError: When the role cannot be provisioned or cannot
                sign in.
        """
        # Resolved before the role is provisioned, so a server that cannot
        # report its port is not asked between provisioning and the login.
        port, source = self._server_port()
        role = _read_only_role_name(database)
        provisioned_key = f"{self.ENGINE_NAME}/{database}"
        password = self._reusable_password(database, role) if reuse else None
        # Whether this call provisioned: a failure before it may be staleness.
        provisioned = password is None
        rotate = False
        env_options = _session_options(timeout_s, read_only=True) or _READ_ONLY_OPTIONS
        while True:
            if password is None:
                role, password = self._ensure_read_only_role(database, rotate=rotate)
                _provisioned_at[provisioned_key] = time.monotonic()
            argv = self._console_argv(
                database,
                [
                    "-h",
                    READ_ONLY_HOST,
                    "-p",
                    str(port),
                    "-U",
                    role,
                    # Never prompt: a password psql asks the terminal for would
                    # hang the CLI and cannot be answered by the web console.
                    "-w",
                    *tail,
                ],
                csv=csv,
            )
            env = {
                "PGPASSWORD": password,
                "PGOPTIONS": env_options,
                "PGCONNECT_TIMEOUT": "10",
            }
            result = self._exec(
                argv,
                env=env,
                input=input,
                timeout=query_deadline(timeout_s),
                secrets=(password,),
            )
            if result.success:
                return result
            if not provisioned and (_sign_in_failed(result) or _PERMISSION_DENIED in result.stderr):
                # The role this process provisioned a moment ago went stale:
                # a password set elsewhere, a table created since.
                password, provisioned = None, True
                continue
            if not _sign_in_failed(result):
                return result
            if not rotate and "password authentication failed" in result.stderr:
                self.logger.info(
                    f"PostgreSQL rejected the stored password of {role}; setting a new one"
                )
                password, rotate = None, True
                continue
            break
        raise self._sign_in_refused(database, role, result, port=port, source=source)

    def _reusable_password(self, database: str, role: str) -> str | None:
        """
        Find the stored password of a role this process provisioned recently.

        Args:
            database: The database the role is for.
            role: The role.

        Returns:
            The password, or None when the role must be provisioned first.
        """
        provisioned = _provisioned_at.get(f"{self.ENGINE_NAME}/{database}")
        if provisioned is None or time.monotonic() - provisioned > _PROVISION_REUSE_SECONDS:
            return None
        return self._stored_password(self._read_only_password_file(role))

    def _console_argv(self, database: str, tail: Sequence[str], *, csv: bool) -> list[str]:
        """
        Build the psql invocation for a console statement.

        Args:
            database: Database to connect to.
            tail: Connection options and the ``-c`` strings.
            csv: Print CSV with a header row instead of bare tuples.

        Returns:
            The argument vector.
        """
        if csv:
            return self._psql_csv_argv(database, *tail)
        return self._psql_argv(database, *tail)

    def _sign_in_refused(
        self, database: str, role: str, result: CommandResult, *, port: int, source: str
    ) -> DatabaseQueryError:
        """
        Build the error for a read-only console that could not sign in.

        The fix is chosen from what psql said, and only that: pg_hba.conf is
        suggested when the server says it has no line for the login, not for
        every refusal.

        Args:
            database: Database the console was reading.
            role: The read-only role.
            result: psql's failed result.
            port: The port the login used.
            source: Where that port came from, as :meth:`_server_port` names it.

        Returns:
            An error that says where the login went, carries psql's own output
            verbatim and gives the fix psql's words point at.
        """
        stderr = result.stderr
        where = f"{READ_ONLY_HOST}:{port}"
        origin = f"Port {port} is {source}."
        elsewhere = (
            f"If the PostgreSQL Noust administers listens on another port, set {_PORT_SETTING} "
            "in /etc/noust/config.yaml to it."
        )
        if "no pg_hba.conf entry" in stderr:
            hba_line = f"host {database} {role} {READ_ONLY_HOST}/32 scram-sha-256"
            steps = (
                "Read mode signs in as its own least-privilege role with a password "
                f"over {READ_ONLY_HOST}, and never falls back to the superuser. Add this "
                "line to pg_hba.conf, above any broader 'host' line, then reload "
                f"PostgreSQL (systemctl reload postgresql):\n  {hba_line}"
            )
        elif "password authentication failed" in stderr or _ROLE_MISSING.search(stderr):
            steps = (
                f"The server answering on {where} does not know {role}, or rejects the "
                "password Noust set for it on the cluster it administers, so another "
                f"PostgreSQL (a container publishing port {port}, a second cluster) is "
                f"probably answering on port {port}. {origin} Set {_PORT_SETTING} in "
                "/etc/noust/config.yaml to the port of the cluster Noust administers: "
                "'SHOW port' in 'noust db connect' prints it."
            )
        elif "Connection refused" in stderr:
            reported = self._show("listen_addresses")
            current = (
                f" PostgreSQL reports listen_addresses = '{reported}'."
                if reported is not None
                else ""
            )
            steps = (
                f"Nothing accepts TCP connections on {where}. {origin}{current} Set "
                "listen_addresses = 'localhost' in postgresql.conf and restart PostgreSQL "
                f"(systemctl restart postgresql). {elsewhere}"
            )
        else:
            steps = (
                f"psql could not open a session on {where} as {role}; its own output "
                f"follows. {origin} {elsewhere}"
            )
        return DatabaseQueryError(
            f"The read-only console could not sign in to PostgreSQL as {role} on {where}",
            details=steps,
            output=(stderr or result.stdout).strip(),
        )

    def _psql_csv_argv(self, database: str, *tail: str) -> list[str]:
        """
        Build a psql invocation that prints CSV with a header row.

        Every other invocation in this module uses :meth:`_psql_argv`'s
        ``-t -A`` (tuples only, unaligned): headerless, which is exactly
        wrong for the console's structured result - it needs the header
        ``--csv`` prints to know what to call each column.

        Args:
            database: Database to connect to.
            *tail: Arguments describing where the SQL comes from.

        Returns:
            The argument vector.
        """
        return [
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            database,
            "--csv",
            "-q",
            "-X",
            # --csv prints a NULL and an empty string alike; the marker tells
            # them apart (execute_query_structured() turns it into None).
            "-P",
            f"null={NULL_MARKER}",
            *tail,
        ]

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
        Run a statement once and parse its CSV output into columns and rows.

        One execution, not two: the plain-text ``output`` this returns is the
        same client invocation's stdout, in CSV form. Running the query a
        second time to also produce the older unaligned format would apply a
        write statement twice, which is not a trade a console is allowed to
        make for a nicer legacy text field.

        Args:
            database: Database name.
            query: The statement.
            read_only: Same enforcement as :meth:`execute_query`: a session
                signed in as the least-privilege role, inside a read-only
                transaction. See :meth:`_run_console`.
            max_rows: Data rows kept before the rest are dropped.
            timeout_s: Seconds the server may spend on the statement
                (``statement_timeout``); the server's own setting when None.

        Returns:
            The parsed result, a NULL as None and an empty string as ``""``.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the statement is refused before it runs
                (see :meth:`_console_statement`), the read-only role cannot
                sign in, or the statement fails.
        """
        result = self._run_console(
            database, query, read_only=read_only, csv=True, timeout_s=timeout_s
        )
        if not result.success:
            raise DatabaseQueryError(
                "Query failed", details=(result.stderr or result.stdout).strip()
            )

        columns, parsed, truncated = parse_tabular_query_output(
            result.stdout, delimiter=",", max_rows=max_rows
        )
        rows: list[list[str | None]] = [
            [None if cell == NULL_MARKER else cell for cell in row] for row in parsed
        ]
        return StructuredQueryResult(
            output=result.stdout.replace(NULL_MARKER, ""),
            columns=columns,
            rows=rows,
            row_count=len(rows),
            duration_ms=result.duration * 1000,
            truncated=truncated,
        )

    # ==================== Statements Noust builds (3.1) ====================

    def run_sql(
        self, database: str, sql: str, *, read_only: bool, timeout_s: int | None = None
    ) -> str:
        """
        Run a statement Noust built - the data explorer's, the row editor's,
        the metrics' - and return what it printed.

        The text travels on stdin, like every other statement of this module:
        it can carry values an operator typed (a filter, a row's new
        password hash), and argv is visible in ``ps``. It is never the
        operator's own SQL, which only ever goes through the console's ``-c``
        (:meth:`_console_statement` says why): values reach it as literals
        :mod:`noust.managers.database.dialects` built.

        Args:
            database: Database to connect to.
            sql: The statement or script. Read mode wraps it in a read-only
                transaction; write mode runs it as it is.
            read_only: Sign in as the database's read-only role
                (:meth:`_read_only_exec`, reusing a recent provisioning);
                otherwise run as the cluster superuser.
            timeout_s: Seconds the server may spend on each statement.

        Returns:
            psql's output: tuples only, unaligned, no status lines.

        Raises:
            DatabaseQueryError: When the statement fails, with psql's own
                output; or the read-only role cannot sign in.
        """
        tail = ["-q", "-f", "-"]
        if read_only:
            script = f"BEGIN READ ONLY;\n{sql.rstrip().rstrip(';')};\nCOMMIT;\n"
            result = self._read_only_exec(
                database, tail, csv=False, timeout_s=timeout_s, input=script, reuse=True
            )
        else:
            options = _session_options(timeout_s, read_only=False)
            result = self._exec(
                self._psql_argv(database, *tail),
                input=sql,
                env={"PGOPTIONS": options} if options else None,
                timeout=query_deadline(timeout_s),
                user=self.SUPERUSER,
            )
        if not result.success:
            raise DatabaseQueryError(
                f"PostgreSQL refused a statement on '{database}'",
                details="PostgreSQL's own message follows.",
                output=(result.stderr or result.stdout).strip(),
            )
        return result.stdout

    def explain(
        self, database: str, statement: str, *, analyze: bool = False, timeout_s: int | None = None
    ) -> str:
        """
        Show how PostgreSQL would run an operator's statement, as JSON.

        Plain EXPLAIN runs read-only, signed in as the read-only role, like
        the console's read mode. ``analyze`` executes the statement to time
        it, so it runs as the superuser, like the console's write mode - and
        inside a transaction that is rolled back, so an ``EXPLAIN ANALYZE
        DELETE`` measures the delete without keeping it. The statement is
        checked exactly as a console statement is (one statement, no psql
        meta-command) and travels as its own ``-c``.

        Args:
            database: The database.
            statement: The operator's statement.
            analyze: Execute it and report real timings.
            timeout_s: Seconds the server may spend on it.

        Returns:
            The plan, as the JSON text EXPLAIN printed.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the statement is refused or fails.
        """
        checked = self._console_statement(database, statement, read_only=True)
        if analyze:
            explained = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {checked}"
            options = _session_options(timeout_s, read_only=False)
            result = self._exec(
                self._psql_argv(database, "-q", "-c", "BEGIN", "-c", explained, "-c", "ROLLBACK"),
                env={"PGOPTIONS": options} if options else None,
                timeout=query_deadline(timeout_s),
                user=self.SUPERUSER,
            )
        else:
            explained = f"EXPLAIN (FORMAT JSON) {checked}"
            result = self._read_only_exec(
                database,
                ["-q", "-c", "BEGIN READ ONLY", "-c", explained, "-c", "COMMIT"],
                csv=False,
                timeout_s=timeout_s,
                reuse=True,
            )
        if not result.success:
            raise DatabaseQueryError(
                "EXPLAIN failed", details=(result.stderr or result.stdout).strip()
            )
        return result.stdout

    # ==================== Read-only console role ====================

    def _read_only_password_file(self, role: str) -> Path:
        """
        Return where a read-only role's password is kept.

        Beside the store, like the lock files and the job logs: under
        ``/var/lib/noust`` on a server, inside the test's own directory in a
        test, inside the sandbox under ``scripts/console_server.py``.

        Args:
            role: The read-only role.

        Returns:
            The path of its password file.
        """
        # Imported here: the store imports a great deal, and nothing else in
        # this module needs it.
        from noust.core.store import get_store

        # A container's cluster has roles of the same name as the host's and
        # passwords of its own.
        prefix = f"{storage_name(self.ENGINE_NAME)}." if self.instance is not None else ""
        return get_store().db_path.parent.joinpath(
            *_READ_ONLY_SECRETS_PATH, f"{prefix}{role}.password"
        )

    def _stored_password(self, path: Path) -> str | None:
        """
        Read a read-only role's stored password, if Noust wrote a usable one.

        The file is opened without following a symlink and must be a regular
        file owned by this user with no group or other access; anything else
        was not left by :func:`~noust.core.config.secure_write` and is
        replaced, not trusted.

        Args:
            path: The password file.

        Returns:
            The password, or None when there is none to use.
        """
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as exc:
            self.logger.warning(f"Not using the read-only console password at {path}: {exc}")
            return None
        with os.fdopen(fd, encoding="ascii", errors="replace") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                self.logger.warning(
                    f"Not using the read-only console password at {path}: it is not a "
                    "private file of this user; a new one will be set"
                )
                return None
            value = handle.read(256).strip()
        return value if _STORED_PASSWORD.fullmatch(value) else None

    def _ensure_read_only_role(self, database: str, *, rotate: bool = False) -> tuple[str, str]:
        """
        Create, idempotently, the role the read-only console signs in as.

        The role is ``NOSUPERUSER`` and holds nothing but ``CONNECT`` on this
        database, ``USAGE`` on its schemas and ``SELECT`` on its tables -
        re-granted on every call, so a table created after the role's first
        use is covered by the next read instead of needing a separate
        migration step. Its attributes are re-applied on every call too, and
        ``default_transaction_read_only`` is set on the role itself, so a
        session signed in with its password is read-only even without the
        console's own ``BEGIN READ ONLY``.

        It can sign in (``LOGIN``) with a random password kept in a 0600 file
        beside the store (:meth:`_read_only_password_file`). The password is
        set - as a SCRAM verifier, never in clear - when there is no stored
        one, which is also how a role provisioned by an earlier 2.0 build,
        created ``NOLOGIN`` and reached with ``SET ROLE``, gets one; and when
        ``rotate`` asks for it. The server is told first and the file written
        after, so a failure in between leaves a role whose password nobody
        knows and the next call sets a new one, never a stored password the
        role does not have.

        Args:
            database: The database the role is scoped to.
            rotate: Set a new password even when one is stored.

        Returns:
            The role name and its password.

        Raises:
            DatabaseQueryError: When the role cannot be created, granted or
                given its password, or the password cannot be stored.
        """
        role = _read_only_role_name(database)
        role_literal = self._escape_literal(role)
        role_identifier = self._escape_identifier(role)
        path = self._read_only_password_file(role)

        password = None if rotate else self._stored_password(path)
        verifier = None
        if password is None:
            password = secrets.token_urlsafe(32)
            verifier = _scram_sha256_verifier(password)
        set_password = f" PASSWORD {self._escape_literal(verifier)}" if verifier else ""
        role_tag = _dollar_tag("wasm_ro_role", role_literal)
        grants_tag = _dollar_tag("wasm_ro_grants", role_literal)

        provision = (
            f"DO {role_tag}\n"  # noqa: S608 - quoted literal, not interpolated data
            "BEGIN\n"
            f"  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = {role_literal}) "
            "THEN\n"
            # Created without LOGIN and given it below with its password, so
            # a role whose ALTER failed halfway is one nobody can sign in as.
            f"    CREATE ROLE {role_identifier} NOLOGIN;\n"
            "  END IF;\n"
            "END\n"
            f"{role_tag};\n"
            f"ALTER ROLE {role_identifier} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
            f"NOREPLICATION NOBYPASSRLS LOGIN{set_password};\n"
            f"ALTER ROLE {role_identifier} SET default_transaction_read_only = on;\n"
            f"GRANT CONNECT ON DATABASE {self._escape_identifier(database)} TO {role_identifier};\n"
            f"DO {grants_tag}\n"
            "DECLARE\n"
            "  schema_name text;\n"
            "BEGIN\n"
            "  FOR schema_name IN\n"
            "    SELECT nspname FROM pg_catalog.pg_namespace\n"
            "    WHERE nspname NOT IN ('pg_catalog', 'information_schema')\n"
            "      AND nspname NOT LIKE 'pg\\_%'\n"
            "  LOOP\n"
            f"    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', schema_name, {role_literal});\n"
            "    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', "
            f"schema_name, {role_literal});\n"
            "  END LOOP;\n"
            "END\n"
            f"{grants_tag};\n"
        )
        success, output = self._execute_sql(
            provision, database=database, secrets=(verifier,) if verifier else ()
        )
        if not success:
            raise DatabaseQueryError(
                f"Could not provision the read-only console role for '{database}'",
                details=output.strip(),
            )

        if verifier is not None:
            try:
                secure_write(path, password + "\n")
            except OSError as exc:
                raise DatabaseQueryError(
                    f"Could not store the password of the read-only role {role}",
                    details=f"{exc}. Check that {path.parent} is writable by root and retry.",
                ) from exc
        return role, password

    # ==================== Passwords, profiles, exposure ====================

    def set_user_password(self, username: str, password: str, host: str = "localhost") -> None:
        """
        Give a role a new password, sent as a SCRAM verifier.

        The statement carries the verifier, never the password: with
        ``log_statement = 'ddl'`` the server log, and ``pg_stat_activity``
        while it runs, only ever show a value that cannot be turned back
        into the password (see :func:`_scram_sha256_verifier`).

        Args:
            username: The role.
            password: Its new password.
            host: Ignored; PostgreSQL restricts hosts in pg_hba.conf.

        Raises:
            DatabaseUserError: When the role does not exist or the server
                refuses.
        """
        self.validate_user_name(username)
        if not self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Run 'noust db user-list --engine postgresql' to see the roles.",
            )
        verifier = _scram_sha256_verifier(password)
        success, output = self._execute_sql(
            f"ALTER ROLE {self._escape_identifier(username)} "
            f"PASSWORD {self._escape_literal(verifier)};",
            secrets=(verifier, password),
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to change the password of '{username}'", details=output.strip()
            )
        self.logger.info(f"Changed the password of: {username}")

    def _user_schemas(self, database: str) -> list[str]:
        """
        List a database's schemas outside the system ones.

        Args:
            database: The database.

        Returns:
            Schema names, sorted.

        Raises:
            DatabaseUserError: When the catalog cannot be read.
        """
        success, output = self._execute_sql(
            f"SELECT nspname FROM pg_catalog.pg_namespace WHERE {_SYSTEM_SCHEMA_FILTER} "  # noqa: S608 - constant filter, no input
            "ORDER BY nspname;",
            database=database,
        )
        if not success:
            raise DatabaseUserError(
                f"Could not list the schemas of '{database}'", details=output.strip()
            )
        return [line.strip() for line in output.splitlines() if line.strip()]

    def apply_profile(
        self, username: str, database: str, profile: str, host: str = "localhost"
    ) -> None:
        """
        Give a role exactly one access profile on a database.

        - ``owner``: the role becomes the database's owner. On PostgreSQL 15
          and later that is also what grants CREATE on ``public`` (owned by
          ``pg_database_owner``), which migrations need.
        - ``read_write``: CONNECT and TEMPORARY on the database; in every
          schema USAGE, SELECT/INSERT/UPDATE/DELETE on the tables, USAGE,
          SELECT and UPDATE on the sequences, and the same by default on
          whatever the owner creates later.
        - ``read_only``: CONNECT; in every schema USAGE, SELECT on the tables
          and sequences, and SELECT by default on the owner's future tables.
          Scoped to this database: never ``pg_read_all_data``, which reads
          every database in the cluster.

        The table and sequence grants, and the defaults, are revoked first,
        so a profile replaces what the role had instead of adding to it.
        Every name is quoted here and the statements go on stdin, one script
        in one transaction.

        Args:
            username: The role.
            database: The database.
            profile: ``owner``, ``read_write`` or ``read_only``.
            host: Ignored; PostgreSQL restricts hosts in pg_hba.conf.

        Raises:
            DatabaseUserError: When the profile is unknown, the role or the
                database does not exist, the role owns the database and would
                be left without it, or the server refuses.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        if profile not in PROFILES:
            raise DatabaseUserError(
                f"Unknown access profile: {profile!r}",
                details=f"Use one of: {', '.join(PROFILES)}.",
            )
        if not self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Create it first with 'noust db user-create'.",
            )
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")

        role = self._escape_identifier(username)
        db = self._escape_identifier(database)
        owner = self._database_owner(database)

        if profile == "owner":
            success, output = self._execute_sql(f"ALTER DATABASE {db} OWNER TO {role};")
            if not success:
                raise DatabaseUserError(
                    f"Failed to make '{username}' the owner of '{database}'",
                    details=output.strip(),
                )
            self.logger.info(f"{username} now owns {database}")
            return

        if owner == username:
            raise DatabaseUserError(
                f"'{username}' owns '{database}'; an owner cannot be given a lesser profile",
                details="Make another role the owner first, then change this one's profile.",
            )
        owner_role = self._escape_identifier(owner or self.SUPERUSER)

        if profile == "read_write":
            database_grant = "CONNECT, TEMPORARY"
            tables = "SELECT, INSERT, UPDATE, DELETE"
            sequences = "USAGE, SELECT, UPDATE"
        else:
            database_grant = "CONNECT"
            tables = "SELECT"
            sequences = "SELECT"

        statements = ["BEGIN;"]
        for schema in self._user_schemas(database):
            name = self._escape_identifier(schema)
            statements += [
                f"REVOKE ALL ON ALL TABLES IN SCHEMA {name} FROM {role};",
                f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA {name} FROM {role};",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA {name} "
                f"REVOKE ALL ON TABLES FROM {role};",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA {name} "
                f"REVOKE ALL ON SEQUENCES FROM {role};",
                f"GRANT USAGE ON SCHEMA {name} TO {role};",
                f"GRANT {tables} ON ALL TABLES IN SCHEMA {name} TO {role};",
                f"GRANT {sequences} ON ALL SEQUENCES IN SCHEMA {name} TO {role};",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA {name} "
                f"GRANT {tables} ON TABLES TO {role};",
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA {name} "
                f"GRANT {sequences} ON SEQUENCES TO {role};",
            ]
        statements.append("COMMIT;")

        success, output = self._execute_sql(
            f"REVOKE ALL ON DATABASE {db} FROM {role};\n"
            f"GRANT {database_grant} ON DATABASE {db} TO {role};\n"
        )
        if success:
            success, output = self._execute_sql("\n".join(statements) + "\n", database=database)
        if not success:
            raise DatabaseUserError(
                f"Failed to give '{username}' the {profile} profile on '{database}'",
                details=output.strip(),
            )
        self.logger.info(f"{username} now has the {profile} profile on {database}")

    def list_access(self, database: str) -> list[AccessEntry]:
        """
        List the roles that hold anything on a database, with their profile.

        One query, run inside the database. A role is listed when it owns
        the database or appears in its ACL, in a table's ACL or in a default
        ACL - not merely because PUBLIC may CONNECT, which would list every
        role in the cluster. The profile is computed from what the role can
        actually do on every table (``has_table_privilege``), or from the
        default privileges when there are no tables yet.

        Args:
            database: The database.

        Returns:
            One entry per role, superusers and ``wasm_ro_`` roles marked
            internal.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseUserError: When the catalog cannot be read.
        """
        self.validate_database_name(database)
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")
        success, output = self._execute_sql(
            "WITH rels AS ("  # noqa: S608 - constant query, no input
            " SELECT c.oid, c.relacl FROM pg_catalog.pg_class c"
            " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
            f" WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND {_SYSTEM_SCHEMA_FILTER}"
            "), this AS ("
            " SELECT datdba, datacl FROM pg_catalog.pg_database"
            " WHERE datname = current_database()"
            "), defaults AS ("
            " SELECT a.grantee, a.privilege_type FROM pg_catalog.pg_default_acl d,"
            " aclexplode(d.defaclacl) a WHERE d.defaclobjtype = 'r'"
            "), grantees AS ("
            " SELECT (aclexplode(datacl)).grantee AS oid FROM this WHERE datacl IS NOT NULL"
            " UNION SELECT datdba FROM this"
            " UNION SELECT (aclexplode(relacl)).grantee FROM rels WHERE relacl IS NOT NULL"
            " UNION SELECT grantee FROM defaults"
            ") SELECT r.rolname, r.oid = (SELECT datdba FROM this), r.rolsuper,"
            " (SELECT count(*) FROM rels),"
            " (SELECT count(*) FROM rels WHERE has_table_privilege(r.oid, rels.oid, 'SELECT')),"
            " (SELECT count(*) FROM rels WHERE has_table_privilege(r.oid, rels.oid, 'INSERT')),"
            " COALESCE((SELECT bool_or(privilege_type = 'SELECT') FROM defaults"
            " WHERE grantee = r.oid), false),"
            " COALESCE((SELECT bool_or(privilege_type = 'INSERT') FROM defaults"
            " WHERE grantee = r.oid), false)"
            " FROM pg_catalog.pg_roles r WHERE r.oid IN (SELECT oid FROM grantees)"
            " ORDER BY r.rolname;",
            database=database,
        )
        if not success:
            raise DatabaseUserError(
                f"Could not read who can reach '{database}'", details=output.strip()
            )

        entries: list[AccessEntry] = []
        for line in output.strip().splitlines():
            parts = line.split("|")
            if len(parts) < 8:
                continue
            name, owns, superuser = parts[0], parts[1] == "t", parts[2] == "t"
            tables, readable, writable = (int(p) if p.isdigit() else 0 for p in parts[3:6])
            default_read, default_write = parts[6] == "t", parts[7] == "t"
            privileges: tuple[str, ...]
            if owns:
                profile, privileges = "owner", ("OWNER",)
            elif tables and readable == tables and writable == tables:
                profile, privileges = "read_write", ("SELECT", "INSERT", "UPDATE", "DELETE")
            elif tables and readable == tables and writable == 0:
                profile, privileges = "read_only", ("SELECT",)
            elif not tables and default_write:
                profile, privileges = "read_write", ("SELECT", "INSERT", "UPDATE", "DELETE")
            elif not tables and default_read:
                profile, privileges = "read_only", ("SELECT",)
            else:
                profile, privileges = "custom", ()
            entries.append(
                AccessEntry(
                    username=name,
                    profile=profile,
                    privileges=privileges,
                    internal=superuser or self.is_internal_user(name),
                )
            )
        return entries

    def drop_read_only_account(self, database: str) -> None:
        """
        Drop the read-only console's role of a dropped database, and its password.

        Args:
            database: The dropped database.
        """
        role = _read_only_role_name(database)
        success, output = self._execute_sql(f"DROP ROLE IF EXISTS {self._escape_identifier(role)};")
        if not success:
            self.logger.warning(f"Could not drop the read-only role {role}: {output.strip()}")
            return
        try:
            self.fs.remove(self._read_only_password_file(role))
        except OSError as exc:
            self.logger.warning(f"Could not remove the password file of {role}: {exc}")

    def access_hint(self) -> str:
        """
        Say how Noust signs in, since there is no password to store.

        Returns:
            What pg_hba.conf must allow; for a container, what its
            environment must carry.
        """
        if self.instance is not None:
            return super().access_hint()
        return (
            "Noust signs in as the system's postgres user over the local socket (peer "
            "authentication). Put 'local all postgres peer' back at the top of pg_hba.conf "
            "and reload PostgreSQL."
        )

    def listen_addresses(self) -> ListenAddress | None:
        """
        Ask the server for ``listen_addresses``.

        Returns:
            The addresses, or None when the superuser session cannot ask.
        """
        raw = self._show("listen_addresses")
        if raw is None:
            return None
        return listen_address("listen_addresses", raw, separator=",")

    def fix_owner(self, database: str, new_owner: str, *, apply: bool = False) -> OwnerPlan:
        """
        Give a database, and what its current owner holds in it, to another role.

        Why it exists: before 3.1 a database provisioned for an application
        was created by, and owned by, ``postgres``, with the application's
        role granted ALL. PostgreSQL 15 took CREATE on ``public`` away from
        PUBLIC and gave it to the database owner, so migrations on Debian 12
        and Ubuntu 24.04 fail with "permission denied for schema public".
        New databases are created owned by the application's role; the ones
        that exist are fixed by this, explicitly and never automatically.

        Without ``apply`` nothing runs but catalog reads: the plan lists the
        database's current owner and every relation and schema that owner
        holds outside the system schemas, and the exact statements. Sequences
        that belong to a column follow their table. Running it again with
        the previous owner puts the database and those objects back.

        Args:
            database: The database.
            new_owner: The role that must own it.
            apply: Run the statements, in one transaction.

        Returns:
            The plan, marked applied when it ran.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseUserError: When the role does not exist, the catalog
                cannot be read, or a statement fails (nothing is changed
                then: the transaction is rolled back).
        """
        self.validate_database_name(database)
        self.validate_user_name(new_owner)
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")
        if not self.user_exists(new_owner):
            raise DatabaseUserError(
                f"User '{new_owner}' does not exist",
                details="Name the application's own role, as 'noust db info' shows it.",
            )
        current = self._database_owner(database)
        role = self._escape_identifier(new_owner)
        statements = [f"ALTER DATABASE {self._escape_identifier(database)} OWNER TO {role};"]
        objects: list[str] = []

        if current and current != new_owner:
            owner_literal = self._escape_literal(current)
            success, output = self._execute_sql(
                "SELECT c.relkind, n.nspname, c.relname FROM pg_catalog.pg_class c"  # noqa: S608 - quoted literal, not interpolated data
                " JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
                " JOIN pg_catalog.pg_roles r ON r.oid = c.relowner"
                f" WHERE r.rolname = {owner_literal} AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')"
                f" AND {_SYSTEM_SCHEMA_FILTER}"
                " AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_depend d WHERE d.objid = c.oid"
                " AND d.classid = 'pg_catalog.pg_class'::regclass AND d.deptype IN ('a', 'i'))"
                " UNION ALL SELECT 'n', n.nspname, '' FROM pg_catalog.pg_namespace n"
                " JOIN pg_catalog.pg_roles r ON r.oid = n.nspowner"
                f" WHERE r.rolname = {owner_literal} AND {_SYSTEM_SCHEMA_FILTER}"
                " AND n.nspname <> 'public'"
                " ORDER BY 1, 2, 3;",
                database=database,
            )
            if not success:
                raise DatabaseUserError(
                    f"Could not read what {current} owns in '{database}'", details=output.strip()
                )
            for line in output.strip().splitlines():
                parts = line.split("|")
                if len(parts) < 3:
                    continue
                kind, schema, name = parts[0], parts[1], parts[2]
                if kind == "n":
                    objects.append(f"SCHEMA {schema}")
                    statements.append(
                        f"ALTER SCHEMA {self._escape_identifier(schema)} OWNER TO {role};"
                    )
                    continue
                keyword = _RELATION_KINDS.get(kind)
                if keyword is None:
                    continue
                objects.append(f"{keyword} {schema}.{name}")
                statements.append(
                    f"ALTER {keyword} {self._escape_identifier(schema)}."
                    f"{self._escape_identifier(name)} OWNER TO {role};"
                )

        plan = OwnerPlan(
            database=database,
            current_owner=current,
            new_owner=new_owner,
            objects=tuple(objects),
            statements=tuple(statements),
            applied=False,
        )
        if not apply or current == new_owner:
            return plan

        success, output = self._execute_sql(statements[0])
        if success and len(statements) > 1:
            script = "BEGIN;\n" + "\n".join(statements[1:]) + "\nCOMMIT;\n"
            success, output = self._execute_sql(script, database=database)
            if not success and current:
                # The objects stayed where they were; the database goes back
                # too, so a failure leaves nothing half moved.
                self._execute_sql(
                    f"ALTER DATABASE {self._escape_identifier(database)} "
                    f"OWNER TO {self._escape_identifier(current)};"
                )
        if not success:
            raise DatabaseUserError(
                f"Could not give '{database}' to {new_owner}; nothing was changed",
                details=output.strip(),
            )
        self.logger.info(f"{database} and {len(objects)} object(s) now belong to {new_owner}")
        return replace(plan, applied=True)

    def get_interactive_command(
        self,
        database: str | None = None,
        username: str | None = None,
    ) -> list[str]:
        """
        Build the command that opens a psql session.

        Args:
            database: Database to connect to.
            username: Role to connect as.

        Returns:
            The argument vector. This one is handed to ``os.execvp`` directly
            instead of going through the runner - an interactive client needs
            the real terminal - so the ``runuser`` prefix that peer
            authentication requires is applied here rather than by ``user=``.
            For a container, the ``docker exec -it`` that opens it there.
        """
        argv = ["psql"]
        if database:
            argv.extend(["-d", database])
        if username:
            argv.extend(["-U", username])
        if self.instance is not None:
            return self._interactive(argv, user=self.SUPERUSER)
        return [*runuser_prefix(self.SUPERUSER), *argv]


DatabaseRegistry.register(PostgresManager, aliases=["postgres", "pg", "pgsql"])
