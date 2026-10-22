# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
ClickHouse as an engine Noust manages (checklist C-06).

ClickHouse is an analytical column store with a SQL dialect and an access control
of its own (users and grants kept in SQL, ``CREATE USER`` and ``GRANT``). It runs
only as an **instance** Noust makes from the project's official image, pinned by
digest (:mod:`noust.managers.database.instance_catalog`): no host install exists
to manage, so this manager always drives a container, through ``docker exec``
like every engine in one.

What it does:

- **Databases**: create, drop, list with their size on disk and their tables.
- **Accounts and profiles**: users with a password, ``GRANT``/``REVOKE`` on a
  database, and the owner / read-write / read-only profiles the other engines
  have. The image creates Noust's administrator (``noust_admin``) with the
  right to manage accounts (``CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT``).
- **Backups and restores**, with ClickHouse's own ``BACKUP`` and ``RESTORE``:
  the server writes a ``.zip`` to a local disk Noust's configuration gives it
  (``noust-backups`` inside the data volume), Noust copies it out, and deletes
  it from the container. A restore copies the file back in and ``RESTORE``s it
  under the name asked. The database inside every backup is called
  ``noust_dump``, whatever it was called when it was taken, so a backup can be
  restored under any name.

What it does not do yet: the SQL console and the data explorer (ClickHouse
enforces a read-only session with a setting the client passes, which needs
trying against a real server before it is offered as the guarantee the other
engines give), TLS, engine settings, metrics, moves between engines, and public
access. The capabilities this manager declares are what the console draws.

Secrets: every statement travels on the client's standard input, so a password
is in no argument; the client reads the administrator's password from the
container's own environment into a file the image's shell script removes
(:data:`~noust.managers.database.instances.CLIENT_SCRIPT`, mode ``clickhouse``).
"""

from __future__ import annotations

import secrets
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import (
    DatabaseBackupError,
    DatabaseEngineError,
    DatabaseError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    DatabaseUserError,
)
from noust.core.runner import CommandResult
from noust.managers.database.base import (
    PROFILES,
    QUERY_TIMEOUT,
    SERVICE_TIMEOUT,
    TRANSFER_TIMEOUT,
    AccessEntry,
    BackupInfo,
    BaseDatabaseManager,
    DatabaseInfo,
    UserInfo,
    format_size,
    load_failure,
    restore_timeout,
)
from noust.managers.database.instance_compose import (
    CLICKHOUSE_ADMIN,
    CLICKHOUSE_BACKUP_DIR,
    CLICKHOUSE_BACKUP_DISK,
)
from noust.managers.database.registry import DatabaseRegistry
from noust.managers.database.urls import connection_url

if TYPE_CHECKING:
    from noust.managers.database.instances import DatabaseInstance

#: The name every backup holds its database under, whatever it was called when it was
#: taken: ``BACKUP DATABASE shop AS noust_dump``. A restore names it, and ``AS`` the
#: database it goes into, so one backup restores under any name.
DUMP_NAME = "noust_dump"

#: Databases ClickHouse keeps for itself.
SYSTEM_DATABASES = frozenset({"system", "INFORMATION_SCHEMA", "information_schema"})

#: The privileges each access profile gives on one database. ``SHOW`` lets the account
#: list the tables it can read.
PROFILE_PRIVILEGES: dict[str, tuple[str, ...]] = {
    "owner": ("ALL",),
    "read_write": ("SELECT", "INSERT", "ALTER UPDATE", "ALTER DELETE", "TRUNCATE", "SHOW"),
    "read_only": ("SELECT", "SHOW"),
}

#: The native protocol's port, which a URL for a client that speaks it names.
NATIVE_PORT = 9000


def identifier(value: str) -> str:
    """
    Quote a name for ClickHouse.

    Args:
        value: A database or user name (already validated; quoting is the second defence).

    Returns:
        The name in backticks, with a backtick or a backslash inside it escaped.
    """
    return "`" + value.replace("\\", "\\\\").replace("`", "\\`") + "`"


def literal(value: str) -> str:
    """
    Write a string as a ClickHouse literal.

    Args:
        value: Any text.

    Returns:
        The text in single quotes, with a quote or a backslash inside it escaped.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def unescape(value: str) -> str:
    """
    Undo TabSeparated's escaping of one field.

    Args:
        value: A field as the client printed it.

    Returns:
        The text.
    """
    if "\\" not in value:
        return value
    table = {"t": "\t", "n": "\n", "\\": "\\", "'": "'", "0": "\0", "r": "\r", "b": "\b"}
    out: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            out.append(table.get(value[index + 1], value[index + 1]))
            index += 2
        else:
            out.append(character)
            index += 1
    return "".join(out)


