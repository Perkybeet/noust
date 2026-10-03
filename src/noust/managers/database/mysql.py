# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
MySQL and MariaDB manager.

Two habits from the previous implementation are gone here:

- statements travel on stdin, so a ``CREATE USER ... IDENTIFIED BY`` no longer
  shows the password to every ``ps`` on the machine,
- dumps are streamed to disk by the runner instead of being pushed through
  ``bash -c "... | gzip > file"``.

Credentials reach the client through a 0600 option file, which is what MySQL
documents as the way to authenticate without a command line password.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import secrets as secrets_module
import shutil
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import (
    DatabaseBackupError,
    DatabaseError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    DatabaseUserError,
)
from noust.core.runner import CommandResult
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
from noust.managers.database.registry import DatabaseRegistry

if TYPE_CHECKING:
    from noust.managers.database.instances import DatabaseInstance

#: How long the read-only account this process provisioned is reused as it
#: is. Provisioning sets a new password and flushes privileges; the data
#: explorer reads several times per screen, and doing that before each read
#: (research/3.1/databases.md, B12) doubled the processes per page. A read
#: the server refuses (the console rotated the password meanwhile) provisions
#: again and retries.
_PROVISION_REUSE_SECONDS = 60.0

#: The read-only account and password this process last provisioned, and when.
_provisioned: dict[str, tuple[str, str, float]] = {}

#: What the server says when an account's credentials or grants do not let it in.
_ACCESS_DENIED = "Access denied"

#: Static privileges MySQL 8 and MariaDB accept in a GRANT. Anything outside
#: this set is rejected before a statement is built.
MYSQL_PRIVILEGES = frozenset(
    {
        "ALL",
        "ALL PRIVILEGES",
        "ALTER",
        "ALTER ROUTINE",
        "CREATE",
        "CREATE ROLE",
        "CREATE ROUTINE",
        "CREATE TABLESPACE",
        "CREATE TEMPORARY TABLES",
        "CREATE USER",
        "CREATE VIEW",
        "DELETE",
        "DROP",
        "DROP ROLE",
        "EVENT",
        "EXECUTE",
        "FILE",
        "GRANT OPTION",
        "INDEX",
        "INSERT",
        "LOCK TABLES",
        "PROCESS",
        "PROXY",
        "REFERENCES",
        "RELOAD",
        "REPLICATION CLIENT",
        "REPLICATION SLAVE",
        "SELECT",
        "SHOW DATABASES",
        "SHOW VIEW",
        "SHUTDOWN",
        "SUPER",
        "TRIGGER",
        "UPDATE",
        "USAGE",
    }
)


#: Escapes MySQL's option file reader turns back into characters. The manual
#: documents ``\b \t \n \r \\ \s``; the reader (mysys/my_default.cc) also
#: accepts ``\"`` and ``\'``, and its comment stripper skips a quote that
#: follows a backslash. Escaping both quote characters is therefore what keeps a
#: '#' inside a password from being taken for a comment and truncating it.
OPTION_FILE_ESCAPES = str.maketrans(
    {
        "\\": "\\\\",
        '"': '\\"',
        "'": "\\'",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
        "\b": "\\b",
        # A quoted value keeps its inner spaces, but the reader trims the raw
        # line first; \s survives both and costs nothing.
        " ": "\\s",
    }
)


def escape_option_file_value(value: str) -> str:
    """
    Render a value so a MySQL option file reads it back unchanged.

    An option file is parsed line by line, so a raw newline in a password does
    not corrupt the value: it ends the record and starts a new directive inside
    the ``[client]`` section. ``socket=`` or ``plugin-dir=`` placed there
    redirects every connection Noust makes afterwards. The escapes below are the
    ones the client applies after the line split, so a newline arrives as data.

    Args:
        value: The raw value, such as a password taken from the configuration.

    Returns:
        The value, escaped and enclosed in double quotes.

    Raises:
        DatabaseError: When the value contains a NUL byte, which the reader
            would silently truncate at rather than misparse.
    """
    if "\x00" in value:
        raise DatabaseError(
            "A MySQL credential contains a NUL byte",
            details=(
                "MySQL option files are read as C strings and would silently use only the "
                "part before the NUL. Change the credential in /etc/noust/config.yaml."
            ),
        )
    return '"' + value.translate(OPTION_FILE_ESCAPES) + '"'


#: Prefix of the least-privilege account the read-only console connects as.
_READ_ONLY_USER_PREFIX = "wasm_ro_"

#: The privileges each access profile grants on one database.
PROFILE_PRIVILEGES: dict[str, tuple[str, ...]] = {
    "owner": ("ALL PRIVILEGES",),
    "read_write": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "read_only": ("SELECT",),
}

#: The mysql.db columns a profile is read back from, in query order.
_PROFILE_COLUMNS = (
    "Select_priv",
    "Insert_priv",
    "Update_priv",
    "Delete_priv",
    "Create_priv",
    "Drop_priv",
    "Alter_priv",
    "Index_priv",
)

#: MySQL and MariaDB both cap a user name at 32 characters.
_USER_NAME_MAX_LENGTH = 32

#: Client commands that reach the host instead of the database: a shell, a
#: file read, a file write, a shell through the pager, an editor. The console
#: runs with ``--binary-mode``, which already disables them; refusing them by
#: name as well turns a client error into an actionable one, and keeps the
#: guarantee if a client ever ships without that switch honoured.
_HOST_CLIENT_COMMANDS = frozenset({"system", "source", "tee", "pager", "edit"})


#: A statement that leaves the database a load was pointed at, at the start
#: of a line (where mysqldump writes every statement), bare or inside a
#: ``/*!NNNNN ... */`` versioned comment. A ``--databases`` dump carries
#: ``CREATE DATABASE`` and ``USE`` for the database it was taken from, and
#: ``mysql -D new`` runs them: the "new" database stays empty and the load
#: goes into the original.
_OTHER_DATABASE_STATEMENT = re.compile(
    rb"^\s*(?:/\*!\d*\s*)?(?:USE\b|(?:CREATE|DROP|ALTER)\s+(?:DATABASE|SCHEMA)\b)",
    re.IGNORECASE,
)


