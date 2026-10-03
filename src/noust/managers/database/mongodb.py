# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
MongoDB manager.

Scripts are piped into ``mongosh`` on stdin rather than passed with ``--eval``,
because a ``createUser`` script carries a password and argv is world readable.
Every value interpolated into a script is rendered with :func:`json.dumps`, which
is a valid JavaScript literal for strings, numbers and objects alike, so a
database name can never become code.

Authorization. Before 3.1 MongoDB was installed without
``security.authorization``: the users and roles Noust created protected
nothing, and anything on the server could read every database. A new install
now creates an administrator (``noust_admin``, its password in Noust's secret
store) and turns authorization on before anything else connects. The shell
authenticates as it with ``db.auth()`` at the head of the script on stdin, and
the dump tools read the password from a 0600 ``--config`` file, so the
password is never in argv. An existing install is not changed: turning
authorization on breaks every application that connects without credentials,
so it gets a warning and the steps instead (:meth:`MongoDBManager.warnings`).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from noust.core.exceptions import (
    ConfigError,
    DatabaseBackupError,
    DatabaseEngineError,
    DatabaseError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    DatabaseUserError,
)
from noust.core.sealing import SealError
from noust.core.secrets import SecretStore
from noust.managers.database.base import (
    PROFILES,
    QUERY_TIMEOUT,
    TRANSFER_TIMEOUT,
    AccessEntry,
    BackupInfo,
    BaseDatabaseManager,
    DatabaseInfo,
    ListenAddress,
    RestoreOutcome,
    UserInfo,
    format_size,
    listen_address,
    restore_timeout,
)
from noust.managers.database.flavours import InstallPlan, distribution, plan_install
from noust.managers.database.instances import PASSWORD_SOURCES
from noust.managers.database.mongo_archive import ArchiveError, read_prelude
from noust.managers.database.registry import DatabaseRegistry

#: A database name mongorestore reads literally in ``--nsInclude``/``--nsTo``.
_LITERAL_NAMESPACE = re.compile(r"[A-Za-z0-9_-]+")

#: Roles MongoDB ships. A deployment may define its own, which are accepted as
#: long as the name is a plain identifier.
BUILT_IN_ROLES = frozenset(
    {
        "backup",
        "clusterAdmin",
        "clusterManager",
        "clusterMonitor",
        "dbAdmin",
        "dbAdminAnyDatabase",
        "dbOwner",
        "enableSharding",
        "hostManager",
        "read",
        "readAnyDatabase",
        "readWrite",
        "readWriteAnyDatabase",
        "restore",
        "root",
        "userAdmin",
        "userAdminAnyDatabase",
    }
)

#: Custom role names Noust is willing to pass on. ``str.isalnum`` would also
#: accept letters from any script, and a role name that is only distinguishable
#: from another by its Unicode block is not a role name anyone typed on purpose.
CUSTOM_ROLE_PATTERN = re.compile(r"\A[A-Za-z0-9_]+\Z")

#: The administrator a new install is given, and where its password is kept.
ADMIN_USER = "noust_admin"
ADMIN_SECRET = "databases/mongodb/admin"  # noqa: S105 - a secret name, not a secret

#: mongod's configuration file.
MONGOD_CONF = Path("/etc/mongod.conf")

#: The built-in role each access profile maps to, on one database.
PROFILE_ROLES: dict[str, str] = {"owner": "dbOwner", "read_write": "readWrite", "read_only": "read"}


