# Copyright (c) 2024-2025 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Redis and Valkey manager: one family, one manager.

Valkey is the fork Fedora ships instead of Redis and Debian 13 ships beside
it; it speaks the same protocol, keeps the same files and answers the same
commands. What differs is the unit (``redis-server`` on Debian, ``redis`` on
Fedora, ``valkey-server`` or ``valkey``) and the programs (``valkey-cli``,
``valkey-server``), so both are detected rather than assumed.

Redis is a key-value store: its "databases" are numbered slots and its users are
ACL entries, so several operations that make sense elsewhere are refused here
with an explanation instead of being emulated.

Passwords never reach argv. ``ACL SETUSER`` receives the SHA-256 form Redis
documents for exactly this reason, ``requirepass`` is set over stdin, and the
client authenticates through ``REDISCLI_AUTH``. The password the client
authenticates with is read from ``databases.credentials.redis.password`` or,
once Noust has set one, from its secret store: before 3.1 it was only known
to the process that set it, so every operation on a server with
``requirepass`` failed with ``NOAUTH``.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import time
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import (
    ConfigError,
    DatabaseBackupError,
    DatabaseError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    DatabaseUserError,
)
from noust.core.sealing import SealError
from noust.core.secrets import SecretStore
from noust.deployers.helpers.permissions import hand_over_file
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
    listen_address,
)
from noust.managers.database.registry import DatabaseRegistry

if TYPE_CHECKING:
    from noust.managers.database.instances import DatabaseInstance

#: How redis-cli prints an error reply when its output is not a terminal: it
#: still exits 0, so the text is the only sign a command was refused.
_ERROR_REPLIES = ("(error)", "ERR ", "NOAUTH", "WRONGPASS", "NOPERM")

#: Where Noust keeps the password it set with ``requirepass``.
REQUIREPASS_SECRET = "databases/redis/requirepass"  # noqa: S105 - a secret name, not a secret

#: The ACL rules each access profile gives. ACLs are instance-wide: a Redis
#: "database" is a slot any client may SELECT, so the profile is the user's
#: on every slot.
PROFILE_RULES: dict[str, tuple[str, ...]] = {
    "owner": ("allkeys", "allchannels", "+@all"),
    "read_write": ("allkeys", "allchannels", "+@all", "-@dangerous"),
    "read_only": ("allkeys", "resetchannels", "-@all", "+@read", "+@connection", "-@dangerous"),
}

#: ACL rules that are bare keywords.
ACL_KEYWORDS = frozenset(
    {
        "allchannels",
        "allcommands",
        "allkeys",
        "clearselectors",
        "nocommands",
        "nokeys",
        "off",
        "on",
        "resetchannels",
        "resetkeys",
        "sanitize-payload",
        "skip-sanitize-payload",
    }
)

# Both rules end in \Z rather than '$': in Python '$' also matches before a
# final newline, so a '$'-anchored rule would accept "+@all\n" and hand a value
# with a line break to the server as if it were a bare keyword.

#: Command and category rules: ``+get``, ``-@admin``, ``+client|list``.
ACL_COMMAND_PATTERN = re.compile(r"\A[+-]@?[A-Za-z0-9_|-]+\Z")

#: Key and channel patterns: ``~*``, ``~cache:*``, ``&events.*``.
ACL_PATTERN_RULE = re.compile(r"\A(?:%(?:R|W|RW))?[~&][A-Za-z0-9_.:*?{}\[\]-]*\Z")

#: Default number of one-second polls spent waiting for persistence to finish.
PERSISTENCE_POLL_SECONDS = 60


class RedisSignInError(DatabaseQueryError):
    """An ACL user :meth:`RedisManager.run_commands` was asked to sign in as was refused."""