def statement_leaving_the_database(dump: Path) -> str | None:
    """
    Find the first statement in a dump that would load into another database.

    The dump is streamed line by line, gzipped or not, so a large one costs
    no memory. Only line starts are looked at: mysqldump escapes the
    newlines inside a value, so a line starting ``USE`` is a statement and
    never data.

    Args:
        dump: A plain or gzipped SQL dump.

    Returns:
        The offending line, shortened, or None when there is none.

    Raises:
        DatabaseBackupError: When a gzipped dump cannot be read.
    """
    try:
        with open(dump, "rb") as probe:
            gzipped = probe.read(2) == b"\x1f\x8b"
        opener = gzip.open if gzipped else open
        with opener(dump, "rb") as handle:
            for line in handle:
                if _OTHER_DATABASE_STATEMENT.match(line):
                    return line.decode("utf-8", errors="replace").strip()[:200]
    except (OSError, EOFError) as exc:
        raise DatabaseBackupError(
            f"Could not read {dump.name} to check it", details=str(exc)
        ) from exc
    return None


def _refuse_client_commands(statement: str) -> None:
    """
    Refuse console text that is a mysql client command rather than SQL.

    The client recognises a named command (``system id``) only at the start
    of a statement and a backslash command (``\\! id``) anywhere outside a
    string, so those are the positions checked. A column called ``status``
    or ``source`` on a later line of a SELECT is not a command and is not
    refused.

    Args:
        statement: The operator's statement.

    Raises:
        DatabaseQueryError: When the text starts with a backslash command,
            contains ``\\!``, or begins a statement with a host command.
    """
    starts = [chunk.split(None, 1)[0].lower() for chunk in statement.split(";") if chunk.split()]
    if (
        statement.lstrip().startswith("\\")
        or "\\!" in statement
        or any(word in _HOST_CLIENT_COMMANDS for word in starts)
    ):
        raise DatabaseQueryError(
            "mysql client commands are not accepted by the console",
            details=(
                "system, source, tee, pager, edit and backslash commands such as \\! "
                "act on the server's filesystem, not the database. Send SQL only; use "
                "'noust db connect' for an interactive mysql session."
            ),
        )


def _read_only_user_name(database: str) -> str:
    """
    Build the deterministic, length-safe user name for a database's console.

    A name assembled by simple concatenation collides silently once it
    overflows the 32-character limit MySQL and MariaDB both enforce: two
    long, similarly prefixed database names could end up sharing one
    read-only account. A short hash of the full name keeps every account
    distinct even when it has to be shortened.

    Args:
        database: The database the account is scoped to.

    Returns:
        ``wasm_ro_<database>``, unchanged when it fits within the limit;
        otherwise truncated and suffixed with an 8-character digest of the
        full database name.
    """
    candidate = f"{_READ_ONLY_USER_PREFIX}{database}"
    if len(candidate) <= _USER_NAME_MAX_LENGTH:
        return candidate
    digest = hashlib.sha256(database.encode()).hexdigest()[:8]
    budget = _USER_NAME_MAX_LENGTH - len(_READ_ONLY_USER_PREFIX) - len(digest) - 1
    return f"{_READ_ONLY_USER_PREFIX}{database[:budget]}_{digest}"