class MongoDBManager(BaseDatabaseManager):
    """Manager for MongoDB deployments."""

    ENGINE_NAME = "mongodb"
    DISPLAY_NAME = "MongoDB"
    DEFAULT_PORT = 27017
    SERVICE_NAME = "mongod"
    PACKAGE_NAMES = ("mongodb-org",)
    CLIENT_BINARY = "mongod"
    VERSION_ARGV = ("mongod", "--version")
    VERSION_PATTERN = r"db version v(\d+\.\d+\.\d+)"
    PURGE_PATHS = ("/var/lib/mongodb", "/var/log/mongodb", "/etc/mongod.conf")
    BACKUP_SUFFIX = ".tar.gz"
    MAX_DATABASE_NAME_LENGTH = 63
    MAX_USER_NAME_LENGTH = 63
    CAPABILITIES = frozenset({"documents", "users", "profiles", "dump", "metrics"})
    INTERNAL_USERS = frozenset({ADMIN_USER})

    #: Databases that belong to the deployment, not to a user.
    SYSTEM_DATABASES = frozenset({"admin", "config", "local"})

    #: Shells to try, newest first.
    SHELLS = ("mongosh", "mongo")

    # ==================== Installation ====================

    def default_install_plan(self) -> InstallPlan:
        """
        Install the default series from MongoDB's repository.

        No distribution ships ``mongodb-org``, so even an install that names
        no version adds the upstream repository, for the series
        :data:`~noust.managers.database.flavours.MONGODB_DEFAULT_SERIES`.

        Returns:
            The plan.

        Raises:
            ValidationError: When MongoDB publishes no packages for this
                distribution; nothing has been downloaded.
        """
        return plan_install("mongodb", None, distribution())

    # ==================== Authorization ====================

    def _secrets(self) -> SecretStore:
        """
        The secret store the administrator's password is kept in.

        Returns:
            A store rooted beside the Noust store.
        """
        return SecretStore()

    def _admin_password(self) -> str | None:
        """
        Read the administrator's password, when Noust created one.

        Returns:
            The password, or None on an install Noust did not secure; always
            None for a container, whose password stays inside it.
        """
        if self.instance is not None:
            return None
        cached = getattr(self, "_admin", None)
        if cached is not None:
            return str(cached) or None
        try:
            password = self._secrets().read(ADMIN_SECRET)
        except (ConfigError, SealError) as exc:
            self.logger.warning(f"Could not read the MongoDB administrator's password: {exc}")
            password = None
        self._admin = password or ""
        return password

    def _auth_preamble(self) -> tuple[str, str | None]:
        """
        Build the line that authenticates a shell script, when there is one.

        ``void`` keeps the result of ``auth()`` out of the output, which the
        JSON helpers parse.

        Returns:
            The line (empty without credentials) and the password it carries.
            In a container the line reads the root account's password from
            the container's own environment (or the file a ``*_FILE``
            variable names), so it never leaves the container and the line
            carries none.
        """
        if self.instance is not None:
            user = self.instance.admin_user
            if not user:
                return "", None
            names = self._js(list(PASSWORD_SOURCES["mongo"]))
            return (
                "void (() => { const env = process.env; const read = (name) => env[name] || "
                "(env[name + '_FILE'] ? require('fs').readFileSync(env[name + '_FILE'], 'utf8')"
                f".trim() : ''); const password = {names}.map(read).find(Boolean); "
                f"if (password) db.getSiblingDB('admin').auth({self._js(user)}, password); }})();\n",
                None,
            )
        password = self._admin_password()
        if not password:
            return "", None
        return (
            f"void db.getSiblingDB('admin').auth({self._js(ADMIN_USER)}, {self._js(password)});\n",
            password,
        )

    @contextmanager
    def _tool_credentials(self) -> Iterator[list[str]]:
        """
        Provide the arguments that authenticate mongodump and mongorestore.

        The password goes in a 0600 ``--config`` file, which the database
        tools read for exactly this purpose; the user name is not a secret.

        Yields:
            Arguments for the tool, empty on an install without credentials
            and in a container, where the client script signs the tool in
            from the container's own environment.
        """
        password = self._admin_password()
        if not password:
            yield []
            return
        fd, path = tempfile.mkstemp(prefix="noust_mongo_", suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(yaml.safe_dump({"password": password}))
            yield [
                f"--config={path}",
                "--username",
                ADMIN_USER,
                "--authenticationDatabase",
                "admin",
            ]
        finally:
            self.fs.remove(Path(path))

    def _authorization_enabled(self) -> bool | None:
        """
        Read whether mongod enforces authorization, from its configuration.

        Returns:
            True or False, or None when the file cannot be read, or the
            server runs in a container (the host's file is not its).
        """
        if self.instance is not None:
            return None
        try:
            config = yaml.safe_load(MONGOD_CONF.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return None
        security = config.get("security") if isinstance(config, dict) else None
        return isinstance(security, dict) and security.get("authorization") == "enabled"

    def _post_install(self) -> None:
        """
        Turn authorization on for a new install, with an administrator.

        The administrator is created first, over the localhost exception
        (the only connection allowed while no user exists), its password kept
        in the secret store before the user exists, so a failure between the
        two leaves a password nobody uses rather than a user nobody can sign
        in as. Then ``security.authorization: enabled`` is written and mongod
        restarted.

        Raises:
            DatabaseEngineError: When the administrator cannot be created or
                the configuration cannot be written.
        """
        password = self.generate_password()
        self._secrets().write(ADMIN_SECRET, password)
        script = (
            f"db.getSiblingDB('admin').createUser({{user: {self._js(ADMIN_USER)}, "
            f"pwd: {self._js(password)}, roles: [{{role: 'root', db: 'admin'}}]}})"
        )
        # No preamble: the user it would authenticate as is being created.
        result = self._exec(
            [self._shell(), "admin", "--quiet"],
            input=script,
            timeout=QUERY_TIMEOUT,
            secrets=(password,),
        )
        if not result.success:
            raise DatabaseEngineError(
                "Could not create MongoDB's administrator",
                details=(result.stderr or result.stdout).strip(),
            )
        self._admin = password

        try:
            config = yaml.safe_load(MONGOD_CONF.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise DatabaseEngineError(
                f"Could not read {MONGOD_CONF} to turn authorization on",
                details=f"{exc}. Add 'security:\\n  authorization: enabled' by hand.",
            ) from exc
        if not isinstance(config, dict):
            config = {}
        security = config.get("security")
        config["security"] = {
            **(security if isinstance(security, dict) else {}),
            "authorization": "enabled",
        }
        self.fs.write_text(MONGOD_CONF, yaml.safe_dump(config, sort_keys=False), mode=0o644)
        self.restart()

    def warnings(self) -> list[str]:
        """
        Warn when mongod does not enforce authorization.

        Returns:
            The warning and what to do, or nothing.
        """
        if self._authorization_enabled() is False:
            return [
                "MongoDB runs without authorization: the users Noust creates protect "
                "nothing, and any process on this server can read and change every "
                "database. To turn it on: create an administrator (db.createUser with the "
                "root role in admin), give every application a user of its own, set "
                "'security.authorization: enabled' in /etc/mongod.conf and restart mongod. "
                "Applications that connect without credentials stop working until they "
                "have one."
            ]
        return []

    def access_hint(self) -> str:
        """
        Say how Noust signs in, since there is no password to store.

        Returns:
            Which account Noust uses and where its password is.
        """
        if self.instance is not None:
            return super().access_hint()
        return (
            f"Noust signs in as {ADMIN_USER}, the administrator it created when it installed "
            f"MongoDB; its password is the Noust secret {ADMIN_SECRET}. If that account was "
            "removed or its password changed, recreate it in admin with the root role and "
            "the password that secret holds."
        )

    def listen_addresses(self) -> ListenAddress | None:
        """
        Ask mongod for ``net.bindIp``.

        Returns:
            The addresses; ``127.0.0.1`` when unset, mongod's own default.
        """
        success, data = self._execute_mongo_json("db.adminCommand({getCmdLineOpts: 1}).parsed")
        if not success or not isinstance(data, dict):
            return None
        found = data.get("net")
        net: dict[str, Any] = found if isinstance(found, dict) else {}
        bind = str(net.get("bindIp") or "127.0.0.1")
        if net.get("bindIpAll"):
            bind = "0.0.0.0"  # noqa: S104 - reporting the setting, not binding
        return listen_address("net.bindIp", bind, separator=",")

    def server_port(self) -> int:
        """
        Return the port mongod says it listens on.

        Returns:
            ``net.port``, or :attr:`DEFAULT_PORT`; for a container, the port
            it is reached on from the host.
        """
        if self.instance is not None:
            return self.instance.port
        success, data = self._execute_mongo_json("db.adminCommand({getCmdLineOpts: 1}).parsed")
        net = data.get("net") if success and isinstance(data, dict) else None
        port = net.get("port") if isinstance(net, dict) else None
        return int(port) if isinstance(port, int) and 0 < port < 65536 else self.DEFAULT_PORT

    # ==================== Shell ====================

    @classmethod
    def validate_privileges(cls, privileges: Sequence[str] | None) -> tuple[str, ...]:
        """
        Check role names before they reach ``grantRolesToUser``.

        Args:
            privileges: Roles requested by the caller, or None for the default.

        Returns:
            The roles, without repeats.

        Raises:
            DatabaseUserError: When a role is not built in and does not look like
                a custom role name.
        """
        requested = list(privileges) if privileges else ["readWrite"]

        roles: list[str] = []
        for role in requested:
            if not isinstance(role, str) or (
                role not in BUILT_IN_ROLES and not CUSTOM_ROLE_PATTERN.match(role)
            ):
                raise DatabaseUserError(
                    f"Invalid MongoDB role: {role!r}",
                    details=(
                        f"Use a built-in role ({', '.join(sorted(BUILT_IN_ROLES))}) or the name "
                        "of a custom role, made of letters, digits and underscores."
                    ),
                )
            if role not in roles:
                roles.append(role)
        return tuple(roles)

    def _shell(self) -> str:
        """
        Pick the shell binary this host provides.

        Returns:
            The name of the shell to run.

        Raises:
            DatabaseEngineError: When no MongoDB shell is installed.
        """
        if self.instance is not None:
            # The client script tries mongosh, then the legacy shell.
            return self.SHELLS[0]
        for shell in self.SHELLS:
            if self.runner.exists(shell):
                return shell
        raise DatabaseEngineError(
            "No MongoDB shell found",
            details="Install mongosh (or the legacy mongo client) and retry.",
        )

    def _execute_mongo(
        self,
        script: str,
        database: str = "admin",
        *,
        secrets: Sequence[str] = (),
        timeout: int = QUERY_TIMEOUT,
    ) -> tuple[bool, str]:
        """
        Run a JavaScript snippet, passing it on stdin.

        Args:
            script: The snippet.
            database: Database the shell connects to.
            secrets: Values the snippet carries that must not be logged.
            timeout: Deadline in seconds.

        Returns:
            Whether the shell succeeded, and its output or its error text.
        """
        preamble, password = self._auth_preamble()
        result = self._exec(
            [self._shell(), database, "--quiet"],
            input=preamble + script,
            timeout=timeout,
            secrets=(*secrets, *((password,) if password else ())),
        )
        return result.success, result.stdout if result.success else result.stderr

    def _execute_mongo_json(
        self,
        expression: str,
        database: str = "admin",
    ) -> tuple[bool, Any]:
        """
        Run an expression and parse its extended JSON result.

        Args:
            expression: The JavaScript expression.
            database: Database the shell connects to.

        Returns:
            Whether the shell succeeded, and the parsed value or the raw output.
        """
        success, output = self._execute_mongo(f"EJSON.stringify({expression})", database)
        if success and output.strip():
            try:
                return True, json.loads(output.strip())
            except json.JSONDecodeError:
                return success, output
        return success, output

    @staticmethod
    def _js(value: Any) -> str:
        """
        Render a Python value as a JavaScript literal.

        Args:
            value: The value to embed in a script.

        Returns:
            A literal that the shell parses as data, never as code.
        """
        return json.dumps(value)

    # ==================== Database Management ====================

    def create_database(
        self,
        name: str,
        owner: str | None = None,
        encoding: str | None = None,
        **kwargs,
    ) -> DatabaseInfo:
        """
        Create a database by creating, then dropping, a placeholder collection.

        Args:
            name: Database name.
            owner: Ignored; MongoDB users are granted roles instead.
            encoding: Ignored; MongoDB stores BSON.
            **kwargs: Unused.

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

        success, output = self._execute_mongo(
            f"db.getSiblingDB({self._js(name)}).createCollection('_wasm_init')"
        )
        if not success:
            raise DatabaseError(f"Failed to create database '{name}'", details=output.strip())

        self._execute_mongo(f"db.getSiblingDB({self._js(name)})._wasm_init.drop()")

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
                details="Run 'noust db list --engine mongodb' to see the databases.",
            )

        success, output = self._execute_mongo(f"db.getSiblingDB({self._js(name)}).dropDatabase()")
        if not success:
            raise DatabaseError(f"Failed to drop database '{name}'", details=output.strip())

        self.logger.info(f"Dropped database: {name}")

    def database_exists(self, name: str) -> bool:
        """
        Report whether a database exists.

        Args:
            name: Database name.

        Returns:
            True when the deployment lists the name.

        Raises:
            DatabaseAccessError: When the server does not let Noust in.
            DatabaseQueryError: When it cannot be asked: "no" would let a
                drop skip its last dump and a forget delete a live row.
        """
        success, data = self._execute_mongo_json(
            "db.adminCommand('listDatabases').databases.map(d => d.name)"
        )
        if not success:
            self._listing_failed("databases", str(data))
        if isinstance(data, list):
            return name in data
        return f'"{name}"' in str(data)

    def list_databases(self) -> list[DatabaseInfo]:
        """
        List the databases that do not belong to the deployment itself.

        ``owner`` stays unset: MongoDB grants roles scoped to a database to
        any number of users (see :meth:`list_users`), it does not record a
        single owning account on the database itself the way a filesystem or
        a single-owner SQL catalog would.

        Returns:
            One entry per user database.
        """
        success, data = self._execute_mongo_json("db.adminCommand('listDatabases')")
        if not success or not isinstance(data, dict):
            self._listing_failed("databases", str(data))

        databases = []
        for entry in data.get("databases", []):
            name = entry.get("name", "")
            if name in self.SYSTEM_DATABASES:
                continue
            size = entry.get("sizeOnDisk", 0)
            databases.append(
                DatabaseInfo(
                    name=name,
                    engine=self.ENGINE_NAME,
                    size=format_size(size),
                    extra={"sizeOnDisk": size, "empty": entry.get("empty", False)},
                )
            )
        return databases

    def get_database_info(self, name: str) -> DatabaseInfo:
        """
        Describe one database.

        Args:
            name: Database name.

        Returns:
            Size and collection count.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
        """
        if not self.database_exists(name):
            raise DatabaseNotFoundError(f"Database '{name}' does not exist")

        success, data = self._execute_mongo_json(f"db.getSiblingDB({self._js(name)}).stats()")

        size = None
        collections = 0
        if success and isinstance(data, dict):
            size = format_size(data.get("dataSize", 0))
            collections = data.get("collections", 0)

        return DatabaseInfo(
            name=name,
            engine=self.ENGINE_NAME,
            size=size,
            tables=collections,
            extra=data if isinstance(data, dict) else {},
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
        Create a user with roles on a database.

        Args:
            username: User name.
            password: Password. Generated when omitted.
            host: Recorded for the caller; MongoDB has no per-host users.
            **kwargs: Accepts ``database`` and ``roles``.

        Returns:
            The user and its password.

        Raises:
            DatabaseUserError: When the user exists or creation fails.
        """
        self.validate_user_name(username)
        if self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' already exists",
                details="Drop the user first, or pick another name.",
            )

        password = password or self.generate_password()
        database = kwargs.get("database", "admin")
        self.validate_database_name(database)

        roles: list[dict[str, str]] = []
        for entry in kwargs.get("roles") or self.validate_privileges(None):
            name = entry.get("role", "") if isinstance(entry, dict) else entry
            target = entry.get("db", database) if isinstance(entry, dict) else database
            self.validate_database_name(target)
            roles.append({"role": self.validate_privileges([name])[0], "db": target})

        script = (
            f"db.getSiblingDB({self._js(database)}).createUser({{"
            f"user: {self._js(username)}, pwd: {self._js(password)}, roles: {self._js(roles)}}})"
        )
        success, output = self._execute_mongo(script, secrets=(password,))
        if not success:
            raise DatabaseUserError(f"Failed to create user '{username}'", details=output.strip())

        self.logger.info(f"Created user: {username}")
        user = UserInfo(
            username=username,
            engine=self.ENGINE_NAME,
            host=host,
            databases=[database],
            privileges=[role["role"] for role in roles],
        )
        return user, password

    def _user_database(self, username: str) -> str | None:
        """
        Find the database a user is defined in.

        A user belongs to the database it was created in (its authentication
        database), and every command about it has to be run there. The
        previous version only ever looked in ``admin``, so a user created for
        an application's own database was reported missing.

        Args:
            username: The user.

        Returns:
            The database, or None when no database defines the user.
        """
        success, data = self._execute_mongo_json(
            "db.getSiblingDB('admin').system.users"
            f".find({{user: {self._js(username)}}}, {{db: 1}}).toArray().map(u => u.db)"
        )
        if success and isinstance(data, list) and data:
            return str(data[0])
        return None

    def drop_user(self, username: str, host: str = "localhost") -> None:
        """
        Drop a user from the database that defines it.

        Args:
            username: User name.
            host: Ignored; MongoDB has no per-host users.

        Raises:
            DatabaseUserError: When the user is missing or the drop fails.
        """
        self.validate_user_name(username)
        home = self._user_database(username)
        if home is None:
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Run 'noust db user-list --engine mongodb' to see the users.",
            )

        success, output = self._execute_mongo(
            f"db.getSiblingDB({self._js(home)}).dropUser({self._js(username)})"
        )
        if not success:
            raise DatabaseUserError(f"Failed to drop user '{username}'", details=output.strip())

        self.logger.info(f"Dropped user: {username}")

    def user_exists(self, username: str, host: str = "localhost") -> bool:
        """
        Report whether any database defines a user.

        Args:
            username: User name.
            host: Ignored; MongoDB has no per-host users.

        Returns:
            True when a database defines it.
        """
        return self._user_database(username) is not None

    def list_users(self) -> list[UserInfo]:
        """
        List every user of every database.

        Returns:
            One entry per user, with its roles and the databases they cover.
        """
        success, data = self._execute_mongo_json(
            "db.getSiblingDB('admin').system.users.find({}, {user: 1, db: 1, roles: 1}).toArray()"
        )
        if not success or not isinstance(data, list):
            self._listing_failed("users", str(data))

        users = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            roles = [role for role in entry.get("roles", []) if isinstance(role, dict)]
            users.append(
                UserInfo(
                    username=entry.get("user", ""),
                    engine=self.ENGINE_NAME,
                    databases=sorted({role.get("db", "") for role in roles if role.get("db")}),
                    privileges=sorted({role.get("role", "") for role in roles}),
                    extra={
                        "auth_database": entry.get("db"),
                        "roles": [{"role": r.get("role"), "db": r.get("db")} for r in roles],
                    },
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
        Grant roles on a database to a user.

        Args:
            username: User name.
            database: Database the roles apply to.
            privileges: Role names. readWrite when omitted.
            host: Ignored; MongoDB has no per-host users.

        Raises:
            DatabaseUserError: When a role is invalid or the grant fails.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        roles = [{"role": role, "db": database} for role in self.validate_privileges(privileges)]
        home = self._user_database(username) or "admin"

        success, output = self._execute_mongo(
            f"db.getSiblingDB({self._js(home)})"
            f".grantRolesToUser({self._js(username)}, {self._js(roles)})"
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to grant roles on '{database}' to '{username}'", details=output.strip()
            )

        self.logger.info(f"Granted {[role['role'] for role in roles]} on {database} to {username}")

    def revoke_privileges(
        self,
        username: str,
        database: str,
        privileges: Sequence[str] | None = None,
        host: str = "localhost",
    ) -> None:
        """
        Revoke roles on a database from a user.

        Args:
            username: User name.
            database: Database the roles apply to.
            privileges: Role names. readWrite when omitted.
            host: Ignored; MongoDB has no per-host users.

        Raises:
            DatabaseUserError: When a role is invalid or the revoke fails.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        roles = [{"role": role, "db": database} for role in self.validate_privileges(privileges)]
        home = self._user_database(username) or "admin"

        success, output = self._execute_mongo(
            f"db.getSiblingDB({self._js(home)})"
            f".revokeRolesFromUser({self._js(username)}, {self._js(roles)})"
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to revoke roles on '{database}' from '{username}'",
                details=output.strip(),
            )

        self.logger.info(
            f"Revoked {[role['role'] for role in roles]} on {database} from {username}"
        )

    # ==================== Backup & Restore ====================

    def backup(
        self,
        database: str,
        output_path: Path | None = None,
        compress: bool = True,
        **kwargs,
    ) -> BackupInfo:
        """
        Dump a database with mongodump and pack the result into a tarball.

        mongodump writes a directory tree, so the archive, not the dump itself, is
        what lands in the backup directory.

        Args:
            database: Database name.
            output_path: Custom destination for the tarball.
            compress: Compress the dumped BSON files.
            **kwargs: Unused.

        Returns:
            Information about the backup.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseBackupError: When the dump or the archiving fails.
        """
        self.validate_database_name(database)
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")

        if self.instance is not None:
            # mongodump writes its tree inside the container, where Noust
            # cannot tar it: one archive on stdout reaches the host instead.
            return self._dump_to_file(
                ["mongodump", "--archive", "--db", database],
                self._backup_path(database, output_path, compress, suffix=".archive"),
                database=database,
                compress=compress,
            )

        archive = self._backup_path(database, output_path, False)

        with (
            tempfile.TemporaryDirectory(prefix="wasm-mongodump-") as workdir,
            self._tool_credentials() as credentials,
        ):
            argv = ["mongodump", *credentials, "--db", database, "--out", workdir]
            if compress:
                argv.append("--gzip")

            result = self._exec(argv, timeout=TRANSFER_TIMEOUT)
            if not result.success:
                raise DatabaseBackupError(
                    f"Failed to back up '{database}'",
                    details=result.stderr.strip() or "mongodump reported no error text.",
                )

            # "tar -czf <archive>" creates the archive with the process umask,
            # which for root is 0644: the whole dump would be readable by every
            # local account. Writing the archive to stdout hands the file to the
            # runner, which creates it 0600 and removes it if tar fails.
            info = self._dump_to_file(
                ["tar", "-czf", "-", "-C", workdir, database],
                archive,
                database=database,
                compress=False,
                timeout=TRANSFER_TIMEOUT,
            )

        # The tarball is gzipped by tar itself, not by the runner's gzip stage.
        info.compressed = True
        return info

    def _create_for_restore(self, database: str, owner: str | None) -> None:
        """
        Nothing to create: MongoDB creates a database on its first write.

        Args:
            database: The database.
            owner: Ignored; MongoDB grants roles instead.
        """

    def _load_backup(self, database: str, backup_path: Path, **kwargs: Any) -> None:
        """
        Load a mongodump tarball or directory with mongorestore.

        Args:
            database: The database to load into.
            backup_path: Tarball or dump directory.
            **kwargs: Unused.

        Raises:
            DatabaseBackupError: When the archive cannot be extracted or
                mongorestore fails.
        """
        if self.instance is not None:
            self._load_archive(database, backup_path)
            return
        with tempfile.TemporaryDirectory(prefix="wasm-mongorestore-") as workdir:
            if backup_path.is_dir():
                dump_dir = backup_path
            else:
                result = self._exec(
                    ["tar", "-xzf", str(backup_path), "-C", workdir],
                    timeout=TRANSFER_TIMEOUT,
                )
                if not result.success:
                    raise DatabaseBackupError(
                        "Failed to extract the backup",
                        details=result.stderr.strip() or f"{backup_path} is not a gzipped tar.",
                    )
                extracted = sorted(Path(workdir).iterdir())
                dump_dir = extracted[0] if extracted else Path(workdir)

            source = dump_dir / database if (dump_dir / database).is_dir() else dump_dir

            with self._tool_credentials() as credentials:
                argv = ["mongorestore", *credentials, "--db", database]
                if any(source.rglob("*.gz")):
                    argv.append("--gzip")
                argv.append(str(source))
                result = self._exec(argv, timeout=TRANSFER_TIMEOUT)
            if not result.success:
                raise DatabaseBackupError(
                    f"Failed to restore database '{database}'",
                    details=result.stderr.strip() or "mongorestore reported no error text.",
                )

    def restore(
        self,
        database: str,
        backup_path: Path,
        drop_existing: bool = False,
        *,
        safety_backup: bool = True,
        on_safety_copy: Callable[[Path], None] | None = None,
        **kwargs: Any,
    ) -> RestoreOutcome:
        """
        Restore a database, refusing a container archive it cannot aim first.

        A container's archive is loaded into the namespaces it names unless
        mongorestore is told otherwise, so which database it holds is read
        before the safety copy is taken or anything is loaded: an archive
        that cannot be aimed at ``database`` costs nothing.

        Args:
            database: Target database name.
            backup_path: Path to the backup file.
            drop_existing: Drop and recreate the database before loading.
            safety_backup: Take the safety copy when nothing is dropped.
            on_safety_copy: Called with the safety copy as soon as it exists.
            **kwargs: Engine-specific options (``isolated``).

        Returns:
            What was done, the safety copy included.

        Raises:
            DatabaseBackupError: When the archive cannot be read or aimed, or
                the restore fails.
        """
        if self.instance is not None and Path(backup_path).exists():
            self._archive_source(self.validate_database_name(database), Path(backup_path))
        return super().restore(
            database,
            backup_path,
            drop_existing,
            safety_backup=safety_backup,
            on_safety_copy=on_safety_copy,
            **kwargs,
        )

    def _archive_source(self, database: str, backup_path: Path) -> str:
        """
        Name the database a container archive is loaded from.

        Args:
            database: The database it is loaded into.
            backup_path: The archive, plain or gzipped.

        Returns:
            ``database`` when the archive holds it (or holds nothing), else the
            one database the archive holds.

        Raises:
            DatabaseBackupError: When the file is not a mongodump archive, or
                it holds several databases and none of them is ``database``.
        """
        try:
            prelude = read_prelude(backup_path)
        except (ArchiveError, OSError) as exc:
            raise DatabaseBackupError(
                f"{backup_path.name} cannot be restored into this container's MongoDB",
                details=(
                    f"{exc}\nA container's MongoDB restores the archives Noust dumps from a "
                    "container (.archive or .archive.gz). A dump taken on the host (a "
                    ".tar.gz tree) is restored into the host's MongoDB."
                ),
            ) from exc
        held = prelude.databases
        if not held or database in held:
            source = database
        elif len(held) == 1:
            source = held[0]
        else:
            raise DatabaseBackupError(
                f"{backup_path.name} holds several databases and none is '{database}'",
                details=(
                    f"It holds: {', '.join(held)}. Restore it into one of those names, or "
                    "dump the one database you want on its own and restore that."
                ),
            )
        # mongorestore reads '*' and '$name$' in a namespace as patterns, and
        # the archive is a file anyone with a dump could have written: a name
        # that is not literal would widen what the restore reaches.
        for name in (source, database):
            if not _LITERAL_NAMESPACE.fullmatch(name):
                raise DatabaseBackupError(
                    f"'{name}' cannot be named exactly in a mongorestore namespace",
                    details="Only letters, digits, '_' and '-' are restored into a container.",
                )
        return source

    def _load_archive(self, database: str, backup_path: Path) -> None:
        """
        Load a container's ``mongodump --archive`` through mongorestore's stdin.

        The archive keeps the namespaces it was dumped from, and mongorestore
        writes them back there unless told otherwise. Only the database it is
        loaded from is included, and it is renamed to ``database`` when they
        differ, so a restore as a new database, or a restore test, never
        reaches the original. Nothing is passed ``--drop``: the base restore
        drops and recreates the database itself when asked to replace it
        (after the safety copy), exactly as a host restore does.

        Args:
            database: The database it is loaded into.
            backup_path: The archive, plain or gzipped.

        Raises:
            DatabaseBackupError: When the archive cannot be aimed or
                mongorestore fails.
        """
        source = self._archive_source(database, backup_path)
        argv = ["mongorestore", "--archive", "--nsInclude", f"{source}.*"]
        if source != database:
            argv += ["--nsFrom", f"{source}.*", "--nsTo", f"{database}.*"]
        with self._staged_backup(backup_path, f"mongodb-restore-{database}.archive") as staged:
            result = self._exec(argv, stdin_path=staged, timeout=restore_timeout(staged))
        if not result.success:
            raise DatabaseBackupError(
                f"Failed to restore database '{database}'",
                details=result.stderr.strip() or "mongorestore reported no error text.",
            )

    # ==================== Query Execution ====================

    def execute_query(
        self,
        database: str,
        query: str,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Run a JavaScript snippet against a database.

        Args:
            database: Database name.
            query: The snippet.
            **kwargs: Unused.

        Returns:
            Success and the snippet's output.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseQueryError: When the snippet fails.
        """
        if not self.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")

        success, output = self._execute_mongo(query, database=database)
        if not success:
            raise DatabaseQueryError("Query failed", details=output.strip())
        return success, output

    # ==================== Passwords and profiles ====================

    def set_user_password(self, username: str, password: str, host: str = "localhost") -> None:
        """
        Give a user a new password, with the script on stdin.

        Args:
            username: The user.
            password: Its new password.
            host: Ignored; MongoDB has no per-host users.

        Raises:
            DatabaseUserError: When the user does not exist or the change fails.
        """
        self.validate_user_name(username)
        home = self._user_database(username)
        if home is None:
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Run 'noust db user-list --engine mongodb' to see the users.",
            )
        success, output = self._execute_mongo(
            f"db.getSiblingDB({self._js(home)})"
            f".changeUserPassword({self._js(username)}, {self._js(password)})",
            secrets=(password,),
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
        Give a user one built-in role on a database, and only that one.

        ``owner`` is ``dbOwner``, ``read_write`` ``readWrite``, ``read_only``
        ``read``; the other two are revoked in the same script.

        Args:
            username: The user.
            database: The database.
            profile: ``owner``, ``read_write`` or ``read_only``.
            host: Ignored; MongoDB has no per-host users.

        Raises:
            DatabaseUserError: When the profile is unknown, the user does not
                exist, or the change fails.
        """
        self.validate_user_name(username)
        self.validate_database_name(database)
        if profile not in PROFILES:
            raise DatabaseUserError(
                f"Unknown access profile: {profile!r}",
                details=f"Use one of: {', '.join(PROFILES)}.",
            )
        home = self._user_database(username)
        if home is None:
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Create it first with 'noust db user-create --engine mongodb'.",
            )
        others = [
            {"role": role, "db": database}
            for name, role in PROFILE_ROLES.items()
            if name != profile
        ]
        wanted = [{"role": PROFILE_ROLES[profile], "db": database}]
        target = f"db.getSiblingDB({self._js(home)})"
        success, output = self._execute_mongo(
            f"{target}.revokeRolesFromUser({self._js(username)}, {self._js(others)});\n"
            f"{target}.grantRolesToUser({self._js(username)}, {self._js(wanted)})"
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to give '{username}' the {profile} profile on '{database}'",
                details=output.strip(),
            )

    def list_access(self, database: str) -> list[AccessEntry]:
        """
        List the users holding a role on a database, with their profile.

        Args:
            database: The database.

        Returns:
            One entry per user.
        """
        by_role = {role: profile for profile, role in PROFILE_ROLES.items()}
        entries: list[AccessEntry] = []
        for user in self.list_users():
            roles = [
                str(role.get("role"))
                for role in user.extra.get("roles", [])
                if isinstance(role, dict) and role.get("db") == database
            ]
            if not roles:
                continue
            profiles = [by_role[role] for role in roles if role in by_role]
            profile = next((candidate for candidate in PROFILES if candidate in profiles), "custom")
            if len(roles) > len(profiles):
                profile = "custom"
            entries.append(
                AccessEntry(
                    username=user.username,
                    profile=profile,
                    privileges=tuple(sorted(roles)),
                    internal=self.is_internal_user(user.username),
                )
            )
        return entries

    def get_interactive_command(
        self,
        database: str | None = None,
        username: str | None = None,
    ) -> list[str]:
        """
        Build the command that opens a shell session.

        Args:
            database: Database to connect to.
            username: User to connect as.

        Returns:
            The argument vector.

        Raises:
            DatabaseEngineError: When no MongoDB shell is installed.
        """
        argv = [self._shell()]
        if database:
            argv.append(database)
        if username:
            argv.extend(["--username", username])
        return self._interactive(argv)


DatabaseRegistry.register(MongoDBManager, aliases=["mongo", "mongod"])