class ClickHouseManager(BaseDatabaseManager):
    """Manager for a ClickHouse instance."""

    ENGINE_NAME = "clickhouse"
    DISPLAY_NAME = "ClickHouse"
    DEFAULT_PORT = 8123
    SERVICE_NAME = "clickhouse-server"
    CLIENT_BINARY = "clickhouse-client"
    VERSION_ARGV = ("clickhouse-client", "--version")
    VERSION_PATTERN = r"version (\d+\.\d+\.\d+)"
    BACKUP_SUFFIX = ".zip"
    MAX_DATABASE_NAME_LENGTH = 63
    MAX_USER_NAME_LENGTH = 63
    CAPABILITIES = frozenset({"users", "profiles", "dump"})
    CONTAINER_ONLY = True
    INTERNAL_USERS = frozenset({"default", CLICKHOUSE_ADMIN})
    VALID_PRIVILEGES = frozenset(
        {
            "ALL",
            "ALL PRIVILEGES",
            "SELECT",
            "INSERT",
            "ALTER",
            "ALTER UPDATE",
            "ALTER DELETE",
            "CREATE",
            "CREATE TABLE",
            "CREATE VIEW",
            "DROP",
            "DROP TABLE",
            "DROP VIEW",
            "TRUNCATE",
            "OPTIMIZE",
            "SHOW",
        }
    )
    DEFAULT_PRIVILEGES = ("ALL",)

    # ==================== Installation ====================

    def is_installed(self) -> bool:
        """
        Report whether this manager has an instance to drive.

        Returns:
            True for a container; False for the host, where Noust never installs it.
        """
        return self.instance is not None

    def _bind_names(self, instance: DatabaseInstance) -> None:
        """
        Name the engine after the container's image.

        Args:
            instance: The container.
        """
        self.DISPLAY_NAME = "ClickHouse"

    def install(self, plan: Any = None) -> None:
        """
        Refuse to install ClickHouse on the host.

        Args:
            plan: Unused.

        Raises:
            DatabaseEngineError: Always: ClickHouse runs as an instance.
        """
        self.refuse_in_container("install")
        raise DatabaseEngineError(
            "Noust runs ClickHouse as an instance, not as a package on the host",
            details="Make one with: noust db instance create NAME --engine clickhouse",
        )

    def _require_instance(self) -> DatabaseInstance:
        """
        Return the container this manager drives.

        Returns:
            The instance.

        Raises:
            DatabaseEngineError: This manager is the host's, which has no ClickHouse.
        """
        if self.instance is None:
            raise DatabaseEngineError(
                "ClickHouse runs as an instance Noust makes, and there is none to use here",
                details="Make one with: noust db instance create NAME --engine clickhouse, "
                "then address it as clickhouse@NAME.",
            )
        return self.instance

    # ==================== SQL ====================

    def _sql(
        self,
        sql: str,
        database: str | None = None,
        *,
        secrets_in: Sequence[str] = (),
        timeout: int = QUERY_TIMEOUT,
    ) -> CommandResult:
        """
        Run statements with the client, which reads them on its standard input.

        Args:
            sql: One or more statements.
            database: Database to select.
            secrets_in: Values the statements carry that must not be logged.
            timeout: Deadline in seconds.

        Returns:
            What the client returned.
        """
        self._require_instance()
        argv = ["clickhouse-client", "--multiquery", "--format", "TabSeparated"]
        if database:
            argv += ["--database", database]
        return self._exec(argv, input=sql, secrets=secrets_in, timeout=timeout)

    def _execute_sql(
        self,
        sql: str,
        database: str | None = None,
        *,
        secrets_in: Sequence[str] = (),
        timeout: int = QUERY_TIMEOUT,
    ) -> tuple[bool, str]:
        """
        Run statements and say how it went.

        Args:
            sql: One or more statements.
            database: Database to select.
            secrets_in: Values the statements carry that must not be logged.
            timeout: Deadline in seconds.

        Returns:
            Whether the client succeeded, and its output or its error text.
        """
        result = self._sql(sql, database, secrets_in=secrets_in, timeout=timeout)
        return result.success, result.stdout if result.success else (result.stderr or result.stdout)

    def _rows(self, sql: str, what: str) -> list[list[str]]:
        """
        Read a listing: the rows of a query, as fields.

        Args:
            sql: A SELECT.
            what: What is being listed, for the error.

        Returns:
            One list of fields per row.

        Raises:
            DatabaseAccessError: ClickHouse does not let Noust in.
            DatabaseQueryError: The listing failed: an empty answer is never made up.
        """
        success, output = self._execute_sql(sql)
        if not success:
            self._listing_failed(what, output)
        return [
            [unescape(field) for field in line.split("\t")]
            for line in output.strip("\n").splitlines()
            if line.strip()
        ]

    # ==================== Database Management ====================

    def create_database(
        self,
        name: str,
        owner: str | None = None,
        encoding: str | None = None,
        **kwargs: Any,
    ) -> DatabaseInfo:
        """
        Create a database.

        Args:
            name: Database name.
            owner: Account to give every privilege on it.
            encoding: Unused; ClickHouse stores UTF-8.
            **kwargs: Unused.

        Returns:
            Information about the new database.

        Raises:
            DatabaseExistsError: The database exists.
            DatabaseError: The name is invalid or the creation fails.
        """
        self.validate_database_name(name)
        if name in SYSTEM_DATABASES or self.database_exists(name):
            raise DatabaseExistsError(
                f"Database '{name}' already exists",
                details="Drop it first, or pick another name.",
            )
        success, output = self._execute_sql(f"CREATE DATABASE {identifier(name)}")
        if not success:
            raise DatabaseError(f"Failed to create database '{name}'", details=output.strip())
        if owner:
            self.grant_privileges(owner, name)
        self.logger.info(f"Created database: {name}")
        return self.get_database_info(name)

    def drop_database(self, name: str, force: bool = False) -> None:
        """
        Drop a database, and wait for its data to go.

        ``SYNC`` makes the files go now: without it ClickHouse keeps a dropped
        table's data for minutes, and a restore under the same name finds it.

        Args:
            name: Database name.
            force: Accept a missing database as success.

        Raises:
            DatabaseNotFoundError: The database does not exist.
            DatabaseError: The database is ClickHouse's own, or the drop fails.
        """
        self.validate_database_name(name)
        if name in SYSTEM_DATABASES:
            raise DatabaseError(
                f"'{name}' is one of ClickHouse's own databases",
                details="Noust never drops the databases the server keeps for itself.",
            )
        if not self.database_exists(name):
            if force:
                return
            raise DatabaseNotFoundError(
                f"Database '{name}' does not exist",
                details=f"Run 'noust db list --engine {self.ENGINE_NAME}' to see the databases.",
            )
        success, output = self._execute_sql(f"DROP DATABASE {identifier(name)} SYNC")
        if not success:
            raise DatabaseError(f"Failed to drop database '{name}'", details=output.strip())
        self.logger.info(f"Dropped database: {name}")

    def database_exists(self, name: str) -> bool:
        """
        Report whether a database exists.

        Args:
            name: Database name.

        Returns:
            True when ``system.databases`` holds it.

        Raises:
            DatabaseAccessError: ClickHouse does not let Noust in.
            DatabaseQueryError: It cannot be asked: "no" would let a drop skip its last dump.
        """
        rows = self._rows(
            f"SELECT count() FROM system.databases WHERE name = {literal(name)}",  # noqa: S608 - quoted literal
            "databases",
        )
        return bool(rows) and rows[0][0] == "1"

    def list_databases(self) -> list[DatabaseInfo]:
        """
        List the databases that are not ClickHouse's own.

        Returns:
            One entry per database, with the bytes its active parts take on disk and
            its tables, from one query.
        """
        rows = self._rows(
            "SELECT d.name, ifNull(p.bytes, 0), ifNull(t.tables, 0), d.engine "
            "FROM system.databases AS d "
            "LEFT JOIN (SELECT database, sum(bytes_on_disk) AS bytes FROM system.parts "
            "WHERE active GROUP BY database) AS p ON p.database = d.name "
            "LEFT JOIN (SELECT database, count() AS tables FROM system.tables "
            "GROUP BY database) AS t ON t.database = d.name "
            "WHERE d.name NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema') "
            "ORDER BY d.name",
            "databases",
        )
        return [self._info(row) for row in rows if len(row) >= 4]

    def _info(self, row: Sequence[str]) -> DatabaseInfo:
        """
        Describe a database from a row of :meth:`list_databases`.

        Args:
            row: Name, bytes, tables, database engine.

        Returns:
            The description.
        """
        size = int(row[1]) if row[1].isdigit() else 0
        return DatabaseInfo(
            name=row[0],
            engine=self.ENGINE_NAME,
            size=format_size(size),
            tables=int(row[2]) if row[2].isdigit() else 0,
            extra={"database_engine": row[3], "size_bytes": size},
        )

    def get_database_info(self, name: str) -> DatabaseInfo:
        """
        Describe one database.

        Args:
            name: Database name.

        Returns:
            Size on disk and table count.

        Raises:
            DatabaseNotFoundError: The database does not exist.
        """
        for info in self.list_databases():
            if info.name == name:
                return info
        raise DatabaseNotFoundError(f"Database '{name}' does not exist")

    # ==================== User Management ====================

    def user_exists(self, username: str, host: str = "localhost") -> bool:
        """
        Report whether an account exists.

        Args:
            username: Account name.
            host: Unused; ClickHouse restricts hosts per account, not per name.

        Returns:
            True when ``system.users`` holds it.
        """
        rows = self._rows(
            f"SELECT count() FROM system.users WHERE name = {literal(username)}",  # noqa: S608 - quoted literal
            "users",
        )
        return bool(rows) and rows[0][0] == "1"

    def create_user(
        self,
        username: str,
        password: str | None = None,
        host: str = "localhost",
        **kwargs: Any,
    ) -> tuple[UserInfo, str]:
        """
        Create an account.

        The statement carries the password, so it travels on the client's standard
        input; the server stores a SHA-256 of it.

        Args:
            username: Account name.
            password: Password; generated when omitted.
            host: Unused.
            **kwargs: Unused.

        Returns:
            The account and its password.

        Raises:
            DatabaseUserError: The account exists or the creation fails.
        """
        self.validate_user_name(username)
        if self.user_exists(username, host):
            raise DatabaseUserError(
                f"User '{username}' already exists",
                details="Drop the user first, or pick another name.",
            )
        password = password or self.generate_password()
        success, output = self._execute_sql(
            f"CREATE USER {identifier(username)} IDENTIFIED WITH sha256_password "
            f"BY {literal(password)}",
            secrets_in=(password,),
        )
        if not success:
            raise DatabaseUserError(f"Failed to create user '{username}'", details=output.strip())
        self.logger.info(f"Created user: {username}")
        return UserInfo(username=username, engine=self.ENGINE_NAME, host="%"), password

    def drop_user(self, username: str, host: str = "localhost") -> None:
        """
        Drop an account.

        Args:
            username: Account name.
            host: Unused.

        Raises:
            DatabaseUserError: The account is missing or the drop fails.
        """
        self.validate_user_name(username)
        if not self.user_exists(username, host):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details=f"Run 'noust db user-list --engine {self.ENGINE_NAME}' to see the users.",
            )
        success, output = self._execute_sql(f"DROP USER {identifier(username)}")
        if not success:
            raise DatabaseUserError(f"Failed to drop user '{username}'", details=output.strip())
        self.logger.info(f"Dropped user: {username}")

    def list_users(self) -> list[UserInfo]:
        """
        List the accounts, with the databases each holds a grant on.

        Returns:
            One entry per account. Two queries (accounts, then grants) however many
            there are.
        """
        users = self._rows("SELECT name FROM system.users ORDER BY name", "users")
        grants = self._rows(
            "SELECT user_name, database, access_type FROM system.grants "
            "WHERE user_name IS NOT NULL AND database IS NOT NULL "
            "ORDER BY user_name, database, access_type",
            "users",
        )
        databases: dict[str, list[str]] = {}
        privileges: dict[str, list[str]] = {}
        for row in grants:
            if len(row) < 3:
                continue
            if row[1] not in databases.setdefault(row[0], []):
                databases[row[0]].append(row[1])
            if row[2] not in privileges.setdefault(row[0], []):
                privileges[row[0]].append(row[2])
        return [
            UserInfo(
                username=row[0],
                engine=self.ENGINE_NAME,
                host="%",
                databases=databases.get(row[0], []),
                privileges=privileges.get(row[0], []),
            )
            for row in users
            if row
        ]

    def grant_privileges(
        self,
        username: str,
        database: str,
        privileges: Sequence[str] | None = None,
        host: str = "localhost",
    ) -> None:
        """
        Grant whitelisted privileges on a database.

        Args:
            username: Account name.
            database: Database name.
            privileges: Privileges; ALL when omitted.
            host: Unused.

        Raises:
            DatabaseUserError: A privilege is not whitelisted or the grant fails.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        granted = self.validate_privileges(privileges)
        success, output = self._execute_sql(
            f"GRANT {', '.join(granted)} ON {identifier(database)}.* TO {identifier(username)}"
        )
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
        Revoke whitelisted privileges on a database.

        Args:
            username: Account name.
            database: Database name.
            privileges: Privileges; ALL when omitted.
            host: Unused.

        Raises:
            DatabaseUserError: A privilege is not whitelisted or the revoke fails.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        revoked = self.validate_privileges(privileges)
        success, output = self._execute_sql(
            f"REVOKE {', '.join(revoked)} ON {identifier(database)}.* FROM {identifier(username)}"
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to revoke privileges on '{database}' from '{username}'",
                details=output.strip(),
            )
        self.logger.info(f"Revoked {', '.join(revoked)} on {database} from {username}")

    def set_user_password(self, username: str, password: str, host: str = "localhost") -> None:
        """
        Give an account a new password, on the client's standard input.

        Args:
            username: Account name.
            password: Its new password.
            host: Unused.

        Raises:
            DatabaseUserError: The account does not exist or the change fails.
        """
        self.validate_user_name(username)
        if not self.user_exists(username, host):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details=f"Run 'noust db user-list --engine {self.ENGINE_NAME}' to see the users.",
            )
        success, output = self._execute_sql(
            f"ALTER USER {identifier(username)} IDENTIFIED WITH sha256_password "
            f"BY {literal(password)}",
            secrets_in=(password,),
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to change the password of '{username}'", details=output.strip()
            )
        self.logger.info(f"Changed the password of: {username}")

    def apply_profile(
        self, username: str, database: str, profile: str, host: str = "localhost"
    ) -> None:
        """
        Give an account exactly one access profile on a database.

        What it held on that database is revoked first, in the same run, so moving an
        account from read-write to read-only takes the writes away.

        Args:
            username: Account name.
            database: Database name.
            profile: ``owner``, ``read_write`` or ``read_only``.
            host: Unused.

        Raises:
            DatabaseUserError: The profile is unknown, the account is missing, or the
                change fails.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        if profile not in PROFILES:
            raise DatabaseUserError(
                f"Unknown access profile: {profile!r}",
                details=f"Use one of: {', '.join(PROFILES)}.",
            )
        if not self.user_exists(username, host):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details=f"Create it first with 'noust db user-create --engine {self.ENGINE_NAME}'.",
            )
        target = f"{identifier(database)}.*"
        wanted = ", ".join(PROFILE_PRIVILEGES[profile])
        success, output = self._execute_sql(
            f"REVOKE ALL ON {target} FROM {identifier(username)};\n"
            f"GRANT {wanted} ON {target} TO {identifier(username)}"
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to give '{username}' the {profile} profile on '{database}'",
                details=output.strip(),
            )

    def list_access(self, database: str) -> list[AccessEntry]:
        """
        List the accounts holding a grant on a database, with the profile it amounts to.

        Args:
            database: Database name.

        Returns:
            One entry per account. The profile is judged by the data privileges it
            holds (``SHOW`` is ignored): ALL is the owner's, SELECT with INSERT and
            an ALTER is read-write, SELECT alone is read-only, anything else custom.
        """
        rows = self._rows(
            "SELECT user_name, access_type FROM system.grants "  # noqa: S608 - quoted literal
            f"WHERE database = {literal(database)} AND user_name IS NOT NULL "
            "ORDER BY user_name, access_type",
            "users",
        )
        held: dict[str, list[str]] = {}
        for row in rows:
            if len(row) >= 2:
                held.setdefault(row[0], []).append(row[1])
        entries: list[AccessEntry] = []
        for username, types in held.items():
            data = {item for item in types if not item.startswith("SHOW")}
            if data & {"ALL", "ALL PRIVILEGES"}:
                profile = "owner"
            elif data == {"SELECT"}:
                profile = "read_only"
            elif {"SELECT", "INSERT"} <= data and any(item.startswith("ALTER") for item in data):
                profile = "read_write"
            else:
                profile = "custom"
            entries.append(
                AccessEntry(
                    username=username,
                    profile=profile,
                    privileges=tuple(sorted(types)),
                    internal=self.is_internal_user(username),
                )
            )
        return entries

    # ==================== Backup & Restore ====================

    def _disk_path(self, name: str) -> str:
        """
        Name a file of the backups disk inside the container.

        Args:
            name: The file.

        Returns:
            Its path.
        """
        return f"{CLICKHOUSE_BACKUP_DIR}{name}"

    def _prepare_backup_disk(self) -> None:
        """
        Make sure the server can write to, and read from, its backups disk.

        Raises:
            DatabaseBackupError: The container has no such disk (it was not made by
                Noust and its configuration does not define one), or its directory
                cannot be prepared.
        """
        rows = self._rows(
            f"SELECT count() FROM system.disks WHERE name = {literal(CLICKHOUSE_BACKUP_DISK)}",  # noqa: S608 - quoted literal
            "disks",
        )
        if not rows or rows[0][0] != "1":
            raise DatabaseBackupError(
                f"This ClickHouse has no disk called {CLICKHOUSE_BACKUP_DISK}",
                details=(
                    "Noust backs ClickHouse up through a local disk its own instances are "
                    "configured with. For a ClickHouse container Noust did not make, define "
                    f"the disk '{CLICKHOUSE_BACKUP_DISK}' (path {CLICKHOUSE_BACKUP_DIR}) in "
                    "its configuration and allow it under <backups>, or move the data into "
                    "an instance: noust db instance create NAME --engine clickhouse."
                ),
            )
        prepared = self._exec(
            [
                "sh",
                "-c",
                f"mkdir -p {CLICKHOUSE_BACKUP_DIR} && chown clickhouse:clickhouse "
                f"{CLICKHOUSE_BACKUP_DIR}",
            ],
            timeout=SERVICE_TIMEOUT,
        )
        if not prepared.success:
            raise DatabaseBackupError(
                "Could not prepare ClickHouse's backups directory",
                details=(prepared.stderr or prepared.stdout).strip()
                or f"Check that {CLICKHOUSE_BACKUP_DIR} can be written in the container.",
            )

    def _forget_in_container(self, name: str) -> None:
        """
        Delete a backup file from the container, whatever happened to it.

        Args:
            name: The file in the backups disk.
        """
        gone = self._exec(["rm", "-f", self._disk_path(name)], timeout=SERVICE_TIMEOUT)
        if not gone.success:
            self.logger.warning(
                f"Could not remove {self._disk_path(name)} from the container: "
                f"{(gone.stderr or gone.stdout).strip()}"
            )

    def backup(
        self,
        database: str,
        output_path: Path | None = None,
        compress: bool = True,
        **kwargs: Any,
    ) -> BackupInfo:
        """
        Back a database up with ClickHouse's own ``BACKUP``.

        The server writes a ``.zip`` into its backups disk; Noust copies it out, and
        removes it from the container even when the copy fails. The file is never
        compressed again: the archive is.

        Args:
            database: Database name.
            output_path: Custom destination.
            compress: Ignored; a ClickHouse backup is an archive already.
            **kwargs: ``exclude_tables`` is refused; the rest is ignored.

        Returns:
            Information about the backup.

        Raises:
            DatabaseNotFoundError: The database does not exist.
            DatabaseBackupError: The backup fails or leaves tables out (not offered yet).
        """
        self.validate_database_name(database)
        if kwargs.get("exclude_tables") or kwargs.get("exclude_schemas"):
            raise DatabaseBackupError(
                "A ClickHouse backup cannot leave tables out yet",
                details="Back up the whole database, or remove the exclusions from the policy.",
            )
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")
        self._prepare_backup_disk()

        destination = self._backup_path(database, output_path, False)
        name = f"noust-{secrets.token_hex(6)}.zip"
        try:
            success, output = self._execute_sql(
                f"BACKUP DATABASE {identifier(database)} AS {identifier(DUMP_NAME)} "
                f"TO Disk({literal(CLICKHOUSE_BACKUP_DISK)}, {literal(name)})",
                timeout=TRANSFER_TIMEOUT,
            )
            if not success:
                raise DatabaseBackupError(f"Failed to back up '{database}'", details=output.strip())
            return self._dump_to_file(
                ["cat", self._disk_path(name)], destination, database=database, compress=False
            )
        finally:
            self._forget_in_container(name)

    def _check_backup(self, backup_path: Path, **kwargs: Any) -> None:
        """
        Refuse a file that is not a ClickHouse backup, before anything is touched.

        Args:
            backup_path: The backup.
            **kwargs: Unused.

        Raises:
            DatabaseBackupError: It is not an archive, or holds no ``.backup`` metadata.
        """
        problem = backup_problem(backup_path)
        if problem is not None:
            raise DatabaseBackupError(
                f"{backup_path.name} is not a ClickHouse backup Noust can restore",
                details=problem,
            )

    def _create_for_restore(self, database: str, owner: str | None) -> None:
        """
        Do nothing: ``RESTORE DATABASE`` creates the database it restores.

        Args:
            database: The database.
            owner: The owner it had before, if any.
        """

    def _load_backup(self, database: str, backup_path: Path, **kwargs: Any) -> None:
        """
        Copy a backup into the container and ``RESTORE`` it under a name.

        The database must not exist: ClickHouse creates it. Restoring over an existing
        one drops it first (``--drop``), after the safety copy every restore takes.

        Args:
            database: The name to restore under.
            backup_path: The backup.
            **kwargs: Unused.

        Raises:
            DatabaseBackupError: The database exists, or the copy or the restore fails;
                the error carries ClickHouse's own words.
        """
        if self.database_exists(database):
            raise DatabaseBackupError(
                f"Database '{database}' already exists",
                details="A ClickHouse backup is restored into a database that does not exist "
                "yet: restore it under another name, or replace this one with --drop (a "
                "safety copy is taken first).",
            )
        self._prepare_backup_disk()
        name = f"noust-restore-{secrets.token_hex(6)}.zip"
        deadline = restore_timeout(backup_path)
        try:
            copied = self._exec(
                ["sh", "-c", f"cat > {self._disk_path(name)}"],
                stdin_path=backup_path,
                timeout=deadline,
            )
            if not copied.success:
                raise load_failure(database, copied, "The backup could not be copied in.")
            result = self._sql(
                f"RESTORE DATABASE {identifier(DUMP_NAME)} AS {identifier(database)} "
                f"FROM Disk({literal(CLICKHOUSE_BACKUP_DISK)}, {literal(name)})",
                timeout=deadline,
            )
            if not result.success:
                raise load_failure(
                    database,
                    result,
                    f"ClickHouse found no database called {DUMP_NAME} in the backup: only "
                    "backups Noust took are restored.",
                )
        finally:
            self._forget_in_container(name)

    # ==================== Query Execution ====================

    def execute_query(
        self,
        database: str,
        query: str,
        *,
        read_only: bool = False,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        """
        Run a statement as the administrator.

        Args:
            database: Database name.
            query: The statement.
            read_only: Refused: ClickHouse has no read-only session Noust can guarantee yet.
            **kwargs: Unused.

        Returns:
            Success and the statement's output.

        Raises:
            DatabaseNotFoundError: The database does not exist.
            DatabaseQueryError: The statement fails, or a read-only run was asked for.
        """
        if read_only:
            raise DatabaseQueryError(
                "ClickHouse has no read-only mode in Noust yet",
                details="Run the statement as the administrator (without --read-only), or "
                "create an account with the read_only profile and connect with it.",
            )
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")
        success, output = self._execute_sql(query, database)
        if not success:
            raise DatabaseQueryError("Query failed", details=output.strip())
        return True, output

    # ==================== Connection ====================

    def get_connection_string(
        self,
        database: str,
        username: str,
        password: str,
        host: str = "localhost",
    ) -> str:
        """
        Build the URL an application connects with.

        The HTTP interface is what applications use, and the port an instance publishes;
        a container published on the native port (9000) gets a ``clickhouse://`` URL.

        Args:
            database: Database name.
            username: Account name.
            password: Its password.
            host: Host to connect to.

        Returns:
            The URL.
        """
        port = self.server_port()
        return connection_url(
            "clickhouse" if port == NATIVE_PORT else "http",
            database=database,
            user=username,
            password=password,
            host=host,
            port=port,
        )

    def get_interactive_command(
        self,
        database: str | None = None,
        username: str | None = None,
    ) -> list[str]:
        """
        Build the command that opens ClickHouse's shell as the administrator.

        Args:
            database: Database to open.
            username: Ignored: the session signs in as the administrator.

        Returns:
            The argument vector.
        """
        self._require_instance()
        argv = ["clickhouse-client"]
        if database:
            argv += ["--database", database]
        return self._interactive(argv)

    def warnings(self) -> list[str]:
        """
        Warn about an administrator with no password that something outside can reach.

        Returns:
            The warning and what to do, or nothing.
        """
        instance = self.instance
        if instance is None or instance.managed is not None:
            return []
        reached = instance.reach_words()
        if reached is None or "CLICKHOUSE_PASSWORD" in instance.env_names:
            return []
        where, who = reached
        return [
            f"ClickHouse in {instance.container} has no password for its default user and "
            f"{where}: {who} can read and change every database. Set CLICKHOUSE_USER, "
            "CLICKHOUSE_PASSWORD and CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1 in the compose "
            "file and recreate the container."
        ]


def backup_problem(path: Path) -> str | None:
    """
    Say why a file is not a ClickHouse backup.

    Reads the archive's directory only: a ClickHouse backup is a zip with a ``.backup``
    metadata file at its root, written last.

    Args:
        path: The file.

    Returns:
        What is wrong, or None when it is whole.
    """
    try:
        if not zipfile.is_zipfile(path):
            return "It is not a zip archive."
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if ".backup" not in names:
                return "The archive holds no .backup metadata: it was cut short, or it is not a backup."
            bad = archive.testzip()
            if bad is not None:
                return f"{bad} in the archive fails its checksum."
    except (OSError, zipfile.BadZipFile, EOFError) as exc:
        return f"The archive cannot be read: {exc}"
    return None


DatabaseRegistry.register(ClickHouseManager, aliases=["ch", "click-house"])