class MySQLManager(BaseDatabaseManager):
    """Manager for MySQL and MariaDB, whichever is installed."""

    ENGINE_NAME = "mysql"
    DISPLAY_NAME = "MySQL/MariaDB"
    DEFAULT_PORT = 3306
    SERVICE_NAME = "mysql"
    PACKAGE_NAMES = ("mysql-server",)
    MARIADB_PACKAGES = ("mariadb-server",)
    CLIENT_BINARY = "mysql"
    VERSION_ARGV = ("mysql", "--version")
    PURGE_PATHS = ("/var/lib/mysql", "/etc/mysql")
    MAX_DATABASE_NAME_LENGTH = 64
    MAX_USER_NAME_LENGTH = 32
    VALID_PRIVILEGES = MYSQL_PRIVILEGES
    DEFAULT_PRIVILEGES = ("ALL PRIVILEGES",)
    SUPPORTS_STRUCTURED_QUERY = True
    CAPABILITIES = frozenset({"sql", "tables", "read_only", "users", "profiles", "dump", "metrics"})
    #: The client reads a dump on its stdin, never by name; see _load_backup().
    RESTORE_READS_STDIN = True
    EOL_FAMILY = "mysql"
    INTERNAL_USERS = frozenset(
        {
            "root",
            "mysql",
            "mysql.sys",
            "mysql.session",
            "mysql.infoschema",
            "mariadb.sys",
            "debian-sys-maint",
        }
    )

    #: Schemas that belong to the server, not to a user.
    SYSTEM_DATABASES = frozenset({"information_schema", "mysql", "performance_schema", "sys"})

    def __init__(self, verbose: bool = False):
        """
        Args:
            verbose: Enable verbose logging.
        """
        super().__init__(verbose=verbose)
        self._detect_variant()

    def _detect_variant(self) -> None:
        """Name the service, and the lifecycle, after the flavour that is installed."""
        if self.runner.exists("mariadb") or self.runner.exists("mariadbd"):
            self.SERVICE_NAME = "mariadb"
            self.DISPLAY_NAME = "MariaDB"
            self.EOL_FAMILY = "mariadb"

    def _bind_names(self, instance: DatabaseInstance) -> None:
        """
        Name the engine after the container's image, never after the host.

        Args:
            instance: The container.
        """
        mariadb = instance.flavour == "mariadb"
        self.DISPLAY_NAME = "MariaDB" if mariadb else "MySQL"
        self.EOL_FAMILY = "mariadb" if mariadb else "mysql"

    def _package_sets(self) -> tuple[list[str], ...]:
        """
        Prefer MariaDB, which is what current Debian and Ubuntu ship.

        Returns:
            The MariaDB packages first, the MySQL ones as a fallback.
        """
        return (list(self.MARIADB_PACKAGES), list(self.PACKAGE_NAMES))

    def _on_packages_installed(self, packages: Sequence[str]) -> None:
        """
        Record which flavour got installed.

        Args:
            packages: The package names that installed successfully.
        """
        if list(packages) == list(self.MARIADB_PACKAGES):
            self.SERVICE_NAME = "mariadb"
            self.DISPLAY_NAME = "MariaDB"
            self.EOL_FAMILY = "mariadb"

    def installed_flavour(self) -> str | None:
        """
        Name the flavour installed: ``mariadb`` or ``mysql``.

        Returns:
            The flavour, or None when neither is installed.
        """
        if not self.is_installed():
            return None
        return "mariadb" if self.is_mariadb else "mysql"

    def _post_install(self) -> None:
        """Drop the anonymous accounts and the test database a fresh install ships."""
        self._execute_sql("DELETE FROM mysql.user WHERE User='';")
        self._execute_sql("DROP DATABASE IF EXISTS test;")
        self._execute_sql("FLUSH PRIVILEGES;")

    # ==================== SQL text ====================

    @staticmethod
    def _escape_identifier(value: str) -> str:
        """
        Quote an identifier the way MySQL does.

        Args:
            value: Raw identifier.

        Returns:
            The identifier in backticks, with embedded backticks doubled.
        """
        return quote_identifier(value, "`")

    @staticmethod
    def _escape_literal(value: str) -> str:
        """
        Quote a string literal the way MySQL does.

        Args:
            value: Raw string.

        Returns:
            The value in single quotes, with backslashes and single quotes
            escaped, which covers both NO_BACKSLASH_ESCAPES settings.
        """
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"

    @classmethod
    def validate_host(cls, host: str) -> str:
        """
        Check a host restriction.

        Args:
            host: Host pattern such as ``localhost``, ``%`` or ``10.0.0.%``.

        Returns:
            The host, unchanged.

        Raises:
            DatabaseUserError: When the host contains anything but letters,
                digits, dot, dash, colon, underscore or the ``%`` wildcard.
        """
        if (
            not host
            or len(host) > 255
            or not set(host)
            <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-_%")
        ):
            raise DatabaseUserError(
                f"Invalid MySQL host: {host!r}",
                details="Use a host name, an address or a pattern such as 'localhost' or '10.0.0.%'.",
            )
        return host

    # ==================== Credentials ====================

    @contextmanager
    def _credentials(self) -> Iterator[list[str]]:
        """
        Provide the client arguments that authenticate, without a password in argv.

        MySQL reads credentials from an option file, which is the mechanism it
        documents for scripts; the file is created 0600 and removed afterwards.

        Yields:
            Arguments to place immediately after the program name.

        Raises:
            DatabaseError: When a credential cannot be written to an option file
                without changing its value.
        """
        if self.instance is not None:
            # Inside a container the account is the one its environment names
            # and its password is found there too (MYSQL_PWD, by the client
            # script): a file on the host is nothing the client could open,
            # and the host's stored account belongs to the host's server.
            yield ["-u", self.instance.admin_user]
            return
        credentials = self.config.get("databases", {}).get("credentials", {}).get("mysql", {})
        user = credentials.get("user")
        password = credentials.get("password")

        if not password:
            yield ["-u", user] if user else []
            return

        # The values are escaped before the file is created, so a credential
        # that cannot be represented fails without leaving a file behind.
        content = "[client]\n"
        if user:
            content += f"user={escape_option_file_value(str(user))}\n"
        content += f"password={escape_option_file_value(str(password))}\n"

        # mkstemp creates the file 0600, so the password is never briefly
        # world readable the way a write-then-chmod would leave it.
        fd, path = tempfile.mkstemp(prefix="wasm_mysql_", suffix=".cnf")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(content)
            # This option is only honoured as the client's first argument.
            yield [f"--defaults-extra-file={path}"]
        finally:
            Path(path).unlink(missing_ok=True)

    def _ensure_read_only_user(self, database: str) -> tuple[str, str]:
        """
        Create, idempotently, the account the read-only console connects as.

        Closes ``SELECT LOAD_FILE('/etc/shadow')`` and
        ``SELECT ... INTO OUTFILE`` being reachable from a "read-only"
        console query: ``START TRANSACTION READ ONLY`` refuses a write to a
        table, but not a call to ``LOAD_FILE`` or a clause that writes to the
        filesystem instead of a table, and the account the write path
        connects as typically carries ``FILE`` (needed for restores) and
        often more. ``wasm_ro_<database>`` is a dedicated account, granted
        nothing but ``SELECT`` on this one database - no ``FILE``, no
        ``SUPER``, no ``PROCESS``, no access to any other schema - with a
        password rotated on every call and never persisted.
        ``REVOKE ALL PRIVILEGES, GRANT OPTION FROM`` is the one MySQL and
        MariaDB statement that succeeds even when the account already holds
        nothing, which is what keeps this idempotent and also keeps a
        privilege granted by an older version of this method from surviving
        an upgrade.

        Args:
            database: The database the account is scoped to.

        Returns:
            The user name and a freshly generated password.

        Raises:
            DatabaseQueryError: When the account cannot be provisioned.
        """
        username = _read_only_user_name(database)
        password = secrets_module.token_urlsafe(24)
        account = f"{self._escape_literal(username)}@{self._escape_literal('localhost')}"
        provision = (
            f"CREATE USER IF NOT EXISTS {account} IDENTIFIED BY "
            f"{self._escape_literal(password)};\n"
            f"ALTER USER {account} IDENTIFIED BY {self._escape_literal(password)};\n"
            f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM {account};\n"
            f"GRANT SELECT ON {self._escape_identifier(database)}.* TO {account};\n"
            "FLUSH PRIVILEGES;\n"
        )
        success, output = self._execute_sql(provision, secrets=(password,))
        if not success:
            raise DatabaseQueryError(
                f"Could not provision the read-only console account for '{database}'",
                details=output.strip(),
            )
        return username, password

    @contextmanager
    def _read_only_credentials(
        self, database: str, account: tuple[str, str] | None = None
    ) -> Iterator[tuple[list[str], dict[str, str]]]:
        """
        Provision and hand back the read-only console's own credentials.

        The client must not be able to end up connected as anyone else, and
        an option file alone does not ensure that: ``--defaults-extra-file`` is
        read *before* the invoking user's ``~/.my.cnf`` (and MySQL's
        ``.mylogin.cnf``), so root's own ``user=root`` there would silently win
        and the "read-only" console would run as root. The account name is
        therefore also given on the command line - it is not a secret, and
        argv beats every option file - and ``HOME`` points at a private
        directory holding nothing but this option file, so root's files are
        not read at all and cannot even turn the connection into an
        authentication failure.

        Args:
            database: The database the account is scoped to.
            account: The user name and password of an account provisioned
                moments ago; provisioned now when None.

        Yields:
            The arguments to place immediately after the program name, and
            the environment the client must run with.

        Raises:
            DatabaseQueryError: When the account cannot be provisioned.
        """
        username, password = account or self._ensure_read_only_user(database)
        if self.instance is not None:
            # Forwarded by name into the container, never in argv; the name
            # on the command line beats any option file the image carries.
            yield [f"--user={username}"], {"MYSQL_PWD": password}
            return

        content = (
            "[client]\n"
            f"user={escape_option_file_value(username)}\n"
            f"password={escape_option_file_value(password)}\n"
        )
        # mkdtemp creates the directory 0700 and the file is created 0600
        # inside it, so the password is never briefly readable by anyone else.
        home = tempfile.mkdtemp(prefix="wasm_mysql_ro_")
        path = Path(home) / "client.cnf"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(content)
            env = {
                "HOME": home,
                # MySQL looks for the login path file here before $HOME.
                "MYSQL_TEST_LOGIN_FILE": str(Path(home) / ".mylogin.cnf"),
            }
            yield [f"--defaults-extra-file={path}", f"--user={username}"], env
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def _client_argv(
        self, credentials: Sequence[str], database: str | None = None, *, headers: bool = False
    ) -> list[str]:
        """
        Build a mysql invocation that prints machine readable output.

        Args:
            credentials: Arguments from :meth:`_credentials`.
            database: Database to select.
            headers: Keep the column name header row ``-B`` alone prints.
                Every caller except the structured console query drops it
                with ``-N``, because they parse a single value or a line at a
                time and a header would be just another line to skip.

        Returns:
            The argument vector.
        """
        argv = ["mysql", *credentials]
        if not headers:
            argv.append("-N")
        argv.append("-B")
        if database:
            argv.extend(["-D", database])
        return argv

    def _execute_sql(
        self,
        sql: str,
        database: str | None = None,
        *,
        secrets: Sequence[str] = (),
        timeout: int = QUERY_TIMEOUT,
    ) -> tuple[bool, str]:
        """
        Run SQL, passing the statement on stdin.

        Args:
            sql: The statement.
            database: Database to select.
            secrets: Values the statement carries that must not be logged.
            timeout: Deadline in seconds.

        Returns:
            Whether the client succeeded, and its output or its error text.
        """
        with self._credentials() as credentials:
            result = self._exec(
                self._client_argv(credentials, database),
                input=sql,
                timeout=timeout,
                secrets=secrets,
            )
        return result.success, result.stdout if result.success else result.stderr

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
            owner: User to grant privileges to once the database exists.
            encoding: Character set, utf8mb4 by default.
            **kwargs: Accepts ``collation``.

        Returns:
            Information about the new database.

        Raises:
            DatabaseExistsError: When the database already exists.
            DatabaseError: When a name is invalid or creation fails.
        """
        self.validate_database_name(name)
        if self.database_exists(name):
            raise DatabaseExistsError(
                f"Database '{name}' already exists",
                details="Drop it first, or pick another name.",
            )

        charset = validate_name(
            encoding or "utf8mb4", kind="character set", engine=self.DISPLAY_NAME, max_length=64
        )
        collation = validate_name(
            kwargs.get("collation", "utf8mb4_unicode_ci"),
            kind="collation",
            engine=self.DISPLAY_NAME,
            max_length=64,
        )

        success, output = self._execute_sql(
            f"CREATE DATABASE {self._escape_identifier(name)} "
            f"CHARACTER SET {charset} COLLATE {collation};"
        )
        if not success:
            raise DatabaseError(f"Failed to create database '{name}'", details=output.strip())

        if owner:
            self.grant_privileges(owner, name)

        self.logger.info(f"Created database: {name}")
        return self.get_database_info(name)

    def drop_database(self, name: str, force: bool = False) -> None:
        """
        Drop a database.

        Args:
            name: Database name.
            force: Accept a missing database as success.

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
                details="Run 'noust db list --engine mysql' to see the databases.",
            )

        success, output = self._execute_sql(f"DROP DATABASE {self._escape_identifier(name)};")
        if not success:
            raise DatabaseError(f"Failed to drop database '{name}'", details=output.strip())

        self.logger.info(f"Dropped database: {name}")

    def database_exists(self, name: str) -> bool:
        """
        Report whether a schema exists.

        Args:
            name: Database name.

        Returns:
            True when INFORMATION_SCHEMA holds the name.

        Raises:
            DatabaseAccessError: When the server does not let Noust in.
            DatabaseQueryError: When it cannot be asked: "no" would let a
                drop skip its last dump and a forget delete a live row.
        """
        success, output = self._execute_sql(
            "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA "  # noqa: S608 - quoted literal, not interpolated data
            f"WHERE SCHEMA_NAME = {self._escape_literal(name)};"
        )
        if not success:
            self._listing_failed("databases", output)
        return output.strip() == name

    def list_databases(self) -> list[DatabaseInfo]:
        """
        List the schemas that do not belong to the server.

        Size comes from the same query, one row per schema rather than one
        query per database: ``INFORMATION_SCHEMA.TABLES`` is joined and
        summed per schema instead of :meth:`get_database_info`'s separate
        call, so a server with a hundred databases still costs one round
        trip.

        Owner is not filled: MySQL and MariaDB have no catalog concept of a
        database owner, only per-account grants, which :meth:`list_users`
        reports instead.

        Returns:
            One entry per user database.
        """
        success, output = self._execute_sql(
            "SELECT s.SCHEMA_NAME, s.DEFAULT_CHARACTER_SET_NAME, "
            "SUM(t.DATA_LENGTH + t.INDEX_LENGTH), COUNT(t.TABLE_NAME) "
            "FROM INFORMATION_SCHEMA.SCHEMATA s "
            "LEFT JOIN INFORMATION_SCHEMA.TABLES t ON t.TABLE_SCHEMA = s.SCHEMA_NAME "
            "GROUP BY s.SCHEMA_NAME, s.DEFAULT_CHARACTER_SET_NAME;"
        )
        if not success:
            self._listing_failed("databases", output)

        databases = []
        for line in output.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t")
            name = parts[0]
            if name in self.SYSTEM_DATABASES:
                continue
            size = None
            if len(parts) > 2 and parts[2].isdigit():
                size = format_size(int(parts[2]))
            tables = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
            databases.append(
                DatabaseInfo(
                    name=name,
                    engine=self.ENGINE_NAME,
                    encoding=parts[1] if len(parts) > 1 else None,
                    size=size,
                    tables=tables,
                )
            )
        return databases

    def get_database_info(self, name: str) -> DatabaseInfo:
        """
        Describe one database.

        Args:
            name: Database name.

        Returns:
            Size, table count and character set.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
        """
        if not self.database_exists(name):
            raise DatabaseNotFoundError(f"Database '{name}' does not exist")

        literal = self._escape_literal(name)
        success, output = self._execute_sql(
            "SELECT SUM(DATA_LENGTH + INDEX_LENGTH), COUNT(*) FROM INFORMATION_SCHEMA.TABLES "  # noqa: S608 - quoted literal, not interpolated data
            f"WHERE TABLE_SCHEMA = {literal};"
        )

        size: str | None = None
        tables = 0
        if success and output.strip():
            parts = output.strip().split("\t")
            if len(parts) >= 2:
                size = format_size(int(parts[0]) if parts[0].isdigit() else 0)
                tables = int(parts[1]) if parts[1].isdigit() else 0

        success, output = self._execute_sql(
            "SELECT DEFAULT_CHARACTER_SET_NAME FROM INFORMATION_SCHEMA.SCHEMATA "  # noqa: S608 - quoted literal, not interpolated data
            f"WHERE SCHEMA_NAME = {literal};"
        )

        return DatabaseInfo(
            name=name,
            engine=self.ENGINE_NAME,
            size=size,
            tables=tables,
            encoding=output.strip() if success else None,
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
        Create a user.

        Args:
            username: User name.
            password: Password. Generated when omitted.
            host: Host the user may connect from.
            **kwargs: Unused.

        Returns:
            The user and its password.

        Raises:
            DatabaseUserError: When the user exists or creation fails.
        """
        self.validate_user_name(username)
        self.validate_host(host)
        if self.user_exists(username, host):
            raise DatabaseUserError(
                f"User '{username}'@'{host}' already exists",
                details="Drop the user first, or pick another name.",
            )

        password = password or self.generate_password()

        success, output = self._execute_sql(
            f"CREATE USER {self._escape_literal(username)}@{self._escape_literal(host)} "
            f"IDENTIFIED BY {self._escape_literal(password)};",
            secrets=(password,),
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to create user '{username}'@'{host}'", details=output.strip()
            )

        self._execute_sql("FLUSH PRIVILEGES;")
        self.logger.info(f"Created user: {username}@{host}")
        return UserInfo(username=username, engine=self.ENGINE_NAME, host=host), password

    def drop_user(self, username: str, host: str = "localhost") -> None:
        """
        Drop a user.

        Args:
            username: User name.
            host: Host the user connects from.

        Raises:
            DatabaseUserError: When the user is missing or the drop fails.
        """
        self.validate_user_name(username)
        self.validate_host(host)
        if not self.user_exists(username, host):
            raise DatabaseUserError(
                f"User '{username}'@'{host}' does not exist",
                details="Run 'noust db users --engine mysql' to see the users.",
            )

        success, output = self._execute_sql(
            f"DROP USER {self._escape_literal(username)}@{self._escape_literal(host)};"
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to drop user '{username}'@'{host}'", details=output.strip()
            )

        self._execute_sql("FLUSH PRIVILEGES;")
        self.logger.info(f"Dropped user: {username}@{host}")

    def user_exists(self, username: str, host: str = "localhost") -> bool:
        """
        Report whether a user exists.

        Args:
            username: User name.
            host: Host the user connects from.

        Returns:
            True when mysql.user holds the pair.
        """
        success, output = self._execute_sql(
            "SELECT User FROM mysql.user "  # noqa: S608 - quoted literals, not interpolated data
            f"WHERE User = {self._escape_literal(username)} "
            f"AND Host = {self._escape_literal(host)};"
        )
        return success and output.strip() == username

    def list_users(self) -> list[UserInfo]:
        """
        List the server's users.

        Databases come from the same query, one row per user rather than one
        query per user: ``mysql.db`` (database-level grants) is left-joined
        and aggregated with ``GROUP_CONCAT`` per account, so a server with a
        hundred users still costs one round trip. Table- and column-level
        grants (``mysql.tables_priv``, ``mysql.columns_priv``) are not
        included - they would need a second join per privilege scope for
        information this listing does not otherwise need, so
        :meth:`grant_privileges` remains the source of truth for exactly
        what an account can do.

        Returns:
            One entry per user and host pair, with the databases it has
            database-level grants on.
        """
        success, output = self._execute_sql(
            "SELECT u.User, u.Host, COALESCE(GROUP_CONCAT(DISTINCT d.Db SEPARATOR ','), '') "
            "FROM mysql.user u "
            "LEFT JOIN mysql.db d ON d.User = u.User AND d.Host = u.Host "
            "WHERE u.User != '' "
            "GROUP BY u.User, u.Host "
            "ORDER BY u.User;"
        )
        if not success:
            self._listing_failed("users", output)

        users = []
        for line in output.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                databases = parts[2].split(",") if len(parts) > 2 and parts[2] else []
                users.append(
                    UserInfo(
                        username=parts[0],
                        engine=self.ENGINE_NAME,
                        host=parts[1],
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
        Grant whitelisted privileges on a database to a user.

        Args:
            username: User name.
            database: Database name.
            privileges: Privileges to grant. ALL PRIVILEGES when omitted.
            host: Host the user connects from.

        Raises:
            DatabaseUserError: When a privilege is not whitelisted or the grant
                fails.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        self.validate_host(host)
        granted = self.validate_privileges(privileges)

        success, output = self._execute_sql(
            f"GRANT {', '.join(granted)} ON {self._escape_identifier(database)}.* "
            f"TO {self._escape_literal(username)}@{self._escape_literal(host)};"
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to grant privileges on '{database}' to '{username}'@'{host}'",
                details=output.strip(),
            )

        self._execute_sql("FLUSH PRIVILEGES;")
        self.logger.info(f"Granted {', '.join(granted)} on {database} to {username}@{host}")

    def revoke_privileges(
        self,
        username: str,
        database: str,
        privileges: Sequence[str] | None = None,
        host: str = "localhost",
    ) -> None:
        """
        Revoke whitelisted privileges on a database from a user.

        Args:
            username: User name.
            database: Database name.
            privileges: Privileges to revoke. ALL PRIVILEGES when omitted.
            host: Host the user connects from.

        Raises:
            DatabaseUserError: When a privilege is not whitelisted or the revoke
                fails.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        self.validate_host(host)
        revoked = self.validate_privileges(privileges)

        success, output = self._execute_sql(
            f"REVOKE {', '.join(revoked)} ON {self._escape_identifier(database)}.* "
            f"FROM {self._escape_literal(username)}@{self._escape_literal(host)};"
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to revoke privileges on '{database}' from '{username}'@'{host}'",
                details=output.strip(),
            )

        self._execute_sql("FLUSH PRIVILEGES;")
        self.logger.info(f"Revoked {', '.join(revoked)} on {database} from {username}@{host}")

    # ==================== Backup & Restore ====================

    def backup(
        self,
        database: str,
        output_path: Path | None = None,
        compress: bool = True,
        **kwargs,
    ) -> BackupInfo:
        """
        Dump a database with mysqldump.

        Args:
            database: Database name.
            output_path: Custom destination.
            compress: Pipe the dump through gzip.
            **kwargs: Unused.

        Returns:
            Information about the backup.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseBackupError: When the dump fails.
        """
        self.validate_database_name(database)
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")

        destination = self._backup_path(database, output_path, compress)
        with self._credentials() as credentials:
            argv = [
                "mysqldump",
                *credentials,
                "--single-transaction",
                "--routines",
                "--triggers",
                # Scheduled events are part of the schema too; without this a
                # restored database silently stops running them.
                "--events",
                database,
            ]
            return self._dump_to_file(argv, destination, database=database, compress=compress)

    def _check_backup(self, backup_path: Path, **kwargs: Any) -> None:
        """
        Refuse a dump that would leave the database an isolated load names.

        Args:
            backup_path: The dump.
            **kwargs: ``isolated`` for a load beside a database (a restore as
                a new one, a restore test), which must reach nothing else.

        Raises:
            DatabaseBackupError: When an isolated load's dump holds ``USE`` or
                ``CREATE DATABASE``: it was taken with ``--databases`` or
                ``--all-databases``, and loading it would write into the
                database it came from, production included.
        """
        if not kwargs.get("isolated"):
            return
        found = statement_leaving_the_database(backup_path)
        if found is not None:
            raise DatabaseBackupError(
                f"{backup_path.name} switches to another database; it cannot be loaded "
                "beside the original",
                details=(
                    f"It contains: {found}\nA dump taken with --databases or "
                    "--all-databases names the database it came from, and loading it "
                    "would write there instead. Restore it over that database, or take "
                    "a dump of the one database without --databases."
                ),
            )

    def _load_backup(self, database: str, backup_path: Path, **kwargs: object) -> None:
        """
        Load a plain or gzipped dump through the client's stdin.

        The staged dump is the client's stdin, read in ``--binary-mode``.
        Named in a ``source`` command instead, the client read it as a
        script, and a ``system`` or ``\\!`` line in a restored dump ran a
        shell as root. Binary mode turns off every client command except
        ``charset`` and ``delimiter`` (which mysqldump's routines and
        triggers need), ``source`` included, which is why the file cannot be
        named and is handed over as stdin. The SQL itself still runs with the
        administrative account's privileges: a restore trusts the dump's
        SQL, which is why it needs sudo mode.

        An isolated load (``isolated=True``: a restore as a new database, a
        restore test) also runs with ``--one-database``, so a ``USE`` that
        slipped past :meth:`_check_backup` makes the client skip what follows
        instead of running it against another database.

        Args:
            database: The database to load into.
            backup_path: The dump as :meth:`_restore_input` prepared it:
                plain, decompressed before anything was dropped.
            **kwargs: ``isolated``, see above.

        Raises:
            DatabaseBackupError: When the client fails or runs out of time;
                the error carries its output.
        """
        isolation = ["--one-database"] if kwargs.get("isolated") else []
        with self._credentials() as credentials:
            result = self._exec(
                [*self._client_argv(credentials, database), "--binary-mode", *isolation],
                stdin_path=backup_path,
                timeout=restore_timeout(backup_path),
            )

        if not result.success:
            raise load_failure(database, result, "The dump may be truncated.")

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
            read_only: Refuse anything that would change data. The server
                enforces it, not a keyword allowlist, because a leading
                keyword does not tell you what a statement does. Enforced by
                connecting as a dedicated, least-privilege account: see
                :meth:`_ensure_read_only_user`.
            **kwargs: Unused.

        Returns:
            Success and the statement's output.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the statement is refused before it runs
                (see :meth:`_console_exec`) or fails, or the read-only account
                cannot be provisioned.
        """
        result = self._console_exec(database, query, read_only=read_only, headers=False)
        if not result.success:
            raise DatabaseQueryError("Query failed", details=result.stderr.strip())
        return True, result.stdout

    def _console_exec(
        self,
        database: str,
        query: str,
        *,
        read_only: bool,
        headers: bool,
        timeout_s: int | None = None,
    ) -> CommandResult:
        """
        Run an operator's console statement with client commands disabled.

        The statement reaches the client on stdin - off the command line, so
        a write that carries a password is not in ``ps`` - and stdin is a
        script to mysql: ``system``/``\\!`` run a shell as root, ``source``
        reads a file, ``tee`` writes one, wherever a statement begins.
        ``--binary-mode`` is the switch that turns every client command but
        ``charset`` and ``delimiter`` off for non-interactive input (MySQL
        and MariaDB alike); :func:`_refuse_client_commands` refuses them by
        name first, with an error that says why.

        Args:
            database: Database to select.
            query: The operator's statement.
            read_only: Run it inside a read-only transaction as the
                least-privilege account, never as the configured one.
            headers: Keep the column header row, for the structured result.
            timeout_s: Seconds the server may spend on the statement; the
                server's own setting when None.

        Returns:
            The client's outcome.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the statement is refused, or the
                read-only account cannot be provisioned.
        """
        statement = console_statement(query, read_only=read_only)
        _refuse_client_commands(statement)
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")

        limit = self.statement_timeout_sql(timeout_s)
        if read_only:
            # The terminator sits on its own line: a statement ending in a
            # "-- comment" would otherwise swallow it and merge with COMMIT.
            sql = f"{limit}START TRANSACTION READ ONLY;\n{statement}\n;\nCOMMIT;\n"
            with self._read_only_credentials(database) as (credentials, env):
                return self._exec(
                    [*self._client_argv(credentials, database, headers=headers), "--binary-mode"],
                    input=sql,
                    env=env,
                    timeout=query_deadline(timeout_s),
                )
        with self._credentials() as credentials:
            return self._exec(
                [*self._client_argv(credentials, database, headers=headers), "--binary-mode"],
                input=f"{limit}{statement}",
                timeout=query_deadline(timeout_s),
            )

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
        Run a statement once and parse its batch output into columns and rows.

        One execution, not two, for the reason :meth:`PostgresManager
        <noust.managers.database.postgres.PostgresManager.execute_query_structured>`
        gives: a second run to also produce the older headerless format
        would apply a write statement twice.

        Args:
            database: Database name.
            query: The statement.
            read_only: Same enforcement as :meth:`execute_query`: a
                dedicated, least-privilege account provisioned by
                :meth:`_ensure_read_only_user`.
            max_rows: Data rows kept before the rest are dropped.
            timeout_s: Seconds the server may spend on the statement
                (``max_statement_time`` on MariaDB, ``max_execution_time`` on
                MySQL, where it bounds SELECTs only).

        Returns:
            The parsed result. Batch output prints a NULL as ``NULL``, which
            is read as None: a text value that is the four letters ``NULL``
            reads as NULL too, which the data explorer, reading JSON, does not.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the statement is refused before it runs
                (see :meth:`_console_exec`) or fails, or the read-only account
                cannot be provisioned.
        """
        result = self._console_exec(
            database, query, read_only=read_only, headers=True, timeout_s=timeout_s
        )
        if not result.success:
            raise DatabaseQueryError(
                "Query failed", details=(result.stderr or result.stdout).strip()
            )

        columns, parsed, truncated = parse_tabular_query_output(
            result.stdout, delimiter="\t", max_rows=max_rows
        )
        rows: list[list[str | None]] = [
            [None if cell == "NULL" else cell for cell in row] for row in parsed
        ]
        return StructuredQueryResult(
            output=result.stdout,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            duration_ms=result.duration * 1000,
            truncated=truncated,
        )

    # ==================== Statements Noust builds (3.1) ====================

    @property
    def is_mariadb(self) -> bool:
        """Whether the server is MariaDB, whose statement timeout and ANALYZE differ."""
        return self.EOL_FAMILY == "mariadb"

    def statement_timeout_sql(self, timeout_s: int | None) -> str:
        """
        Build the statement that bounds how long the session's statements run.

        Args:
            timeout_s: Seconds, or None for the server's own setting.

        Returns:
            ``SET SESSION max_statement_time`` on MariaDB (every statement,
            in seconds), ``SET SESSION max_execution_time`` on MySQL (SELECTs
            only, in milliseconds), or nothing.
        """
        if timeout_s is None:
            return ""
        if self.is_mariadb:
            return f"SET SESSION max_statement_time = {int(timeout_s)};\n"
        return f"SET SESSION max_execution_time = {int(timeout_s) * 1000};\n"

    def run_sql(
        self,
        database: str | None,
        sql: str,
        *,
        read_only: bool,
        timeout_s: int | None = None,
    ) -> str:
        """
        Run statements Noust built - the data explorer's, the row editor's,
        the metrics' - and return what they printed.

        The text travels on stdin, off the command line, and the client
        prints raw (``--raw``): each result row is one line with nothing
        escaped, which is what lets a row rendered as one JSON document be
        read back as JSON. Values reach the text only as literals
        :mod:`noust.managers.database.dialects` built.

        Args:
            database: Database to select; None for server-wide statements.
            sql: The statements.
            read_only: Run as the database's read-only account inside a
                read-only transaction, reusing an account provisioned in the
                last :data:`_PROVISION_REUSE_SECONDS`; otherwise as the
                configured administrative account.
            timeout_s: Seconds the server may spend on each statement.

        Returns:
            The client's output.

        Raises:
            DatabaseQueryError: When a statement fails, with the server's own
                message; or the read-only account cannot be provisioned.
        """
        limit = self.statement_timeout_sql(timeout_s)
        if read_only and database:
            script = f"{limit}START TRANSACTION READ ONLY;\n{sql.rstrip()}\n;\nCOMMIT;\n"
            cached = _provisioned.get(f"{self.ENGINE_NAME}/{database}")
            fresh = (
                (cached[0], cached[1])
                if cached and time.monotonic() - cached[2] <= _PROVISION_REUSE_SECONDS
                else None
            )
            attempts: list[tuple[str, str] | None] = [fresh] if fresh else []
            attempts.append(None)
            for account in attempts:
                if account is None:
                    account = self._ensure_read_only_user(database)
                    _provisioned[f"{self.ENGINE_NAME}/{database}"] = (*account, time.monotonic())
                with self._read_only_credentials(database, account=account) as (credentials, env):
                    result = self._exec(
                        [*self._client_argv(credentials, database), "--raw", "--binary-mode"],
                        input=script,
                        env=env,
                        timeout=query_deadline(timeout_s),
                    )
                if result.success or _ACCESS_DENIED not in result.stderr:
                    break
        else:
            with self._credentials() as credentials:
                result = self._exec(
                    [*self._client_argv(credentials, database), "--raw", "--binary-mode"],
                    input=f"{limit}{sql}",
                    timeout=query_deadline(timeout_s),
                )
        if not result.success:
            raise DatabaseQueryError(
                f"{self.DISPLAY_NAME} refused a statement"
                + (f" on '{database}'" if database else ""),
                details=f"{self.DISPLAY_NAME}'s own message follows.",
                output=(result.stderr or result.stdout).strip(),
            )
        return result.stdout

    def explain(
        self, database: str, statement: str, *, analyze: bool = False, timeout_s: int | None = None
    ) -> str:
        """
        Show how the server would run an operator's statement.

        Plain EXPLAIN (``FORMAT=JSON``) runs as the read-only account inside
        a read-only transaction, like the console's read mode. ``analyze``
        executes the statement to time it (MariaDB's ``ANALYZE
        FORMAT=JSON``, MySQL's ``EXPLAIN ANALYZE``, which prints a tree), so
        it runs as the administrative account inside a transaction that is
        rolled back. The statement is checked exactly as a console statement
        is: one statement, no client command.

        Args:
            database: The database.
            statement: The operator's statement.
            analyze: Execute it and report real timings.
            timeout_s: Seconds the server may spend on it.

        Returns:
            The plan as the server printed it: JSON, or MySQL's analyzed tree.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the statement is refused or fails.
        """
        checked = console_statement(statement, read_only=True)
        _refuse_client_commands(checked)
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")
        limit = self.statement_timeout_sql(timeout_s)
        if analyze:
            verb = "ANALYZE FORMAT=JSON" if self.is_mariadb else "EXPLAIN ANALYZE"
            script = f"{limit}START TRANSACTION;\n{verb} {checked}\n;\nROLLBACK;\n"
            with self._credentials() as credentials:
                result = self._exec(
                    [*self._client_argv(credentials, database), "--raw", "--binary-mode"],
                    input=script,
                    timeout=query_deadline(timeout_s),
                )
        else:
            script = (
                f"{limit}START TRANSACTION READ ONLY;\nEXPLAIN FORMAT=JSON {checked}\n;\nCOMMIT;\n"
            )
            with self._read_only_credentials(database) as (credentials, env):
                result = self._exec(
                    [*self._client_argv(credentials, database), "--raw", "--binary-mode"],
                    input=script,
                    env=env,
                    timeout=query_deadline(timeout_s),
                )
        if not result.success:
            raise DatabaseQueryError(
                "EXPLAIN failed", details=(result.stderr or result.stdout).strip()
            )
        return result.stdout

    def server_port(self) -> int:
        """
        Return the port the server listens on, as it reports it.

        Returns:
            ``@@port``, or :attr:`DEFAULT_PORT` when the server cannot say;
            for a container, the port it is reached on from the host.
        """
        if self.instance is not None:
            return self.instance.port
        cached = getattr(self, "_port", None)
        if cached is not None:
            return int(cached)
        success, output = self._execute_sql("SELECT @@port;")
        value = output.strip()
        if success and value.isdigit() and 0 < int(value) < 65536:
            self._port = int(value)
            return self._port
        return self.DEFAULT_PORT

    # ==================== Passwords, profiles, exposure ====================

    def _account(self, username: str, host: str) -> str:
        """
        Quote a ``'user'@'host'`` account.

        Args:
            username: The user, already validated.
            host: The host, already validated.

        Returns:
            The quoted account.
        """
        return f"{self._escape_literal(username)}@{self._escape_literal(host)}"

    def set_user_password(self, username: str, password: str, host: str = "localhost") -> None:
        """
        Give an account a new password, with the statement on stdin.

        Args:
            username: The user.
            password: Its new password.
            host: Host the account connects from.

        Raises:
            DatabaseUserError: When the account does not exist or the server
                refuses.
        """
        self.validate_user_name(username)
        self.validate_host(host)
        if not self.user_exists(username, host):
            raise DatabaseUserError(
                f"User '{username}'@'{host}' does not exist",
                details="Run 'noust db user-list --engine mysql' to see the users.",
            )
        success, output = self._execute_sql(
            f"ALTER USER {self._account(username, host)} "
            f"IDENTIFIED BY {self._escape_literal(password)};\nFLUSH PRIVILEGES;",
            secrets=(password,),
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to change the password of '{username}'@'{host}'",
                details=output.strip(),
            )
        self.logger.info(f"Changed the password of: {username}@{host}")

    def apply_profile(
        self, username: str, database: str, profile: str, host: str = "localhost"
    ) -> None:
        """
        Give an account exactly one access profile on a database.

        ``owner`` is ALL PRIVILEGES on the database (MySQL has no owner),
        ``read_write`` SELECT, INSERT, UPDATE and DELETE, ``read_only``
        SELECT. What the account held on the database is revoked first; a
        SELECT is granted before the revoke because MySQL refuses to revoke
        from an account that holds nothing there.

        Args:
            username: The user.
            database: The database.
            profile: ``owner``, ``read_write`` or ``read_only``.
            host: Host the account connects from.

        Raises:
            DatabaseUserError: When the profile is unknown or the server
                refuses.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        self.validate_host(host)
        if profile not in PROFILES:
            raise DatabaseUserError(
                f"Unknown access profile: {profile!r}",
                details=f"Use one of: {', '.join(PROFILES)}.",
            )
        account = self._account(username, host)
        scope = f"{self._escape_identifier(database)}.*"
        privileges = ", ".join(PROFILE_PRIVILEGES[profile])
        success, output = self._execute_sql(
            f"GRANT SELECT ON {scope} TO {account};\n"  # noqa: S608 - quoted identifiers
            f"REVOKE ALL PRIVILEGES ON {scope} FROM {account};\n"
            f"GRANT {privileges} ON {scope} TO {account};\n"
            "FLUSH PRIVILEGES;\n"
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to give '{username}'@'{host}' the {profile} profile on '{database}'",
                details=output.strip(),
            )
        self.logger.info(f"{username}@{host} now has the {profile} profile on {database}")

    def list_access(self, database: str) -> list[AccessEntry]:
        """
        List the accounts with database-level grants on a database.

        One query over ``mysql.db``. Global grants (root's) and table-level
        grants are not per-database access and are not listed.

        Args:
            database: The database.

        Returns:
            One entry per account, with the profile its grants amount to.

        Raises:
            DatabaseUserError: When the grant table cannot be read.
        """
        self.validate_database_name(database)
        columns = ", ".join(_PROFILE_COLUMNS)
        success, output = self._execute_sql(
            f"SELECT User, Host, {columns} FROM mysql.db "  # noqa: S608 - quoted literal, not interpolated data
            f"WHERE Db = {self._escape_literal(database)} ORDER BY User, Host;"
        )
        if not success:
            raise DatabaseUserError(
                f"Could not read who can reach '{database}'", details=output.strip()
            )
        entries: list[AccessEntry] = []
        for line in output.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 2 + len(_PROFILE_COLUMNS):
                continue
            granted = {
                column.removesuffix("_priv").upper()
                for column, flag in zip(_PROFILE_COLUMNS, parts[2:], strict=False)
                if flag == "Y"
            }
            data = {"SELECT", "INSERT", "UPDATE", "DELETE"}
            if granted >= data | {"CREATE", "DROP", "ALTER", "INDEX"}:
                profile = "owner"
            elif granted == data:
                profile = "read_write"
            elif granted == {"SELECT"}:
                profile = "read_only"
            else:
                profile = "custom"
            entries.append(
                AccessEntry(
                    username=parts[0],
                    host=parts[1],
                    profile=profile,
                    privileges=tuple(sorted(granted)),
                    internal=self.is_internal_user(parts[0]),
                )
            )
        return entries

    def drop_read_only_account(self, database: str) -> None:
        """
        Drop the read-only console's account of a dropped database.

        Args:
            database: The dropped database.
        """
        account = self._account(_read_only_user_name(database), "localhost")
        success, output = self._execute_sql(f"DROP USER IF EXISTS {account};")
        if not success:
            self.logger.warning(f"Could not drop the read-only account {account}: {output.strip()}")

    def listen_addresses(self) -> ListenAddress | None:
        """
        Ask the server for ``bind_address``.

        Returns:
            The addresses, or None when the server cannot say.
        """
        success, output = self._execute_sql("SELECT @@bind_address;")
        if not success or not output.strip():
            return None
        return listen_address("bind-address", output.strip(), separator=",")

    def get_interactive_command(
        self,
        database: str | None = None,
        username: str | None = None,
    ) -> list[str]:
        """
        Build the command that opens a mysql session.

        Args:
            database: Database to select.
            username: User to connect as.

        Returns:
            The argument vector. The client prompts for the password itself,
            except inside a container, where the administrative account signs
            in the way every other command does.
        """
        argv = ["mysql"]
        if username:
            argv.extend(["-u", username, "-p"])
        elif self.instance is not None:
            argv.extend(["-u", self.instance.admin_user])
        if database:
            argv.append(database)
        return self._interactive(argv)


DatabaseRegistry.register(MySQLManager, aliases=["mariadb", "maria"])