class RedisManager(BaseDatabaseManager):
    """Manager for a Redis or Valkey instance."""

    ENGINE_NAME = "redis"
    DISPLAY_NAME = "Redis"
    DEFAULT_PORT = 6379
    SERVICE_NAME = "redis-server"
    SERVICE_CANDIDATES = ("redis-server", "redis", "valkey-server", "valkey")
    PACKAGE_NAMES = ("redis-server",)
    #: Debian 13 and Ubuntu 24.04 (universe) ship Valkey under this name.
    VALKEY_PACKAGES = ("valkey-server",)
    CLIENT_BINARY = "redis-cli"
    #: The client Valkey ships; ``redis-cli`` is only there with its compat package.
    VALKEY_CLIENT = "valkey-cli"
    VERSION_ARGV = ("redis-server", "--version")
    VERSION_PATTERN = r"v=(\d+\.\d+\.\d+)"
    PURGE_PATHS = ("/var/lib/redis", "/etc/redis")
    BACKUP_SUFFIX = ".rdb"
    CAPABILITIES = frozenset({"keys", "users", "profiles", "dump", "metrics"})

    #: Where the server keeps its RDB and AOF files.
    DATA_DIR = Path("/var/lib/redis")
    #: Account the server runs as, and therefore the owner of its data files.
    DATA_OWNER = "redis"
    #: Number of database slots a default configuration exposes.
    DEFAULT_DATABASE_COUNT = 16
    #: One-second polls spent waiting for a save or an AOF rewrite to finish.
    PERSISTENCE_POLLS = PERSISTENCE_POLL_SECONDS

    def __init__(self, verbose: bool = False):
        """
        Args:
            verbose: Enable verbose logging.
        """
        super().__init__(verbose=verbose)
        self._password: str | None = None
        self._password_loaded = False

    # ==================== Family ====================

    def _bind_names(self, instance: DatabaseInstance) -> None:
        """
        Name the engine after the container's image.

        Args:
            instance: The container.
        """
        valkey = instance.flavour == "valkey"
        self.DISPLAY_NAME = "Valkey" if valkey else "Redis"
        self.EOL_FAMILY = "valkey" if valkey else ""

    def _is_valkey(self) -> bool:
        """
        Tell whether the instance is Valkey rather than Redis.

        Returns:
            True when Valkey's server is installed and Redis's is not; for a
            container, when its image is Valkey's.
        """
        if self.instance is not None:
            return self.instance.flavour == "valkey"
        return self.runner.exists("valkey-server") and not self.runner.exists("redis-server")

    def _cli(self) -> str:
        """
        Name the client program to run.

        Returns:
            ``redis-cli``, or ``valkey-cli`` where only Valkey's client exists.
            Inside a container the client script tries both.
        """
        if self.instance is not None:
            return self.CLIENT_BINARY
        if self.runner.exists(self.CLIENT_BINARY):
            return self.CLIENT_BINARY
        if self.runner.exists(self.VALKEY_CLIENT):
            return self.VALKEY_CLIENT
        return self.CLIENT_BINARY

    def is_installed(self) -> bool:
        """
        Report whether a Redis or Valkey client is installed.

        Returns:
            True when either client is on PATH; always for a container, whose
            image is the installation.
        """
        if self.instance is not None:
            return True
        return self.runner.exists(self.CLIENT_BINARY) or self.runner.exists(self.VALKEY_CLIENT)

    def get_version(self) -> str | None:
        """
        Read the server's version, from Redis's or Valkey's own binary.

        Returns:
            The version, or None.
        """
        if self._is_valkey():
            self.DISPLAY_NAME = "Valkey"
            self.EOL_FAMILY = "valkey"
            result = self._exec(["valkey-server", "--version"])
            match = re.search(self.VERSION_PATTERN, result.stdout) if result.success else None
            return match.group(1) if match else None
        return super().get_version()

    def _package_sets(self) -> tuple[list[str], ...]:
        """
        Prefer Redis, and fall back to Valkey where only Valkey is packaged.

        Returns:
            The Redis packages first, the Valkey ones as a fallback.
        """
        return (list(self.PACKAGE_NAMES), list(self.VALKEY_PACKAGES))

    def _on_packages_installed(self, packages: Sequence[str]) -> None:
        """
        Name the unit and the family after the flavour that installed.

        Args:
            packages: The package names that installed successfully.
        """
        if list(packages) == list(self.VALKEY_PACKAGES):
            self.SERVICE_NAME = "valkey-server"
            self.DISPLAY_NAME = "Valkey"
            self.EOL_FAMILY = "valkey"
            self._unit_detected = True

    def installed_flavour(self) -> str | None:
        """
        Name the flavour installed: ``valkey`` or ``redis``.

        Returns:
            The flavour, or None when neither is installed.
        """
        if not self.is_installed():
            return None
        return "valkey" if self._is_valkey() else "redis"

    # ==================== Client ====================

    def _secrets(self) -> SecretStore:
        """
        The secret store the ``requirepass`` password is kept in.

        Returns:
            A store rooted beside the Noust store.
        """
        return SecretStore()

    def _known_password(self) -> str | None:
        """
        Find the password the client must authenticate with, once.

        ``databases.credentials.redis.password`` wins, as the operator's
        word; then the password Noust set itself. An unreadable or sealed
        secret is reported and treated as absent: the command then fails with
        Redis's own ``NOAUTH``, which says what is wrong.

        Returns:
            The password, or None when there is none. For a container, the
            ``--requirepass`` its command line carries: a password its
            environment holds is read inside it by the client script, and the
            host's stored password is the host's server's.
        """
        if self._password_loaded:
            return self._password
        self._password_loaded = True
        if self.instance is not None:
            self._password = self.instance.command_password
            return self._password
        settings = self.config.get("databases", {}).get("credentials", {}).get("redis", {})
        configured = settings.get("password") if isinstance(settings, dict) else None
        if configured:
            self._password = str(configured)
            return self._password
        try:
            self._password = self._secrets().read(REQUIREPASS_SECRET)
        except (ConfigError, SealError) as exc:
            self.logger.warning(f"Could not read the Redis password Noust stored: {exc}")
        return self._password

    def client_password(self) -> str | None:
        """
        Name the password clients authenticate with, as an application's URL needs it.

        Returns:
            The configured or stored ``requirepass``, or None when there is none.
        """
        return self._known_password()

    def requirepass_set(self) -> bool | None:
        """
        Ask the instance whether clients must authenticate.

        Returns:
            False when ``requirepass`` is empty (every client connects without
            a password), True when one is set, None when the instance could
            not be asked (a password Noust does not know answers ``NOAUTH``).
        """
        success, output = self._execute_redis("CONFIG", "GET", "requirepass")
        if not success or "NOAUTH" in output:
            return None
        lines = [line.strip() for line in output.splitlines()]
        if not lines or lines[0] != "requirepass":
            return None
        return len(lines) > 1 and lines[1] != ""

    def _client_env(self) -> Mapping[str, str] | None:
        """
        Build the environment that authenticates the client.

        Returns:
            REDISCLI_AUTH carrying the password, or None when there is none.
            The password never goes in argv, where ``ps`` would show it.
        """
        password = self._known_password()
        return {"REDISCLI_AUTH": password} if password else None

    def _execute_redis(self, *args: str, db: int = 0) -> tuple[bool, str]:
        """
        Run a Redis command.

        Args:
            *args: Command and arguments, already free of secrets.
            db: Database slot to select.

        Returns:
            Whether the command succeeded, and its output or its error text.
        """
        env = self._client_env()
        result = self._exec(
            [self._cli(), "-n", str(db), *args],
            env=env,
            timeout=QUERY_TIMEOUT,
            secrets=tuple(env.values()) if env else (),
        )
        output = result.stdout if result.success else result.stderr
        if result.success and result.stdout.lstrip().startswith(_ERROR_REPLIES):
            return False, result.stdout
        return result.success, output

    @staticmethod
    def _quote_for_stdin(value: str) -> str:
        """
        Render a value as a redis-cli string literal.

        Every byte becomes a hex escape, so nothing in a password can be read as
        quoting or as a separator by the client's argument parser.

        Args:
            value: The raw value.

        Returns:
            A double quoted, fully escaped literal.
        """
        return '"' + "".join(f"\\x{byte:02x}" for byte in value.encode()) + '"'

    def _execute_redis_with_secret(self, command: str, secret: str) -> tuple[bool, str]:
        """
        Run a command whose text carries a secret, over stdin.

        Args:
            command: The complete command line, secret already quoted.
            secret: The secret it carries, kept out of the logs.

        Returns:
            Whether the command succeeded, and its output.
        """
        env = self._client_env()
        result = self._exec(
            [self._cli()],
            input=f"{command}\n",
            env=env,
            timeout=QUERY_TIMEOUT,
            secrets=(secret, *(env.values() if env else ())),
        )
        if result.success and result.stdout.lstrip().startswith(_ERROR_REPLIES):
            return False, result.stdout
        return result.success, result.stdout if result.success else result.stderr

    @staticmethod
    def _quote_argument(value: str | bytes) -> str:
        """
        Render one argument as a redis-cli literal, whatever bytes it holds.

        Args:
            value: Text (sent as UTF-8) or raw bytes, such as a key read back
                from the server.

        Returns:
            A double quoted literal with every byte hex-escaped.
        """
        data = value.encode() if isinstance(value, str) else value
        return '"' + "".join(f"\\x{byte:02x}" for byte in data) + '"'

    def run_commands(
        self,
        commands: Sequence[Sequence[str | bytes]],
        *,
        db: int = 0,
        username: str | None = None,
        password: str | None = None,
        secrets: Sequence[str] = (),
        timeout: int = QUERY_TIMEOUT,
    ) -> str:
        """
        Run several commands through one redis-cli, answers in CSV.

        The commands are written to the client's stdin, one per line, every
        argument hex-escaped (:meth:`_quote_argument`), so a key with a space,
        a quote or a newline is one argument and nothing reaches argv. With
        ``--csv`` each answer is one line - arrays flattened, strings quoted
        and escaped - which is what lets a batch be read back answer by
        answer (:mod:`noust.managers.database.keys` parses it).

        Args:
            commands: The commands, each a sequence of arguments.
            db: The database slot.
            username: Sign in as this ACL user instead of the client's
                default identity.
            password: That user's password, passed as ``REDISCLI_AUTH``.
            secrets: Values the commands carry that must not be logged.
            timeout: Deadline in seconds.

        Returns:
            One CSV line per command.

        Raises:
            RedisSignInError: When ``username`` was refused. redis-cli carries
                on after a refused AUTH with the connection's default
                identity, so a refusal must never pass for a successful read.
            DatabaseQueryError: When the client itself fails.
        """
        script = "".join(
            " ".join(self._quote_argument(argument) for argument in command) + "\n"
            for command in commands
        )
        argv = [self._cli(), "-n", str(db), "--csv"]
        if username is not None:
            argv[1:1] = ["--user", username]
            env: Mapping[str, str] | None = {"REDISCLI_AUTH": password or ""}
        else:
            env = self._client_env()
        result = self._exec(
            argv,
            input=script,
            env=env,
            timeout=timeout,
            secrets=(*secrets, *(env.values() if env else ())),
        )
        if username is not None and (
            "AUTH failed" in result.stderr or "WRONGPASS" in result.stderr
        ):
            raise RedisSignInError(
                f"Redis refused the sign-in as {username}", output=result.stderr.strip()
            )
        if not result.success:
            raise DatabaseQueryError(
                "redis-cli failed", details=(result.stderr or result.stdout).strip()
            )
        return result.stdout

    # ==================== Validation ====================

    @classmethod
    def validate_privileges(cls, privileges: Sequence[str] | None) -> tuple[str, ...]:
        """
        Check ACL rules before they reach ``ACL SETUSER``.

        Redis rules are not SQL keywords, so they get their own grammar. Password
        rules (``>secret``, ``<secret``) are refused: a password must not travel
        through this path, where it would end up in argv.

        Args:
            privileges: Rules requested by the caller, or None for the default.

        Returns:
            The rules, unchanged and without repeats.

        Raises:
            DatabaseUserError: When a rule is not a recognised ACL rule.
        """
        requested = list(privileges) if privileges else ["+@all", "~*"]

        rules: list[str] = []
        for rule in requested:
            if not isinstance(rule, str) or not (
                rule in ACL_KEYWORDS
                or ACL_COMMAND_PATTERN.match(rule)
                or ACL_PATTERN_RULE.match(rule)
            ):
                raise DatabaseUserError(
                    f"Invalid Redis ACL rule: {rule!r}",
                    details=(
                        "Use rules such as '+@all', '-@admin', '+get', '~cache:*' or 'allkeys'. "
                        "Passwords are set with create_user, not with an ACL rule."
                    ),
                )
            if rule not in rules:
                rules.append(rule)
        return tuple(rules)

    @staticmethod
    def _database_number(name: str) -> int:
        """
        Parse a Redis database slot.

        Args:
            name: Slot number as text.

        Returns:
            The slot number.

        Raises:
            DatabaseError: When the value is not a number.
        """
        try:
            return int(name)
        except (TypeError, ValueError) as exc:
            raise DatabaseError(
                f"Invalid Redis database number: {name!r}",
                details="Redis databases are numbered; pass a number such as 0.",
            ) from exc

    def get_status(self) -> dict[str, Any]:
        """
        Summarise the instance, adding mode, clients and memory when running.

        Returns:
            A dictionary describing the instance.
        """
        status = super().get_status()
        if not status["running"]:
            return status

        success, output = self._execute_redis("INFO", "server")
        if success:
            for line in output.splitlines():
                if ":" not in line:
                    continue
                key, value = line.strip().split(":", 1)
                if key == "redis_mode":
                    status["mode"] = value
                elif key == "connected_clients" and value.isdigit():
                    status["clients"] = int(value)
                elif key == "used_memory_human":
                    status["memory"] = value
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
        Refuse to create a database.

        Args:
            name: Ignored.
            owner: Ignored.
            encoding: Ignored.
            **kwargs: Ignored.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always; Redis slots are fixed by configuration.
        """
        raise DatabaseError(
            "Redis uses numbered databases (0-15 by default)",
            details=(
                "Select a slot with SELECT <number>, or raise 'databases' in redis.conf to "
                "expose more."
            ),
        )

    def drop_database(self, name: str, force: bool = False) -> None:
        """
        Delete every key in a database slot.

        Args:
            name: Slot number.
            force: Ignored; a flush is unconditional.

        Raises:
            DatabaseError: When the slot is not a number or the flush fails.
        """
        db_number = self._database_number(name)
        success, output = self._execute_redis("FLUSHDB", db=db_number)
        if not success:
            raise DatabaseError(f"Failed to flush database {db_number}", details=output.strip())
        self.logger.info(f"Flushed database: {db_number}")

    def _database_count(self) -> int:
        """
        Read how many database slots the server exposes.

        Returns:
            The configured slot count, or the default when it cannot be read.
        """
        success, output = self._execute_redis("CONFIG", "GET", "databases")
        if success:
            parts = output.strip().splitlines()
            if len(parts) >= 2 and parts[1].strip().isdigit():
                return int(parts[1].strip())
        return self.DEFAULT_DATABASE_COUNT

    def database_exists(self, name: str) -> bool:
        """
        Report whether a slot number is within range.

        Args:
            name: Slot number.

        Returns:
            True when the slot exists.
        """
        try:
            db_number = int(name)
        except (TypeError, ValueError):
            return False
        return 0 <= db_number < self._database_count()

    def list_databases(self) -> list[DatabaseInfo]:
        """
        List the slots that hold keys, plus slot 0.

        ``owner`` and ``size`` stay unset: a Redis "database" is a numbered
        keyspace slot inside one server process, not an object with a
        catalog entry - there is no owner to report, and INFO's per-slot
        stats give a key count, not the memory a slot itself accounts for
        (that is server-wide, from :meth:`get_status`).

        Returns:
            One entry per listed slot, with its key count.
        """
        keyspace: dict[int, int] = {}
        success, output = self._execute_redis("INFO", "keyspace")
        if not success:
            self._listing_failed("databases", output)
        for line in output.splitlines():
            match = re.match(r"db(\d+):keys=(\d+)", line)
            if match:
                keyspace[int(match.group(1))] = int(match.group(2))

        databases = []
        for db_number in range(self._database_count()):
            keys = keyspace.get(db_number, 0)
            if keys > 0 or db_number == 0:
                databases.append(
                    DatabaseInfo(
                        name=str(db_number),
                        engine=self.ENGINE_NAME,
                        keys=keys,
                        extra={"keys": keys},
                    )
                )
        return databases

    def get_database_info(self, name: str) -> DatabaseInfo:
        """
        Describe one slot.

        Args:
            name: Slot number.

        Returns:
            Key count and the instance's memory usage.

        Raises:
            DatabaseNotFoundError: When the slot is out of range.
        """
        db_number = self._database_number(name)
        if not self.database_exists(name):
            raise DatabaseNotFoundError(
                f"Database {db_number} does not exist",
                details="Raise 'databases' in redis.conf to expose more slots.",
            )

        keys = 0
        success, output = self._execute_redis("DBSIZE", db=db_number)
        if success:
            match = re.search(r"(\d+)", output)
            if match:
                keys = int(match.group(1))

        memory = None
        success, output = self._execute_redis("INFO", "memory")
        if success:
            for line in output.splitlines():
                if line.startswith("used_memory_human:"):
                    memory = line.split(":", 1)[1].strip()
                    break

        return DatabaseInfo(
            name=str(db_number),
            engine=self.ENGINE_NAME,
            size=memory,
            keys=keys,
            extra={"keys": keys},
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
        Create an ACL user (Redis 6 and later).

        The password is stored as the SHA-256 digest Redis accepts with the ``#``
        prefix, so the plain text never appears in a command line.

        Args:
            username: User name.
            password: Password. Generated when omitted.
            host: Recorded for the caller; Redis has no per-host users.
            **kwargs: Accepts ``permissions``, a list of ACL rules.

        Returns:
            The user and its password.

        Raises:
            DatabaseUserError: When the user exists or the server refuses the ACL.
        """
        self.validate_user_name(username)
        if self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' already exists",
                details="Delete the user first, or pick another name.",
            )

        password = password or self.generate_password()
        rules = self.validate_privileges(kwargs.get("permissions"))
        digest = hashlib.sha256(password.encode()).hexdigest()

        success, output = self._execute_redis(
            "ACL", "SETUSER", username, "on", f"#{digest}", *rules
        )
        if not success:
            if "unknown command" in output.lower():
                raise DatabaseUserError(
                    "This Redis server has no ACL support",
                    details="ACL users require Redis 6.0 or later.",
                )
            raise DatabaseUserError(f"Failed to create user '{username}'", details=output.strip())

        self.logger.info(f"Created user: {username}")
        user = UserInfo(
            username=username,
            engine=self.ENGINE_NAME,
            host=host,
            privileges=list(rules),
        )
        return user, password

    def drop_user(self, username: str, host: str = "localhost") -> None:
        """
        Delete an ACL user.

        Args:
            username: User name.
            host: Ignored; Redis has no per-host users.

        Raises:
            DatabaseUserError: When the user is missing or the deletion fails.
        """
        self.validate_user_name(username)
        if not self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Run 'noust db users --engine redis' to see the ACL users.",
            )

        success, output = self._execute_redis("ACL", "DELUSER", username)
        if not success:
            raise DatabaseUserError(f"Failed to delete user '{username}'", details=output.strip())

        self.logger.info(f"Deleted user: {username}")

    def user_exists(self, username: str, host: str = "localhost") -> bool:
        """
        Report whether an ACL user exists.

        Args:
            username: User name.
            host: Ignored; Redis has no per-host users.

        Returns:
            True when ACL LIST mentions the user.
        """
        success, output = self._execute_redis("ACL", "LIST")
        if not success:
            return False
        return any(line.startswith(f"user {username} ") for line in output.splitlines())

    def list_users(self) -> list[UserInfo]:
        """
        List the ACL users.

        ``databases`` stays empty: an ACL rule selects keys by pattern
        (``~app:*``), not by the numbered slot a client happens to ``SELECT``
        into, so there is no per-slot grant to report the way a SQL engine's
        per-database privilege can be. The rule text itself, in
        ``privileges``, is the accurate answer.

        Returns:
            One entry per user, with its rules.
        """
        success, output = self._execute_redis("ACL", "LIST")
        if not success:
            self._listing_failed("users", output)

        users = []
        for line in output.splitlines():
            if not line.startswith("user "):
                continue
            parts = line.split()
            if len(parts) >= 2:
                users.append(
                    UserInfo(
                        username=parts[1],
                        engine=self.ENGINE_NAME,
                        privileges=parts[2:],
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
        Add ACL rules to a user.

        Args:
            username: User name.
            database: Ignored; Redis ACLs are instance wide.
            privileges: ACL rules. Full access when omitted.
            host: Ignored; Redis has no per-host users.

        Raises:
            DatabaseUserError: When a rule is invalid or the server refuses it.
        """
        self.validate_user_name(username)
        if not self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Create the user before granting rules to it.",
            )

        rules = self.validate_privileges(privileges)
        success, output = self._execute_redis("ACL", "SETUSER", username, *rules)
        if not success:
            raise DatabaseUserError(
                f"Failed to grant privileges to '{username}'", details=output.strip()
            )

        self.logger.info(f"Granted {' '.join(rules)} to {username}")

    def revoke_privileges(
        self,
        username: str,
        database: str,
        privileges: Sequence[str] | None = None,
        host: str = "localhost",
    ) -> None:
        """
        Take every command and key away from a user.

        Args:
            username: User name.
            database: Ignored; Redis ACLs are instance wide.
            privileges: Ignored; Redis revokes by resetting the rules.
            host: Ignored; Redis has no per-host users.

        Raises:
            DatabaseUserError: When the user is missing or the server refuses.
        """
        self.validate_user_name(username)
        if not self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Run 'noust db users --engine redis' to see the ACL users.",
            )

        success, output = self._execute_redis("ACL", "SETUSER", username, "nocommands", "resetkeys")
        if not success:
            raise DatabaseUserError(
                f"Failed to revoke privileges from '{username}'", details=output.strip()
            )

        self.logger.info(f"Revoked all rules from {username}")

    # ==================== Backup & Restore ====================

    def backup(
        self,
        database: str,
        output_path: Path | None = None,
        compress: bool = True,
        **kwargs,
    ) -> BackupInfo:
        """
        Copy the instance's RDB snapshot, or its AOF file.

        Redis persists the whole instance, so the database argument is only used
        to label the backup.

        Args:
            database: Ignored; kept for interface compatibility.
            output_path: Custom destination.
            compress: Pipe the file through gzip.
            **kwargs: Accepts ``method``: ``rdb`` (default) or ``aof``.

        Returns:
            Information about the backup.

        Raises:
            DatabaseBackupError: When the save or the copy fails.
        """
        if kwargs.get("method") == "aof":
            return self.backup_aof(output_path=output_path, compress=compress)
        snapshot = self._snapshot_path()

        success, output = self._execute_redis("BGSAVE")
        if not success:
            success, output = self._execute_redis("SAVE")
            if not success:
                raise DatabaseBackupError(
                    "Failed to save the Redis dataset",
                    details=output.strip() or "Check that the server can write to its data dir.",
                )
        self._wait_for("rdb_bgsave_in_progress:0")

        destination = self._backup_path(database, output_path, compress, label="dump")
        return self._dump_to_file(
            ["cat", str(snapshot)],
            destination,
            database="all",
            compress=compress,
        )

    def backup_aof(
        self,
        output_path: Path | None = None,
        compress: bool = True,
    ) -> BackupInfo:
        """
        Rewrite the append-only file and copy the result.

        Args:
            output_path: Custom destination.
            compress: Pipe the file through gzip.

        Returns:
            Information about the backup.

        Raises:
            DatabaseBackupError: When the rewrite or the copy fails, or the
                server runs in a container.
        """
        if self.instance is not None:
            raise DatabaseBackupError(
                "Noust copies a container's Redis as its RDB snapshot only",
                details="Back it up without --method aof: the snapshot holds every slot.",
            )
        success, output = self._execute_redis("BGREWRITEAOF")
        if not success:
            raise DatabaseBackupError(
                "Failed to trigger an AOF rewrite",
                details=output.strip() or "Enable appendonly in redis.conf first.",
            )
        self._wait_for("aof_rewrite_in_progress:0")

        destination = self._backup_path("all", output_path, compress, label="aof", suffix=".aof")
        return self._dump_to_file(
            ["cat", str(self._aof_path())],
            destination,
            database="all",
            compress=compress,
        )

    def _snapshot_path(self) -> PurePosixPath | Path:
        """
        Locate the RDB snapshot the server writes.

        Returns:
            ``dump.rdb`` in the host's data directory; inside a container,
            where the server says (``CONFIG GET dir`` and ``dbfilename``),
            ``/data/dump.rdb`` in the official image.
        """
        if self.instance is None:
            return self.DATA_DIR / "dump.rdb"
        directory, filename = "/data", "dump.rdb"
        for setting in ("dir", "dbfilename"):
            success, output = self._execute_redis("CONFIG", "GET", setting)
            lines = output.strip().splitlines() if success else []
            value = lines[1].strip() if len(lines) >= 2 else ""
            if value and setting == "dir":
                directory = value
            elif value:
                filename = value
        return PurePosixPath(directory) / PurePosixPath(filename).name

    def _wait_for(self, marker: str) -> None:
        """
        Wait until a persistence counter reports the operation has finished.

        Args:
            marker: The INFO persistence line that means "done".
        """
        for _ in range(self.PERSISTENCE_POLLS):
            success, output = self._execute_redis("INFO", "persistence")
            if success and marker in output:
                return
            time.sleep(1)
        self.logger.warning(f"Timed out waiting for Redis persistence marker {marker}")

    def _aof_path(self) -> Path:
        """
        Locate the append-only file, honouring the Redis 7 directory layout.

        Returns:
            The path of the AOF file to copy.
        """
        success, output = self._execute_redis("CONFIG", "GET", "appendfilename")
        parts = output.strip().splitlines() if success else []
        filename = parts[1].strip() if len(parts) >= 2 and parts[1].strip() else "appendonly.aof"

        success, output = self._execute_redis("CONFIG", "GET", "appenddirname")
        dir_parts = output.strip().splitlines() if success else []
        if len(dir_parts) >= 2 and dir_parts[1].strip():
            candidate = self.DATA_DIR / dir_parts[1].strip() / filename
            if candidate.exists():
                return candidate
        return self.DATA_DIR / filename

    def list_backups(self, database: str | None = None) -> list[BackupInfo]:
        """
        List the snapshots, whichever slot was asked about.

        A snapshot holds every slot of the instance, so a slot's page must
        show all of them: filtering on the slot number, as the other engines
        filter on a database name, showed none.

        Args:
            database: Ignored; every snapshot covers every slot.

        Returns:
            Snapshots, newest first.
        """
        return super().list_backups(database=None)

    def _append_only(self) -> bool:
        """
        Ask whether the server persists through the append-only file.

        Returns:
            True when ``appendonly`` is ``yes``.
        """
        success, output = self._execute_redis("CONFIG", "GET", "appendonly")
        lines = output.strip().splitlines() if success else []
        return len(lines) >= 2 and lines[1].strip().lower() == "yes"

    def restore(
        self,
        database: str,
        backup_path: Path,
        drop_existing: bool = False,
        *,
        safety_backup: bool = True,
        **kwargs,
    ) -> RestoreOutcome:
        """
        Replace the instance's snapshot with a backup, keeping a safety copy.

        A snapshot replaces every slot, so the safety copy is always taken.
        With ``appendonly yes`` the restore is refused before anything
        changes: Redis rebuilds its data from the append-only file when both
        exist, so the copied snapshot would be ignored and the previous
        version reported "Restored" over data that never changed. When the
        new snapshot cannot be handed to the server's account, the safety
        copy is put back.

        Args:
            database: Ignored; a Redis snapshot covers the whole instance.
            backup_path: Path to the backup file, plain or gzipped.
            drop_existing: Ignored; the snapshot replaces everything.
            safety_backup: Ignored; the safety copy is always taken.
            **kwargs: ``on_safety_copy``, called with the safety copy before
                the snapshot is replaced (see the base class).

        Returns:
            What was done.

        Raises:
            DatabaseBackupError: When the file is missing, the server uses
                the append-only file, or the snapshot cannot be installed;
                and for a container, whose data directory is a volume Noust
                does not write into.
        """
        if self.instance is not None:
            raise DatabaseBackupError(
                f"Noust does not restore the snapshot of the container {self.instance.container}",
                details=(
                    "A snapshot is installed with the server stopped, into its data volume. "
                    f"Stop the container (docker stop {self.instance.container}), copy the "
                    "file in as dump.rdb (docker cp <file> "
                    f"{self.instance.container}:/data/dump.rdb), make it readable by the "
                    "server's account and start it again."
                ),
            )
        backup_path = Path(backup_path)
        if not backup_path.exists():
            raise DatabaseBackupError(
                f"Backup file not found: {backup_path}",
                details="Run 'noust db backups' to list the backups Noust knows about.",
            )
        if self._append_only():
            raise DatabaseBackupError(
                "This server rebuilds its data from the append-only file, not from a snapshot",
                details=(
                    "appendonly is yes, so Redis would ignore the restored dump.rdb and "
                    "keep the data it has. Turn it off first (redis-cli CONFIG SET "
                    "appendonly no, and in redis.conf), restore, then turn it back on: "
                    "Redis rewrites the append-only file from the restored data."
                ),
            )

        safety = self.backup("all").path
        self.logger.info(f"Safety copy of the instance taken before the restore: {safety}")
        on_safety_copy = kwargs.get("on_safety_copy")
        if callable(on_safety_copy):
            on_safety_copy(safety)
        try:
            self._load_backup("all", backup_path)
        except DatabaseBackupError as exc:
            try:
                self._load_backup("all", safety)
            except DatabaseBackupError as again:
                raise DatabaseBackupError(
                    "Restoring the snapshot failed, and putting the previous one back failed too",
                    details=(
                        f"The restore said:\n{exc.details or exc}\n\nThe safety copy said:\n"
                        f"{again.details or again}\n\nThe safety copy is kept at {safety}."
                    ),
                ) from exc
            raise DatabaseBackupError(
                "Restoring the snapshot failed; the previous one was put back",
                details=f"{exc.details or exc}\n\nThe safety copy is kept at {safety}.",
            ) from exc

        self.logger.info(f"Restored Redis from: {backup_path}")
        return RestoreOutcome(database="all", source=backup_path, safety_copy=safety, replaced=True)

    def _load_backup(self, database: str, backup_path: Path, **kwargs: Any) -> None:
        """
        Install a snapshot as ``dump.rdb`` with the server stopped.

        The server is started again whatever happens: it may still read a
        file it does not own, or it may fail, and the operator needs its
        journal for that, not a stopped service.

        Args:
            database: Ignored; a snapshot covers the instance.
            backup_path: The snapshot, plain or gzipped.
            **kwargs: Unused.

        Raises:
            DatabaseBackupError: When the file cannot be written or handed to
                the server's account.
        """
        rdb_file = self.DATA_DIR / "dump.rdb"
        self.stop()
        try:
            if backup_path.suffix == ".gz":
                result = self.runner.capture_to_file(
                    ["gzip", "-dc", str(backup_path)],
                    rdb_file,
                    timeout=TRANSFER_TIMEOUT,
                )
            else:
                result = self._exec(
                    ["cp", str(backup_path), str(rdb_file)], timeout=TRANSFER_TIMEOUT
                )
            if not result.success:
                raise DatabaseBackupError(
                    "Failed to install the backup file",
                    details=result.stderr.strip() or f"Could not write {rdb_file}.",
                )
            handed_over = hand_over_file(
                rdb_file,
                user=self.DATA_OWNER,
                group=self.DATA_OWNER,
                mode=0o660,
                runner=self.runner,
                logger=self.logger,
            )
            if not handed_over:
                raise DatabaseBackupError(
                    f"Could not give {rdb_file} to the {self.DATA_OWNER} account",
                    details=(
                        f"The server runs as {self.DATA_OWNER} and could not read a snapshot "
                        "root owns. Check that the account exists (id redis)."
                    ),
                )
        finally:
            self.start()

    # ==================== Query Execution ====================

    def execute_query(
        self,
        database: str,
        query: str,
        **kwargs,
    ) -> tuple[bool, str]:
        """
        Run a Redis command given as text.

        Args:
            database: Slot number.
            query: The command, split on whitespace.
            **kwargs: Unused.

        Returns:
            Success and the command's output.

        Raises:
            DatabaseQueryError: When the command is empty or fails.
        """
        try:
            db_number = int(database)
        except (TypeError, ValueError):
            db_number = 0

        # Split the way redis-cli's own prompt does, quotes included, so
        # SET greeting "hello world" sets one value and not two arguments.
        try:
            parts = shlex.split(query)
        except ValueError as exc:
            raise DatabaseQueryError(
                "The Redis command has an unbalanced quote",
                details=f"{exc}. Close the quote, or escape it with a backslash.",
            ) from exc
        if not parts:
            raise DatabaseQueryError(
                "Empty Redis command", details="Pass a command such as 'INFO server'."
            )

        success, output = self._execute_redis(*parts, db=db_number)
        if not success:
            raise DatabaseQueryError("Command failed", details=output.strip())
        return success, output

    def get_connection_string(
        self,
        database: str,
        username: str,
        password: str,
        host: str = "localhost",
    ) -> str:
        """
        Build a Redis URI, the slot as its path.

        Args:
            database: Slot number; anything else means slot 0.
            username: ACL user, or ``default`` for the password alone.
            password: Password.
            host: Host to connect to.

        Returns:
            The connection string.
        """
        try:
            db_number = int(database)
        except (TypeError, ValueError):
            db_number = 0
        return super().get_connection_string(str(db_number), username, password, host)

    def server_port(self) -> int:
        """
        Return the port the server says it listens on.

        Returns:
            ``CONFIG GET port``, or :attr:`DEFAULT_PORT` when it cannot say;
            for a container, the port it is reached on from the host.
        """
        if self.instance is not None:
            return self.instance.port
        cached = getattr(self, "_port", None)
        if cached is not None:
            return int(cached)
        success, output = self._execute_redis("CONFIG", "GET", "port")
        lines = output.strip().splitlines() if success else []
        if len(lines) >= 2 and lines[1].strip().isdigit():
            self._port = int(lines[1].strip())
            return self._port
        return self.DEFAULT_PORT

    def listen_addresses(self) -> ListenAddress | None:
        """
        Ask the server for its ``bind`` setting.

        Returns:
            The addresses, or None when the server cannot say.
        """
        success, output = self._execute_redis("CONFIG", "GET", "bind")
        lines = output.strip().splitlines() if success else []
        if len(lines) < 2:
            return None
        return listen_address("bind", lines[1])

    def warnings(self) -> list[str]:
        """
        Warn when the instance takes commands from anyone without a password.

        Returns:
            One sentence when no password is known and the server answered.
        """
        if self._known_password():
            return []
        success, output = self._execute_redis("ACL", "WHOAMI")
        if success and output.strip() == "default":
            return [
                "Redis accepts every command without a password: any process on this "
                "server can read and change every key. Set one with 'noust db "
                "user-password default --engine redis'."
            ]
        return []

    def set_user_password(self, username: str, password: str, host: str = "localhost") -> None:
        """
        Give an ACL user a new password, or the instance a new ``requirepass``.

        Args:
            username: The ACL user, or ``default`` for ``requirepass``.
            password: The new password.
            host: Ignored; Redis has no per-host users.

        Raises:
            DatabaseUserError: When the user does not exist or the server
                refuses.
        """
        if username in ("", "default"):
            try:
                self.set_password(password)
            except DatabaseError as exc:
                raise DatabaseUserError(str(exc), details=exc.details) from exc
            return
        self.validate_user_name(username)
        if not self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Run 'noust db user-list --engine redis' to see the ACL users.",
            )
        digest = hashlib.sha256(password.encode()).hexdigest()
        success, output = self._execute_redis("ACL", "SETUSER", username, "resetpass", f"#{digest}")
        if not success:
            raise DatabaseUserError(
                f"Failed to change the password of '{username}'", details=output.strip()
            )
        self._execute_redis("ACL", "SAVE")
        self.logger.info(f"Changed the password of: {username}")

    def apply_profile(
        self, username: str, database: str, profile: str, host: str = "localhost"
    ) -> None:
        """
        Give an ACL user one of the profile presets.

        Redis ACLs are instance-wide, so the profile holds on every slot: the
        ``database`` argument names the slot only for the caller's messages.

        Args:
            username: The ACL user.
            database: Ignored beyond messages.
            profile: ``owner``, ``read_write`` or ``read_only``.
            host: Ignored; Redis has no per-host users.

        Raises:
            DatabaseUserError: When the profile is unknown, the user does not
                exist, or the server refuses.
        """
        self.validate_user_name(username)
        if profile not in PROFILES:
            raise DatabaseUserError(
                f"Unknown access profile: {profile!r}",
                details=f"Use one of: {', '.join(PROFILES)}.",
            )
        if not self.user_exists(username):
            raise DatabaseUserError(
                f"User '{username}' does not exist",
                details="Create it first with 'noust db user-create --engine redis'.",
            )
        success, output = self._execute_redis(
            "ACL",
            "SETUSER",
            username,
            "nocommands",
            "resetkeys",
            "resetchannels",
            *PROFILE_RULES[profile],
        )
        if not success:
            raise DatabaseUserError(
                f"Failed to give '{username}' the {profile} profile", details=output.strip()
            )
        self._execute_redis("ACL", "SAVE")

    def list_access(self, database: str) -> list[AccessEntry]:
        """
        List the ACL users with the profile their rules amount to.

        Args:
            database: Ignored; ACLs are instance-wide.

        Returns:
            One entry per ACL user.
        """
        entries: list[AccessEntry] = []
        for user in self.list_users():
            rules = set(user.privileges)
            if "+@all" in rules and "-@dangerous" not in rules:
                profile = "owner"
            elif "+@all" in rules:
                profile = "read_write"
            elif "+@read" in rules and "+@write" not in rules:
                profile = "read_only"
            else:
                profile = "custom"
            entries.append(
                AccessEntry(
                    username=user.username,
                    profile=profile,
                    privileges=tuple(user.privileges),
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
        Build the command that opens a redis-cli session.

        Args:
            database: Slot to select.
            username: ACL user to connect as.

        Returns:
            The argument vector.
        """
        argv = [self._cli()]
        if database is not None and str(database).isdigit():
            argv.extend(["-n", str(int(database))])
        if username:
            argv.extend(["--user", username])
        return self._interactive(argv)

    # ==================== Redis-specific ====================

    def set_password(self, password: str) -> None:
        """
        Set ``requirepass`` and persist it to the configuration file.

        The password is sent on stdin, hex escaped, so it appears neither in argv
        nor in the client's own parsing edge cases.

        Args:
            password: The new password.

        Raises:
            DatabaseError: When the server refuses the change, or it runs in
                a container, whose command line or environment sets it.
        """
        self.refuse_in_container("change the password of")
        success, output = self._execute_redis_with_secret(
            f"CONFIG SET requirepass {self._quote_for_stdin(password)}", password
        )
        if not success:
            raise DatabaseError("Failed to set the Redis password", details=output.strip())

        self._password = password
        self._password_loaded = True
        # Kept so the next process authenticates too: the password used to
        # live only in the object that set it, and every later command failed
        # with NOAUTH.
        self._secrets().write(REQUIREPASS_SECRET, password)
        self._execute_redis("CONFIG", "REWRITE")

    def get_memory_stats(self) -> dict[str, str]:
        """
        Read the memory section of INFO.

        Returns:
            The section as key-value pairs.
        """
        success, output = self._execute_redis("INFO", "memory")
        if not success:
            return {}
        stats = {}
        for line in output.splitlines():
            if ":" in line:
                key, value = line.strip().split(":", 1)
                stats[key] = value
        return stats

    def flush_all(self) -> None:
        """
        Delete every key in every slot.

        Raises:
            DatabaseError: When the server refuses.
        """
        success, output = self._execute_redis("FLUSHALL")
        if not success:
            raise DatabaseError("Failed to flush all databases", details=output.strip())


DatabaseRegistry.register(RedisManager, aliases=["redis-server", "valkey", "valkey-server"])
