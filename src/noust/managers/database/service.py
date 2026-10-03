# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a database is, to Noust: the one implementation behind the CLI and the API.

Before 3.1 there were two. ``noust db create`` recorded the new database in
the store and ``POST /api/databases/databases`` did not; ``noust db drop``
forgot it and ``DELETE`` did not. A database made in the console never
appeared in its application's backups, and one dropped from the console left
a row behind that made the application's next backup fail. Both front ends
now call :class:`DatabaseService`, and so do the jobs, so the store cannot
disagree with itself depending on which door was used.

Engine and store:

- The **engine** is the truth for what exists and what an account can do.
- The **store** is the truth for what Noust manages: which databases it
  tracks, which application each belongs to, which variable of which
  application carries its connection string (``database_links``), and what
  Noust last did to an account (``database_accounts``). Passwords Noust knows
  live in the secret store (``databases/<engine>/<user>``), never in a row.

The guards live here, not in the endpoints (rule 4): an internal account
(the engine's superuser, the read-only console's ``wasm_ro_`` accounts) is
never altered or dropped; a database linked to an application is never
dropped without unlinking it; a drop and a restore never destroy without a
safety copy; a password rotation that leaves an application down is undone.

Every change is recorded with :func:`noust.core.audit.record`, under the
actor the caller names (the console's session, a job's), or the one the CLI
command bound.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from noust.core import audit
from noust.core.config import Config
from noust.core.ens import profile as security_profile
from noust.core.exceptions import (
    ConfigError,
    DatabaseAccessError,
    DatabaseEngineError,
    DatabaseError,
    DatabaseExistsError,
    DatabaseNotFoundError,
    DatabaseQueryError,
    DatabaseUserError,
    DeploymentError,
    NoustError,
    ValidationError,
)
from noust.core.logger import Logger
from noust.core.secrets import SecretStore
from noust.core.store import App, Database, NoustStore, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.helpers.databases import (
    SUPPORTED_ENGINES,
    _owner_secret,
    _password_secret,
    database_identifiers,
    provision_database,
)
from noust.managers.database.base import (
    PROFILES,
    READ_ONLY_ACCOUNT_PREFIX,
    AccessEntry,
    BackupInfo,
    BaseDatabaseManager,
    RestoreOutcome,
    UserInfo,
    console_statement,
)
from noust.managers.database.detect_links import (
    AppReferences,
    Detected,
    Endpoint,
    detect,
    read_environment,
    references_in,
)
from noust.managers.database.engine_setup import (
    EnginePlan,
    InstallOutcome,
    install_catalog,
    install_engine,
    plan_engine_install,
)
from noust.managers.database.exposure import (
    ExposedPort,
    find_exposed_database_ports,
    public_address,
    ssh_port,
    tunnel_instructions,
)
from noust.managers.database.instances import (
    DatabaseInstance,
    assign_apps,
    discover,
    engine_of,
    instance_key,
    is_instance_key,
    parse_instance_key,
)
from noust.managers.database.linking import (
    EXTRA_VARIABLES,
    change_app_env,
    restore_app_env,
)
from noust.managers.database.records import DatabaseLink, DatabaseRecords, default_env_var
from noust.managers.database.registry import DatabaseRegistry, get_db_manager
from noust.managers.database.settings import SettingsOutcome, SettingsReport, settings_for
from noust.managers.database.urls import connection_url, masked
from noust.validators.domain import validate_domain
from noust.validators.names import resolve_within, validate_filename

#: Longest console statement accepted, by the CLI and the API alike.
MAX_QUERY_LENGTH = 20_000

#: Where the operator can pin the address the Connect instructions use.
PUBLIC_ADDRESS_SETTING = "server.public_address"

#: Stands in for a password in a URL that is only ever shown masked.
_PLACEHOLDER = "PASSWORD"

#: The engines that sign in with an account the operator stores. PostgreSQL
#: signs in as the system's postgres user over its socket, and MongoDB as the
#: administrator Noust created; neither reads a stored password.
CREDENTIAL_ENGINES = frozenset({"mysql", "redis"})


class _CredentialOverlay:
    """
    The configuration, with one engine's credentials changed as saving would.

    Lets a manager try an account before it is saved, through the same code
    that will use it afterwards. Only what is given replaces the stored
    value; an empty user or password keeps the stored one, which is what
    :meth:`DatabaseService.set_credentials` writes.
    """

    def __init__(self, base: Any, engine: str, user: str | None, password: str | None) -> None:
        """
        Args:
            base: The real configuration.
            engine: The engine whose credentials change.
            user: The candidate user.
            password: The candidate password.
        """
        self._base = base
        self._engine = engine
        self._account = {
            key: value for key, value in (("user", user), ("password", password)) if value
        }

    def get(self, key: str, default: Any = None) -> Any:
        """
        Read a key, with the candidate account in ``databases.credentials``.

        Args:
            key: Configuration key.
            default: Value when absent.

        Returns:
            The value.
        """
        value = self._base.get(key, default)
        if key != "databases":
            return value
        databases = dict(value or {})
        credentials = dict(databases.get("credentials") or {})
        # What is given replaces only its own key, as saving it will: a
        # password alone is tried with the stored user, not the default one.
        credentials[self._engine] = {
            **dict(credentials.get(self._engine) or {}),
            **self._account,
        }
        databases["credentials"] = credentials
        return databases

    def __getattr__(self, name: str) -> Any:
        """Everything else is the real configuration's."""
        return getattr(self._base, name)


#: Application types whose deployers provision their own databases (from the
#: repository's compose file): a database asked for with their first deploy is
#: refused rather than created and never written into their environment.
OWN_DATABASE_TYPES = frozenset({"monorepo", "docker-compose"})

#: The profile a new application account gets when a link creates it.
LINK_PROFILE = "owner"

#: The audit event a refused change to an internal account is recorded under.
_GUARD_EVENTS: dict[str, str] = {
    "drop": "db.user.drop",
    "grant": "db.grant",
    "revoke": "db.revoke",
    "profile": "db.user.profile",
    "password": "db.user.rotate",
    "link": "db.link",
}


def console_request(query: str, *, single: bool) -> str:
    """
    Check a console statement before any engine sees it.

    The one guard the CLI and the API share. Length is checked first, so the
    console is never a file upload; then, when ``single`` asks for it, the
    text is held to one statement (see
    :func:`~noust.managers.database.base.console_statement` for why a second
    statement is refused, and why a ``;`` inside a literal is too). There is
    no keyword allow-list: what a read may do is decided by the server,
    through the least-privilege account the read runs as.

    Args:
        query: The statement as the operator typed it.
        single: Hold it to one statement.

    Returns:
        The statement; with ``single``, stripped and without its trailing
        semicolon.

    Raises:
        DatabaseQueryError: When it is empty, too long or, with ``single``,
            more than one statement.
    """
    if len(query.strip()) > MAX_QUERY_LENGTH:
        raise DatabaseQueryError(
            f"Statement is too long: {len(query.strip())} characters",
            details=(
                f"The console accepts at most {MAX_QUERY_LENGTH} characters. Put a longer "
                "script in a file and feed it to the engine's own client with "
                "'noust db connect'."
            ),
        )
    return console_statement(query, read_only=single)


# ============================================================ views


@dataclass
class DatabaseView:
    """
    One database, as the engine and the store together describe it.

    Attributes:
        name: The database (a slot number for Redis).
        engine: Canonical engine name.
        size: Human-readable size, when the engine reports one.
        tables: Tables or collections, None when the listing does not count
            them (PostgreSQL would need a connection per database).
        keys: Keys in a Redis slot.
        owner: The owning account, for engines that have one.
        encoding: Character set or encoding.
        tracked: Whether the store records it: it is Noust's, it is backed up
            with its application, and it can be linked.
        missing: Recorded in the store but gone from the engine (dropped by
            hand); its application's backups would fail until it is forgotten.
        unverified: Recorded in the store, on an engine that could not be read
            this time: whether it is still there is not known.
        app: The application it belongs to, whose backups include it.
        apps: Every application linked to it.
        detected_apps: Applications whose environment names it, without a
            link Noust recorded: they use it, and "record the link" makes it
            one without touching their ``.env``.
        username: The account Noust provisioned for it.
        engine_version: The engine's version.
        last_backup: When the newest dump of it was taken, ISO 8601.
    """

    name: str
    engine: str
    size: str | None = None
    tables: int | None = None
    keys: int | None = None
    owner: str | None = None
    encoding: str | None = None
    tracked: bool = False
    missing: bool = False
    unverified: bool = False
    app: str | None = None
    apps: list[str] = field(default_factory=list)
    detected_apps: list[str] = field(default_factory=list)
    username: str | None = None
    engine_version: str | None = None
    last_backup: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the view as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


@dataclass
class ListingProblem:
    """
    An engine whose databases could not be read, and why.

    Attributes:
        engine: The engine.
        display_name: Its name for people.
        message: What failed.
        hint: How to fix it.
        output: The engine's own message, verbatim.
        access: The engine refused to sign Noust in (the fix is credentials).
        kind: ``host`` for the host's engine, ``container`` for an instance
            Docker runs, ``docker`` when Docker itself could not be asked.
    """

    engine: str
    display_name: str
    message: str
    hint: str
    output: str
    access: bool
    kind: str = "host"

    def to_dict(self) -> dict[str, Any]:
        """
        Render the problem as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


@dataclass
class DatabaseListing:
    """
    Every database the engines report, and the engines that could not be read.

    Attributes:
        databases: The databases.
        problems: One entry per engine that failed.
    """

    databases: list[DatabaseView]
    problems: list[ListingProblem]


@dataclass
class AccessView:
    """
    One account's access to one database, with what Noust knows about it.

    Attributes:
        username: The account.
        host: Its host restriction.
        profile: ``owner``, ``read_write``, ``read_only`` or ``custom``.
        privileges: The grants behind it.
        internal: The engine's or Noust's own account; never changed here.
        managed: Noust knows its password (it created or rotated it).
        apps: Applications whose connection string signs in as it.
        password_changed_at: When Noust last set its password.
    """

    username: str
    host: str = "localhost"
    profile: str = "custom"
    privileges: list[str] = field(default_factory=list)
    internal: bool = False
    managed: bool = False
    apps: list[str] = field(default_factory=list)
    password_changed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the view as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


@dataclass
class LinkView:
    """
    One database an application uses, for the application's Database tab.

    Attributes:
        domain: The application.
        engine: Canonical engine name.
        database: The database.
        username: The account its connection string signs in as.
        env_var: The variable holding the connection string.
        extra_vars: Whether the ``DB_*`` variables are written too.
        url: The connection string with the password masked.
        exists: Whether the database is still on the engine.
        size: Its size, when known.
        engine_version: The engine's version.
        created_at: When it was linked.
    """

    domain: str
    engine: str
    database: str
    username: str | None
    env_var: str
    extra_vars: bool
    url: str | None
    exists: bool = True
    size: str | None = None
    engine_version: str | None = None
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the view as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


@dataclass
class DropOutcome:
    """
    What :meth:`DatabaseService.drop` did.

    Attributes:
        engine: Canonical engine name.
        database: The dropped database.
        safety_copy: The dump taken first, or None when waived.
        unlinked: Applications whose variables were removed.
    """

    engine: str
    database: str
    safety_copy: str | None
    unlinked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the outcome as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


@dataclass
class LinkOutcome:
    """
    What linking a database to an application did.

    Attributes:
        domain: The application.
        engine: Canonical engine name.
        database: The database.
        username: The account the connection string signs in as.
        env_vars: The variables written.
        restarted: Whether the application restarted on them.
        created_database: Whether the database was created for it.
        created_user: Whether the account was created for it.
    """

    domain: str
    engine: str
    database: str
    username: str | None
    env_vars: list[str]
    restarted: bool
    created_database: bool = False
    created_user: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Render the outcome as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


@dataclass
class RotationOutcome:
    """
    What a password rotation did.

    Attributes:
        engine: Canonical engine name.
        username: The account.
        password: The new password. Handed back once, to the caller that
            asked; the API never puts it in a job result.
        apps: Applications whose variables now carry it.
        restarted: Those restarted on it behind their gate.
    """

    engine: str
    username: str
    password: str = field(repr=False)
    apps: list[str] = field(default_factory=list)
    restarted: list[str] = field(default_factory=list)

    def to_dict(self, *, include_password: bool = False) -> dict[str, Any]:
        """
        Render the outcome as plain data.

        Args:
            include_password: Keep the password in the output.

        Returns:
            A JSON-serialisable dictionary, without the password by default.
        """
        data = asdict(self)
        if not include_password:
            data.pop("password")
        return data


@dataclass(frozen=True)
class NewAppDatabase:
    """
    A database provisioned for an application its first deploy is creating.

    Made by :meth:`DatabaseService.prepare_for_new_app` before anything is
    built, so the first build and the first start already have the connection
    string; linked by :meth:`DatabaseService.link_new_app` once the deploy
    created the application.

    Attributes:
        domain: The application.
        engine: Canonical engine name.
        database: The database (a slot for Redis).
        username: The account the connection string signs in as.
        env_var: The variable carrying the connection string.
        extra_vars: Whether the ``DB_*`` variables are written too.
        created_database: Whether the database was created for it, rather
            than kept from an earlier attempt.
        values: The variables to write. They carry the password, so they
            stay out of ``repr``.
    """

    domain: str
    engine: str
    database: str
    username: str | None
    env_var: str
    extra_vars: bool
    created_database: bool
    values: dict[str, str] = field(repr=False)

    @property
    def secret_marks(self) -> dict[str, bool]:
        """Every variable written, marked secret, as a link marks them."""
        return dict.fromkeys(self.values, True)

    def configure_options(self, marks: dict[str, bool] | None = None) -> dict[str, Any]:
        """
        Say what a deployer's ``configure`` is given for this database.

        Args:
            marks: The secret marks the application starts with otherwise.

        Returns:
            ``database_env`` (the variables, written into the environment
            with the operator's before the build) and ``env_secret_marks``
            (``marks`` with these variables marked secret).
        """
        return {
            "database_env": dict(self.values),
            "env_secret_marks": {**(marks or {}), **self.secret_marks},
        }


# ============================================================ service


class DatabaseService:
    """
    Every database operation Noust offers, for every front end.

    Construct one per request or command; it holds no state beyond its
    collaborators.
    """

    def __init__(
        self,
        *,
        actor: audit.Actor | None = None,
        logger: Logger | None = None,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        resolve: Callable[[str], BaseDatabaseManager | None] | None = None,
        engines: Callable[[], list[str]] | None = None,
        instances: Callable[[], list[DatabaseInstance]] | None = None,
    ) -> None:
        """
        Args:
            actor: Who acts, for the audit trail; the bound actor when None.
            logger: Where progress is reported. A job passes its capturing
                logger so the steps reach the job log.
            store: The store. Defaults to the process-wide one.
            secrets: The secret store. Defaults to one beside the store.
            resolve: Engine name to manager; the registry by default.
            engines: The engine names to list; the registry's by default.
                A list given here is the whole list: no container is
                discovered unless ``instances`` is given too.
            instances: The database containers on this server; Docker's,
                discovered once per service, by default.
        """
        self.actor = actor
        self.logger = logger or Logger()
        self._store = store
        self._secrets = secrets
        self._resolve = resolve
        self._engine_names = engines
        self._instances = instances
        self._discovered: list[DatabaseInstance] | None = None

    # ------------------------------------------------------------ plumbing

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use so a refused request never opens it."""
        if self._store is None:
            self._store = get_store()
        return self._store

    @property
    def secrets(self) -> SecretStore:
        """The secret store, resolved on first use."""
        if self._secrets is None:
            self._secrets = SecretStore()
        return self._secrets

    @property
    def records(self) -> DatabaseRecords:
        """The links and accounts tables."""
        return DatabaseRecords(self.store)

    def audit(self, event: str, target: str, outcome: str = "ok", **details: Any) -> None:
        """
        Record a change in the audit trail.

        Args:
            event: A catalog name, such as ``db.drop``.
            target: What it happened to, such as ``postgresql/shop``.
            outcome: ``ok``, ``failure`` or ``denied``.
            **details: Anything else worth keeping. Never a credential.
        """
        audit.record(
            event,
            actor=self.actor,
            target=f"db:{target}",
            outcome=outcome,
            details={key: value for key, value in details.items() if value is not None},
        )

    def manager(self, engine: str) -> BaseDatabaseManager:
        """
        Resolve an engine name or alias to its manager.

        Args:
            engine: The name as the caller gave it.

        Returns:
            The manager.

        Raises:
            DatabaseEngineError: When no engine answers to the name. The
                registry is the allow-list, and for an instance key the
                containers Docker runs.
        """
        resolve = self._resolve or (lambda name: get_db_manager(name, verbose=False))
        if is_instance_key(engine):
            return self._instance_manager(engine, resolve)
        manager = resolve(engine)
        if manager is None:
            names = self._engine_names() if self._engine_names else DatabaseRegistry.list_engines()
            raise DatabaseEngineError(
                f"Unknown database engine: {engine}",
                details=f"Available engines: {', '.join(names)}.",
            )
        return manager

    def instances(self) -> list[DatabaseInstance]:
        """
        The database containers on this server, each with its application.

        Discovered once per service (it is built per request or command).

        Returns:
            The instances, sorted by key.

        Raises:
            DatabaseQueryError: When Docker does not answer.
        """
        if self._discovered is None:
            if self._instances is not None:
                found = self._instances()
            elif self._engine_names is not None:
                found = []
            else:
                found = assign_apps(discover(), self.store.list_apps())
            self._discovered = found
        return self._discovered

    def _instance_manager(
        self, key: str, resolve: Callable[[str], BaseDatabaseManager | None]
    ) -> BaseDatabaseManager:
        """
        Resolve an instance key to a manager bound to its container.

        Args:
            key: ``engine@project.service`` or ``engine@container``; the
                engine part may be an alias (``pg@...``).
            resolve: Engine name to manager.

        Returns:
            The manager.

        Raises:
            DatabaseEngineError: When the key is malformed, its engine is
                unknown, or no container answers to it.
        """
        wanted, base = self._instance_key(key, resolve)
        found = next((item for item in self.instances() if item.key == wanted), None)
        if found is None:
            known = ", ".join(item.key for item in self.instances()) or "none"
            raise DatabaseEngineError(
                f"No database container answers to {key}",
                details=f"Containers found: {known}. List them with: noust db engines",
            )
        return base.bind(found)

    def _instance_key(
        self, key: str, resolve: Callable[[str], BaseDatabaseManager | None]
    ) -> tuple[str, BaseDatabaseManager]:
        """
        Spell an instance key the way the store files it, and find its engine.

        Nothing asks Docker: this is what a store row is found by, whether
        or not its container still exists.

        Args:
            key: ``engine@project.service`` or ``engine@container``; the
                engine part may be an alias (``pg@...``).
            resolve: Engine name to manager.

        Returns:
            The canonical key and an unbound manager of its engine.

        Raises:
            DatabaseEngineError: When the key is malformed or its engine unknown.
        """
        try:
            parts = parse_instance_key(key)
        except ValidationError as exc:
            raise DatabaseEngineError(exc.message, details=exc.details) from exc
        canonical = DatabaseRegistry.canonical(parts.engine) or parts.engine
        base = resolve(canonical)
        if base is None:
            raise DatabaseEngineError(
                f"Unknown database engine in {key}: {parts.engine}",
                details=f"Available engines: {', '.join(DatabaseRegistry.list_engines())}.",
            )
        wanted = instance_key(
            canonical, project=parts.project, service=parts.service, container=parts.container
        )
        return wanted, base

    def store_key(self, engine: str) -> tuple[str, BaseDatabaseManager]:
        """
        Name the key an engine's rows are filed under, without reaching a container.

        Unlinking or forgetting only changes the store, so a container that
        no longer exists must not stand in the way.

        Args:
            engine: An engine name, an alias or an instance key.

        Returns:
            The canonical key and a manager able to validate names; bound
            only for the host's engines, which need no Docker.

        Raises:
            DatabaseEngineError: When no engine answers to the name.
        """
        if not is_instance_key(engine):
            manager = self.manager(engine)
            return manager.ENGINE_NAME, manager
        resolve = self._resolve or (lambda name: get_db_manager(name, verbose=False))
        return self._instance_key(engine, resolve)

    def _container_gone(self, key: str) -> bool:
        """
        Say whether Docker answered and runs no container under a key.

        Args:
            key: A canonical instance key.

        Returns:
            True when the container is gone, so nothing can hold its databases.

        Raises:
            DatabaseQueryError: When Docker does not answer: then nothing
                is proven.
        """
        return is_instance_key(key) and all(item.key != key for item in self.instances())

    def instance_managers(
        self, problems: list[ListingProblem] | None = None
    ) -> list[BaseDatabaseManager]:
        """
        One manager per database container.

        Args:
            problems: Where Docker's failure to answer is recorded; logged
                when None.

        Returns:
            The bound managers, by key.
        """
        try:
            found = self.instances()
        except DatabaseError as exc:
            if problems is None:
                self.logger.warning(f"Could not list the database containers: {exc}")
            else:
                problems.append(
                    ListingProblem(
                        engine="docker",
                        display_name="Docker",
                        message=exc.message,
                        hint=exc.details,
                        output=exc.output or "",
                        access=False,
                        kind="docker",
                    )
                )
            return []
        resolve = self._resolve or (lambda name: get_db_manager(name, verbose=False))
        managers: list[BaseDatabaseManager] = []
        for instance in found:
            base = resolve(instance.engine)
            if base is not None:
                managers.append(base.bind(instance))
        return managers

    def running(self, engine: str) -> BaseDatabaseManager:
        """
        Resolve an engine that must be installed and running.

        Args:
            engine: The engine name.

        Returns:
            The manager.

        Raises:
            DatabaseEngineError: When it is unknown, not installed or stopped.
        """
        manager = self.manager(engine)
        if manager.instance is not None and not manager.is_running():
            raise DatabaseEngineError(
                f"The container {manager.instance.container} is not running",
                details=f"Start it with: noust db start {manager.ENGINE_NAME}",
            )
        if not manager.is_installed():
            raise DatabaseEngineError(
                f"{manager.DISPLAY_NAME} is not installed",
                details=f"Install it with: noust db install {manager.ENGINE_NAME}",
            )
        if not manager.is_running():
            raise DatabaseEngineError(
                f"{manager.DISPLAY_NAME} is not running",
                details=f"Start it with: noust db start {manager.ENGINE_NAME}",
            )
        return manager

    def all_managers(self) -> list[BaseDatabaseManager]:
        """
        Every engine Noust manages, one manager each.

        Returns:
            The managers: the host's engines in registry order, then the
            database containers by key.
        """
        return [*self.host_managers(), *self.instance_managers()]

    def host_managers(self) -> list[BaseDatabaseManager]:
        """
        One manager per engine of the host.

        Returns:
            The managers, in registry order.
        """
        names = self._engine_names() if self._engine_names else DatabaseRegistry.list_engines()
        managers = [
            self._resolve(name) if self._resolve else get_db_manager(name) for name in names
        ]
        return [manager for manager in managers if manager is not None]

    def _app(self, domain: str) -> App:
        """
        Find an application by domain.

        Args:
            domain: The application's domain.

        Returns:
            Its store row.

        Raises:
            NoustError: When there is no such application.
        """
        app = self.store.get_app(validate_domain(domain))
        if app is None or app.id is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        return app

    def _domain_of(self, app_id: int | None) -> str | None:
        """
        Name an application by its id.

        Args:
            app_id: A store id.

        Returns:
            Its domain, or None.
        """
        if app_id is None:
            return None
        app = self.store.get_app_by_id(app_id)
        return app.domain if app is not None else None

    def _password(self, engine: str, username: str) -> str | None:
        """
        Read the password Noust keeps for an account.

        Args:
            engine: Canonical engine name.
            username: The account.

        Returns:
            The password, or None when Noust does not know it.
        """
        return self.secrets.read(_password_secret(engine, username))

    # ------------------------------------------------------------- engines

    def engines(self) -> list[dict[str, Any]]:
        """
        Describe every engine: installed, running, version, port, support.

        Returns:
            One dictionary per engine, as
            :meth:`~noust.managers.database.base.BaseDatabaseManager.get_status`
            renders it, with the port the server really listens on.
        """
        described: list[dict[str, Any]] = []
        for manager in self.all_managers():
            status = manager.get_status()
            if status.get("running"):
                status["port"] = manager.server_port()
            # A container signs Noust in with what its own environment holds.
            status["stored_account"] = (
                manager.instance is None and manager.ENGINE_NAME in CREDENTIAL_ENGINES
            )
            described.append(status)
        return described

    # ----------------------------------------------------------- databases

    def list_databases(self, engine: str | None = None) -> list[DatabaseView]:
        """
        List the databases of every running engine, joined with the store.

        Args:
            engine: Only this engine.

        Returns:
            The databases, engine by engine; see :meth:`listing` for the
            engines that could not be read.
        """
        return self.listing(engine).databases

    def listing(self, engine: str | None = None) -> DatabaseListing:
        """
        List the databases of every running engine, and say which failed.

        A database the store tracks but the engine no longer has is listed
        too, marked ``missing``: it is why its application's next backup
        would fail. One unreachable engine does not hide the others, and it
        is not silent either: it comes back as a problem with the engine's
        own message, and its tracked databases as ``unverified`` rather than
        missing.

        Args:
            engine: Only this engine.

        Returns:
            The databases, engine by engine, and the problems.
        """
        problems: list[ListingProblem] = []
        if engine:
            managers = [self.manager(engine)]
        else:
            managers = [*self.host_managers(), *self.instance_managers(problems)]
        views: list[DatabaseView] = []
        for manager in managers:
            if not manager.is_installed() or not manager.is_running():
                continue
            try:
                entries = manager.list_databases()
            except DatabaseError as exc:
                self.logger.warning(f"Could not list {manager.DISPLAY_NAME} databases: {exc}")
                problems.append(
                    ListingProblem(
                        engine=manager.ENGINE_NAME,
                        display_name=manager.DISPLAY_NAME,
                        message=exc.message,
                        hint=exc.details,
                        output=exc.output or "",
                        access=isinstance(exc, DatabaseAccessError),
                        kind="host" if manager.instance is None else "container",
                    )
                )
                views.extend(self._views(manager, [], readable=False))
                continue
            views.extend(self._views(manager, entries))
        self._mark_detected(views, self.detected(managers))
        return DatabaseListing(views, problems)

    # ------------------------------------------------------------- detected links

    def _endpoints(self, managers: list[BaseDatabaseManager]) -> list[Endpoint]:
        """
        Say where each running engine answers, to resolve references against.

        Args:
            managers: The engines.

        Returns:
            One endpoint per running engine: the host's on its port, a
            container on the ports it publishes and by its Compose service.
        """
        endpoints: list[Endpoint] = []
        for manager in managers:
            instance = manager.instance
            if instance is None:
                if not manager.is_installed() or not manager.is_running():
                    continue
                try:
                    port = manager.server_port()
                except DatabaseError as exc:
                    self.logger.warning(f"Could not read {manager.DISPLAY_NAME}'s port: {exc}")
                    continue
                endpoints.append(
                    Endpoint(manager.ENGINE_NAME, manager.engine_type, frozenset({port}))
                )
                continue
            endpoints.append(
                Endpoint(
                    manager.ENGINE_NAME,
                    manager.engine_type,
                    frozenset(port.host_port for port in instance.published),
                    project=instance.project,
                    service=instance.service,
                )
            )
        return endpoints

    def detected(self, managers: list[BaseDatabaseManager] | None = None) -> list[Detected]:
        """
        Find the databases every application's environment names.

        Args:
            managers: The engines to resolve against; every engine when
                omitted.

        Returns:
            One entry per reference, resolved to an engine key or not.
        """
        endpoints = self._endpoints(managers if managers is not None else self.all_managers())
        entries: list[AppReferences] = []
        for app in self.store.list_apps():
            references = references_in(read_environment(app))
            if references:
                project = app.compose_project or domain_to_app_name(app.domain)
                entries.append(AppReferences(app=app, references=references, project=project))
        return detect(entries, endpoints)

    @staticmethod
    def _mark_detected(views: list[DatabaseView], found: list[Detected]) -> None:
        """
        Name, on each database, the applications that use it without a link.

        Args:
            views: The databases.
            found: What the environments name.
        """
        users: dict[tuple[str, str], set[str]] = {}
        for entry in found:
            database = entry.reference.database or (
                "0" if entry.reference.engine == "redis" else None
            )
            if entry.engine is None or database is None:
                continue
            users.setdefault((entry.engine, database), set()).add(entry.domain)
        for view in views:
            domains = users.get((view.engine, view.name), set()) - set(view.apps)
            view.detected_apps = sorted(domains)

    def record_detected_link(self, engine: str, name: str, domain: str) -> LinkView:
        """
        Record, as a link, a use Noust found in an application's environment.

        Nothing in the application changes: its ``.env`` already names the
        database, which is why the use was found. The link makes it count as
        the application's for backups and the console.

        An application's environment is written by whoever deploys it, so
        what it names is a claim, not a fact. Three limits keep that claim
        from reaching another application's data:

        - a database another application owns cannot be claimed this way;
        - an existing link is never overwritten, so a link made by
          ``noust db link`` keeps its account and variable;
        - the link is recorded with no account. A password rotation writes
          the new password only into links that carry the rotated account,
          so naming someone else's account in a ``.env`` never earns its
          next password.

        Args:
            engine: The engine key.
            name: The database.
            domain: The application.

        Returns:
            The recorded link.

        Raises:
            ValidationError: When the application's environment does not
                name that database, another application owns it, or the
                application is already linked to it.
        """
        manager = self.running(engine)
        app = self._app(domain)
        match = next(
            (
                entry
                for entry in self.detected([manager])
                if entry.domain == app.domain
                and entry.engine == manager.ENGINE_NAME
                and (
                    entry.reference.database or ("0" if entry.reference.engine == "redis" else None)
                )
                == name
            ),
            None,
        )
        if match is None:
            raise ValidationError(
                f"{app.domain}'s environment does not name {name} on {manager.DISPLAY_NAME}",
                details="Only a use Noust found can be recorded this way; 'noust db link' "
                "writes a new connection string instead.",
            )
        if app.id is None:
            raise ValidationError(f"{app.domain} is not in the store")
        row = self.store.get_database(name, manager.ENGINE_NAME)
        stack_app = manager.instance.app if manager.instance is not None else None
        owner = (self._domain_of(row.app_id) if row is not None else None) or stack_app
        if owner is not None and owner != app.domain:
            raise ValidationError(
                f"{name} on {manager.DISPLAY_NAME} belongs to {owner}, not to {app.domain}",
                details=(
                    f"A database another application owns is not recorded from what "
                    f"{app.domain}'s environment says. If {app.domain} really is meant to "
                    f"use it, link it explicitly: noust db link {app.domain} {name} "
                    f"--engine {manager.ENGINE_NAME}"
                ),
            )
        if self.records.link(app.id, manager.ENGINE_NAME, name) is not None:
            raise ValidationError(
                f"{app.domain} is already linked to {name} on {manager.DISPLAY_NAME}",
                details=f"See the link with: noust db links {app.domain}",
            )
        # No account: the detected username is whatever the .env says, and a
        # link's account is what a rotation writes the new password for.
        saved = self.records.save_link(
            DatabaseLink(
                app_id=app.id,
                engine=manager.ENGINE_NAME,
                db_name=name,
                username=None,
                env_var=match.reference.variable,
            )
        )
        self.audit(
            "db.link.record",
            f"{manager.ENGINE_NAME}/{name}",
            app=app.domain,
            variable=match.reference.variable,
        )
        return self._link_views([saved])[0]

    def _views(
        self, manager: BaseDatabaseManager, entries: list[Any], *, readable: bool = True
    ) -> list[DatabaseView]:
        """
        Join one engine's databases with the store, the links and the dumps.

        Args:
            manager: The engine.
            entries: What it listed.
            readable: Whether the engine could be read at all; when not, the
                tracked databases are unverified rather than missing.

        Returns:
            The views, the missing tracked ones last.
        """
        engine = manager.ENGINE_NAME
        rows = {row.name: row for row in self.store.list_databases(engine=engine)}
        linked: dict[str, list[str]] = {}
        for link in self.records.links(engine=engine):
            domain = self._domain_of(link.app_id)
            if domain:
                linked.setdefault(link.db_name, []).append(domain)
        newest: dict[str, str] = {}
        for backup in manager.list_backups():
            newest.setdefault(backup.database, backup.created.isoformat())
        version = manager.get_version()

        views: list[DatabaseView] = []
        seen: set[str] = set()
        # A container's databases belong to the application its Compose project
        # is, unless the store records another owner.
        stack_app = manager.instance.app if manager.instance is not None else None
        for info in entries:
            seen.add(info.name)
            row = rows.get(info.name)
            owner_app = (self._domain_of(row.app_id) if row else None) or stack_app
            apps = sorted({*linked.get(info.name, []), *([owner_app] if owner_app else [])})
            views.append(
                DatabaseView(
                    name=info.name,
                    engine=engine,
                    size=info.size,
                    tables=info.tables,
                    keys=info.keys,
                    owner=info.owner,
                    encoding=info.encoding,
                    tracked=row is not None,
                    app=owner_app,
                    apps=apps,
                    username=row.username if row else None,
                    engine_version=version,
                    last_backup=newest.get(info.name) or newest.get("all"),
                )
            )
        for name, row in rows.items():
            if name in seen:
                continue
            owner_app = self._domain_of(row.app_id)
            views.append(
                DatabaseView(
                    name=name,
                    engine=engine,
                    tracked=True,
                    missing=readable,
                    unverified=not readable,
                    app=owner_app,
                    apps=sorted({*linked.get(name, []), *([owner_app] if owner_app else [])}),
                    username=row.username,
                    engine_version=version,
                    last_backup=newest.get(name),
                )
            )
        return views

    def get(self, engine: str, name: str) -> DatabaseView:
        """
        Describe one database.

        Args:
            engine: The engine.
            name: The database.

        Returns:
            The view.

        Raises:
            DatabaseNotFoundError: When the engine has no such database.
        """
        manager = self.running(engine)
        name = manager.validate_database_name(name)
        info = manager.get_database_info(name)
        return self._views(manager, [info])[0]

    def overview(self, engine: str, name: str) -> dict[str, Any]:
        """
        Everything the database page's Overview shows, in one call.

        Args:
            engine: The engine.
            name: The database.

        Returns:
            ``database`` (the view), ``display_name``, ``port``, ``service``,
            ``capabilities``, ``support`` (end-of-life notice), ``warnings``,
            ``access`` (who can reach it), ``links`` and ``backups`` (count).

        Raises:
            DatabaseNotFoundError: When the engine has no such database.
        """
        manager = self.running(engine)
        view = self.get(engine, name)
        try:
            access = [entry.to_dict() for entry in self.access(engine, view.name)]
        except DatabaseError as exc:
            self.logger.warning(f"Could not list who can reach {view.name}: {exc}")
            access = []
        return {
            "database": view.to_dict(),
            "display_name": manager.DISPLAY_NAME,
            "port": manager.server_port(),
            "service": manager.service_unit(),
            "capabilities": sorted(manager.CAPABILITIES),
            "support": manager.support(view.engine_version).to_dict(),
            "warnings": manager.warnings(),
            "access": access,
            "links": [
                link.to_dict()
                for link in self._link_views(
                    self.records.links(engine=manager.ENGINE_NAME, db_name=view.name)
                )
            ],
            "backups": len(manager.list_backups(database=view.name)),
        }

    def create(
        self,
        engine: str,
        name: str,
        *,
        owner: str | None = None,
        encoding: str | None = None,
        domain: str | None = None,
    ) -> DatabaseView:
        """
        Create a database and record it in the store.

        Args:
            engine: The engine.
            name: The database.
            owner: The account that owns it (PostgreSQL's OWNER, MySQL's ALL).
            encoding: Character set or encoding.
            domain: The application it belongs to, so its backups include it.

        Returns:
            The new database.

        Raises:
            DatabaseExistsError: When it already exists.
            DatabaseError: When the engine refuses.
        """
        manager = self.running(engine)
        name = manager.validate_database_name(name)
        if owner:
            manager.validate_user_name(owner)
        app = self._app(domain) if domain else None
        target = f"{manager.ENGINE_NAME}/{name}"
        try:
            info = manager.create_database(name, owner=owner, encoding=encoding)
        except DatabaseError:
            self.audit("db.create", target, "failure")
            raise

        row = self.store.get_database(name, manager.ENGINE_NAME)
        record = Database(
            id=row.id if row else None,
            app_id=app.id if app else (row.app_id if row else None),
            name=name,
            engine=manager.ENGINE_NAME,
            host="localhost",
            port=manager.server_port(),
            username=owner,
            encoding=encoding,
        )
        if row is None:
            self.store.create_database(record)
        else:
            record.created_at = row.created_at
            self.store.update_database(record)
        self.audit("db.create", target, owner=owner, app=domain)
        return self._views(manager, [info])[0]

    def drop(
        self,
        engine: str,
        name: str,
        *,
        force: bool = False,
        keep_backup: bool = True,
        unlink: bool = False,
    ) -> DropOutcome:
        """
        Drop a database, keeping a last dump and leaving nothing behind.

        Refused while the database is linked to an application or belongs to
        one, unless ``unlink`` says to remove its variables from them too:
        dropping it from under a running application is not something to do
        by accident. The last dump is taken first unless ``keep_backup`` is
        off. The store row, the links and the read-only console's account
        (cluster-wide in PostgreSQL and MySQL) go with it.

        Args:
            engine: The engine.
            name: The database.
            force: Disconnect open sessions first (PostgreSQL), and accept a
                database that is already gone.
            keep_backup: Dump it before dropping it.
            unlink: Remove its variables from the applications that use it.

        Returns:
            What was done.

        Raises:
            DatabaseError: When it is linked and ``unlink`` is off, the dump
                fails (nothing is dropped then), or the engine refuses.
        """
        manager = self.running(engine)
        name = manager.validate_database_name(name)
        engine_name = manager.ENGINE_NAME
        target = f"{engine_name}/{name}"
        row = self.store.get_database(name, engine_name)
        links = self.records.links(engine=engine_name, db_name=name)
        users = sorted(
            {
                domain
                for domain in [
                    *(self._domain_of(link.app_id) for link in links),
                    self._domain_of(row.app_id) if row else None,
                ]
                if domain
            }
        )
        if users and not unlink:
            self.audit("db.drop", target, "denied", reason="linked", apps=",".join(users))
            raise DatabaseError(
                f"The database '{name}' is used by {', '.join(users)}",
                details=(
                    "Unlink it from them first, or drop it with --unlink (the API's "
                    "unlink=true), which removes its variables from their environment."
                ),
            )

        safety: str | None = None
        if keep_backup and manager.database_exists(name):
            self.logger.substep(f"Taking a last dump of {name}")
            safety = str(manager.backup(name).path)

        for link in links:
            app = self.store.get_app_by_id(link.app_id)
            if app is not None:
                self._remove_link_variables(app, link, restart=False)

        try:
            manager.drop_database(name, force=force)
        except DatabaseError:
            self.audit("db.drop", target, "failure", safety_copy=safety)
            raise
        self.store.delete_database(name, engine_name)
        self.records.delete_links_to(engine_name, name)
        manager.drop_read_only_account(name)
        self.audit("db.drop", target, safety_copy=safety, unlinked=",".join(users) or None)
        return DropOutcome(engine=engine_name, database=name, safety_copy=safety, unlinked=users)

    def set_credentials(self, engine: str, user: str | None, password: str | None) -> None:
        """
        Store the account Noust signs in to an engine with, once it works.

        The account is tried first, by listing the engine's databases through
        the code that will use it: a wrong password is refused with the
        engine's own message and nothing is saved.

        Args:
            engine: The engine.
            user: The administrative user; Redis has none.
            password: Its password.

        Raises:
            ValidationError: For an engine that does not sign in with a stored
                account, or when nothing was given.
            DatabaseAccessError: When the engine refuses the account.
            ConfigError: When the configuration cannot be written.
        """
        manager = self.running(engine)
        name = manager.ENGINE_NAME
        if name not in CREDENTIAL_ENGINES:
            raise ValidationError(
                f"{manager.DISPLAY_NAME} does not sign in with a stored account",
                details=manager.access_hint(),
            )
        if not user and not password:
            raise ValidationError("Give a user, a password or both", field="password")
        candidate = self.manager(name)
        # The overlay answers get() like the configuration, which is all a manager reads.
        candidate.config = cast(Config, _CredentialOverlay(manager.config, name, user, password))
        try:
            candidate.list_databases()
        except DatabaseAccessError as exc:
            self.audit("db.credentials.set", name, outcome="failure", user=user)
            raise DatabaseAccessError(
                f"{manager.DISPLAY_NAME} refused that account",
                details="Check the user and the password; nothing was saved.",
                output=exc.output,
            ) from exc
        config = Config()
        if user:
            config.set(f"databases.credentials.{name}.user", user)
        if password:
            config.set(f"databases.credentials.{name}.password", password)
        if not config.save():
            raise ConfigError(
                "The credentials work, but the configuration could not be written",
                details="Check that the configuration file under /etc/noust is writable by root.",
            )
        self.audit("db.credentials.set", name, user=user)

    def adopt(self, engine: str | None = None) -> list[str]:
        """
        Record in the store every database the engines hold and it does not.

        Forge calls it "Sync databases". A database created outside Noust
        becomes one it tracks: listed as Noust's, linkable, and backed up
        with the application it is later linked to.

        Args:
            engine: Only this engine.

        Returns:
            The databases adopted, as ``engine/name``.
        """
        adopted: list[str] = []
        for view in self.list_databases(engine):
            if view.tracked or view.missing:
                continue
            manager = self.manager(view.engine)
            self.store.create_database(
                Database(
                    name=view.name,
                    engine=view.engine,
                    host="localhost",
                    port=manager.server_port(),
                    encoding=view.encoding,
                )
            )
            adopted.append(f"{view.engine}/{view.name}")
        if adopted:
            self.audit("db.adopt", engine or "all", databases=",".join(adopted))
        return adopted

    def forget(self, engine: str, name: str) -> bool:
        """
        Forget a database the store tracks and the engine no longer has.

        Args:
            engine: The engine.
            name: The database.

        Returns:
            Whether a row was removed.

        A database is gone when its engine says so, or when Docker answers
        and no longer runs the container it was in. Anything that cannot be
        asked refuses: the row and its links are what an application's next
        backup goes by.

        Raises:
            DatabaseError: When the database still exists (drop it instead),
                or its engine cannot be asked whether it does.
        """
        key, base = self.store_key(engine)
        name = base.validate_database_name(name)
        if not self._container_gone(key):
            manager = self.running(engine)
            if manager.database_exists(name):
                raise DatabaseError(
                    f"The database '{name}' still exists",
                    details="Drop it instead; forgetting is for a database that is gone.",
                )
        removed = self.store.delete_database(name, key)
        self.records.delete_links_to(key, name)
        self.audit("db.forget", f"{key}/{name}")
        return removed

    def fix_owner(
        self, engine: str, name: str, *, owner: str | None = None, apply: bool = False
    ) -> Any:
        """
        Show, or apply, giving a PostgreSQL database to its application's role.

        Args:
            engine: The engine; PostgreSQL only.
            name: The database.
            owner: The role to give it to; the account the store records for
                it (the one Noust provisioned) by default.
            apply: Run the statements instead of only showing them.

        Returns:
            The :class:`~noust.managers.database.postgres.OwnerPlan`.

        Raises:
            DatabaseError: When the engine is not PostgreSQL, or no owner is
                given and the store records none.
        """
        manager = self.running(engine)
        name = manager.validate_database_name(name)
        fix = getattr(manager, "fix_owner", None)
        if fix is None:
            raise DatabaseError(
                f"{manager.DISPLAY_NAME} has no database owner to fix",
                details="fix-owner is for PostgreSQL, where the owner decides who may migrate.",
            )
        if owner is None:
            row = self.store.get_database(name, manager.ENGINE_NAME)
            owner = row.username if row else None
        if not owner:
            raise DatabaseError(
                f"Noust has no application role recorded for '{name}'",
                details="Name the role that must own it with --owner.",
            )
        plan = fix(name, owner, apply=apply)
        if apply:
            self.audit(
                "db.fix_owner",
                f"{manager.ENGINE_NAME}/{name}",
                previous_owner=plan.current_owner,
                owner=owner,
                objects=len(plan.objects),
            )
        return plan

    # --------------------------------------------------------------- users

    def _guard_account(self, manager: BaseDatabaseManager, username: str, action: str) -> None:
        """
        Refuse to change an account that belongs to the engine or to Noust.

        Args:
            manager: The engine.
            username: The account.
            action: What was asked, for the message.

        Raises:
            DatabaseUserError: When the account is internal.
        """
        if manager.is_internal_user(username):
            self.audit(
                _GUARD_EVENTS.get(action, "db.user.profile"),
                f"{manager.ENGINE_NAME}/{username}",
                "denied",
                reason="internal",
            )
            raise DatabaseUserError(
                f"'{username}' is managed by {manager.DISPLAY_NAME} or by Noust itself",
                details=(
                    "The engine's own accounts and the read-only console's wasm_ro_ accounts "
                    "are never changed through Noust."
                ),
            )

    def list_users(self, engine: str) -> list[UserInfo]:
        """
        List an engine's accounts, internal ones marked.

        Args:
            engine: The engine.

        Returns:
            The accounts; ``extra["internal"]`` says which belong to the
            engine or to Noust.
        """
        manager = self.running(engine)
        users = manager.list_users()
        for user in users:
            user.extra["internal"] = manager.is_internal_user(user.username)
        return users

    def create_user(
        self,
        engine: str,
        username: str,
        *,
        password: str | None = None,
        host: str = "localhost",
        database: str | None = None,
        profile: str | None = None,
    ) -> tuple[UserInfo, str]:
        """
        Create an account, optionally with a profile on one database.

        Its password is kept in the secret store, so it can later be linked
        to an application or shown again with sudo mode.

        Args:
            engine: The engine.
            username: The account.
            password: Its password; generated when omitted.
            host: Host restriction (MySQL).
            database: A database to give it access to.
            profile: The profile on that database; the engine's full
                privileges when a database is given without one.

        Returns:
            The account and its password, which the caller shows once.

        Raises:
            DatabaseUserError: When the name is reserved, the profile is
                unknown or the engine refuses.
        """
        manager = self.running(engine)
        username = manager.validate_user_name(username)
        if username.startswith(READ_ONLY_ACCOUNT_PREFIX):
            raise DatabaseUserError(
                f"Account names starting with {READ_ONLY_ACCOUNT_PREFIX} are Noust's own",
                details="They belong to the read-only console. Pick another name.",
            )
        if profile is not None and profile not in PROFILES:
            raise DatabaseUserError(
                f"Unknown access profile: {profile!r}",
                details=f"Use one of: {', '.join(PROFILES)}.",
            )
        if database is not None:
            database = manager.validate_database_name(database)
        target = f"{manager.ENGINE_NAME}/{username}"
        user, secret = manager.create_user(
            username=username, password=password, host=host, database=database
        )
        self.secrets.write(_password_secret(manager.ENGINE_NAME, username), secret)
        if database:
            try:
                if profile:
                    manager.apply_profile(username, database, profile, host=host)
                else:
                    manager.grant_privileges(username, database, host=host)
            except DatabaseError as exc:
                # The account exists either way; the access is fixed with
                # `noust db access`, not by rolling the account back.
                self.logger.warning(f"Created {username}, but could not give it access: {exc}")
                self.audit("db.user.create", target, "failure", database=database)
                raise
        self.records.save_account(
            manager.ENGINE_NAME,
            username,
            host,
            db_name=database,
            profile=profile,
            password_changed=True,
        )
        self.audit("db.user.create", target, database=database, profile=profile)
        return user, secret

    def drop_user(self, engine: str, username: str, *, host: str = "localhost") -> None:
        """
        Drop an account, refusing one an application signs in as.

        Args:
            engine: The engine.
            username: The account.
            host: Host restriction (MySQL).

        Raises:
            DatabaseUserError: When it is internal, an application uses it,
                or the engine refuses.
        """
        manager = self.running(engine)
        username = manager.validate_user_name(username)
        engine_name = manager.ENGINE_NAME
        self._guard_account(manager, username, "drop")
        users = sorted(
            {
                domain
                for domain in [
                    *(
                        self._domain_of(link.app_id)
                        for link in self.records.links(engine=engine_name, username=username)
                    ),
                    *(
                        self._domain_of(row.app_id)
                        for row in self.store.list_databases(engine=engine_name)
                        if row.username == username
                    ),
                ]
                if domain
            }
        )
        if users:
            self.audit("db.user.drop", f"{engine_name}/{username}", "denied", reason="linked")
            raise DatabaseUserError(
                f"'{username}' is the account {', '.join(users)} signs in as",
                details="Unlink its databases from those applications first.",
            )
        manager.drop_user(username, host=host)
        self.secrets.delete(_password_secret(engine_name, username))
        self.secrets.delete(_owner_secret(engine_name, username))
        self.records.delete_account(engine_name, username, host)
        self.audit("db.user.drop", f"{engine_name}/{username}", host=host)

    def grant(
        self,
        engine: str,
        username: str,
        database: str,
        *,
        privileges: list[str] | None = None,
        host: str = "localhost",
    ) -> None:
        """
        Grant whitelisted privileges on a database.

        Args:
            engine: The engine.
            username: The account.
            database: The database.
            privileges: The privileges; the engine's full set when omitted.
            host: Host restriction (MySQL).

        Raises:
            DatabaseUserError: When the account is internal, a privilege is
                not whitelisted, or the engine refuses.
        """
        manager = self.running(engine)
        username = manager.validate_user_name(username)
        database = manager.validate_database_name(database)
        self._guard_account(manager, username, "grant")
        manager.grant_privileges(username, database, privileges=privileges, host=host)
        self.audit(
            "db.grant",
            f"{manager.ENGINE_NAME}/{username}",
            database=database,
            privileges=",".join(privileges) if privileges else "default",
        )

    def revoke(
        self,
        engine: str,
        username: str,
        database: str,
        *,
        privileges: list[str] | None = None,
        host: str = "localhost",
    ) -> None:
        """
        Revoke whitelisted privileges on a database.

        Args:
            engine: The engine.
            username: The account.
            database: The database.
            privileges: The privileges; the engine's full set when omitted.
            host: Host restriction (MySQL).

        Raises:
            DatabaseUserError: When the account is internal, a privilege is
                not whitelisted, or the engine refuses.
        """
        manager = self.running(engine)
        username = manager.validate_user_name(username)
        database = manager.validate_database_name(database)
        self._guard_account(manager, username, "revoke")
        manager.revoke_privileges(username, database, privileges=privileges, host=host)
        self.audit(
            "db.revoke",
            f"{manager.ENGINE_NAME}/{username}",
            database=database,
            privileges=",".join(privileges) if privileges else "default",
        )

    def access(self, engine: str, database: str) -> list[AccessView]:
        """
        List who can reach a database, with profiles and what Noust knows.

        Args:
            engine: The engine.
            database: The database.

        Returns:
            One view per account.
        """
        manager = self.running(engine)
        database = manager.validate_database_name(database)
        engine_name = manager.ENGINE_NAME
        accounts = {(a.username, a.host): a for a in self.records.accounts(engine_name)}
        by_user: dict[str, list[str]] = {}
        for link in self.records.links(engine=engine_name, db_name=database):
            domain = self._domain_of(link.app_id)
            if domain and link.username:
                by_user.setdefault(link.username, []).append(domain)
        views: list[AccessView] = []
        entries: list[AccessEntry] = manager.list_access(database)
        for entry in entries:
            account = accounts.get((entry.username, entry.host))
            views.append(
                AccessView(
                    username=entry.username,
                    host=entry.host,
                    profile=entry.profile,
                    privileges=list(entry.privileges),
                    internal=entry.internal,
                    managed=self._password(engine_name, entry.username) is not None,
                    apps=sorted(by_user.get(entry.username, [])),
                    password_changed_at=account.password_changed_at if account else None,
                )
            )
        return views

    def set_profile(
        self, engine: str, database: str, username: str, profile: str, *, host: str = "localhost"
    ) -> None:
        """
        Give an account one access profile on a database.

        Args:
            engine: The engine.
            database: The database.
            username: The account.
            profile: ``owner``, ``read_write`` or ``read_only``.
            host: Host restriction (MySQL).

        Raises:
            DatabaseUserError: When the account is internal, the profile is
                unknown, or the engine refuses.
        """
        manager = self.running(engine)
        username = manager.validate_user_name(username)
        database = manager.validate_database_name(database)
        self._guard_account(manager, username, "profile")
        manager.apply_profile(username, database, profile, host=host)
        self.records.save_account(
            manager.ENGINE_NAME, username, host, db_name=database, profile=profile
        )
        self.audit(
            "db.user.profile",
            f"{manager.ENGINE_NAME}/{username}",
            database=database,
            profile=profile,
        )

    def reveal_password(self, engine: str, username: str) -> str:
        """
        Read the password Noust keeps for an account, for a sudo-mode "Show".

        Args:
            engine: The engine.
            username: The account.

        Returns:
            The password.

        Raises:
            DatabaseUserError: When Noust does not know it.
        """
        manager = self.manager(engine)
        username = manager.validate_user_name(username)
        password = self._password(manager.ENGINE_NAME, username)
        if password is None:
            raise DatabaseUserError(
                f"Noust does not know the password of '{username}'",
                details="Rotate it to set one Noust keeps: noust db user-password.",
            )
        self.audit("db.user.password.show", f"{manager.ENGINE_NAME}/{username}")
        return password

    def rotate_password(
        self,
        engine: str,
        username: str,
        *,
        host: str = "localhost",
        propagate: bool = True,
        password: str | None = None,
        first_password: bool = False,
    ) -> RotationOutcome:
        """
        Give an account a new password and every application that uses it.

        The engine is changed first, then each application that signs in as
        the account gets the new value in its variables and restarts behind
        its health gate. When one does not come back, everything is undone:
        the old password goes back on the engine, and every application
        changed so far gets its previous variables back and restarts on them.
        There is a restart window: without two valid passwords at once, an
        application reconnecting between the engine's change and its own
        restart is refused.

        Applications are found by their links, and, for a database
        provisioned before 3.1 (recipes, monorepos), by the variables of the
        application it belongs to that carry the old password.

        Args:
            engine: The engine.
            username: The account; ``default`` for Redis's ``requirepass``.
            host: Host restriction (MySQL).
            propagate: Rewrite and restart the applications that use it.
            password: The new password; generated when omitted.
            first_password: Confirms giving a Redis instance that has no
                ``requirepass`` its first one. Every client that connects
                without a password - an application with no link among them,
                which Noust cannot find - starts getting ``NOAUTH``, so it is
                never done without being asked for.

        Returns:
            What was done, the new password included.

        Raises:
            DatabaseUserError: When the account is internal, the engine
                refuses, or a first Redis password was not confirmed.
            DatabaseError: When an application did not come back; everything
                is undone and the error carries the gate's evidence.
        """
        manager = self.running(engine)
        engine_name = manager.ENGINE_NAME
        if username != "default":
            username = manager.validate_user_name(username)
        self._guard_account(manager, username, "password")
        target = f"{engine_name}/{username}"
        old = self._password(engine_name, username)
        if old is None and username == "default":
            known = getattr(manager, "client_password", None)
            old = known() if callable(known) else None
            # "No password" is a state an undo can put back (requirepass ""),
            # not an unknown one; it is also the case that cuts off clients.
            probe = getattr(manager, "requirepass_set", None)
            if old is None and callable(probe) and probe() is False:
                old = ""
        if old == "" and username == "default" and not first_password:
            raise DatabaseUserError(
                f"{manager.DISPLAY_NAME} has no password yet; setting one is not a rotation",
                details=(
                    "Every client that connects without a password starts failing with "
                    "NOAUTH, and only the applications linked to it are given the new "
                    "one. Check what else connects to it, then confirm: noust db "
                    f"user-password default --engine {engine_name} --first-password"
                ),
            )
        new = password or manager.generate_password()

        apps = self._apps_using(engine_name, username, old) if propagate else []
        manager.set_user_password(username, new, host=host)
        self.secrets.write(_password_secret(engine_name, username), new)

        changed: list[tuple[App, dict[str, str]]] = []
        restarted: list[str] = []
        for app in apps:
            previous = self._current_env(app)
            self.logger.substep(f"Giving {app.domain} the new password")
            try:
                change = change_app_env(
                    app,
                    self._password_edit(app, engine_name, username, old, new),
                    store=self.store,
                    restart=True,
                    restore_on_failure=False,
                    operation="database password rotation",
                    log=self.logger,
                )
            except (NoustError, OSError) as exc:
                self._undo_rotation(
                    manager, username, host, old, [*changed, (app, previous)], target
                )
                message = exc.message if isinstance(exc, NoustError) else str(exc)
                details = exc.details if isinstance(exc, NoustError) else None
                raise DatabaseError(
                    f"The new password of '{username}' was undone: {message}",
                    details=(
                        (details or "")
                        + (
                            ""
                            if old is not None
                            else "\n\nNoust did not know the old password, so the engine "
                            "keeps the new one; the applications have their previous variables."
                        )
                    ).strip(),
                ) from exc
            if change.changed:
                changed.append((app, change.previous))
            if change.restarted:
                restarted.append(app.domain)

        self.records.save_account(engine_name, username, host, password_changed=True)
        self.audit(
            "db.user.rotate",
            target,
            apps=",".join(app.domain for app in apps) or None,
        )
        return RotationOutcome(
            engine=engine_name,
            username=username,
            password=new,
            apps=[app.domain for app in apps],
            restarted=restarted,
        )

    def _undo_rotation(
        self,
        manager: BaseDatabaseManager,
        username: str,
        host: str,
        old: str | None,
        changed: list[tuple[App, dict[str, str]]],
        target: str,
    ) -> None:
        """
        Put a rotation back: the old password first, then each application.

        The order matters: an application given back its old variables
        before the engine has the old password back would fail its gate
        again.

        Args:
            manager: The engine.
            username: The account.
            host: Its host restriction.
            old: The old password, when Noust knew it; "" for a Redis
                instance that had none.
            changed: The applications whose variables were written, with
                what they held before.
            target: The audit target.
        """
        self.logger.warning("Undoing the password rotation")
        if old is not None:
            # "" is Redis's "no password": set back as such, it lifts the
            # requirepass the rotation put on every other client.
            manager.set_user_password(username, old, host=host)
            self.secrets.write(_password_secret(manager.ENGINE_NAME, username), old)
        for app, previous in changed:
            restore_app_env(app, previous, store=self.store, restart=True, log=self.logger)
        self.audit("db.user.rotate", target, "failure")

    def _current_env(self, app: App) -> dict[str, str]:
        """
        Read an application's variables as they are now.

        Args:
            app: The application.

        Returns:
            Its variables.
        """
        from noust.deployers.helpers.app_env import read_app_env

        return read_app_env(app)

    def _apps_using(self, engine: str, username: str, old: str | None) -> list[App]:
        """
        Find the applications that sign in as an account.

        Args:
            engine: Canonical engine name.
            username: The account.
            old: Its current password, to find the applications whose
                variables carry it without a link (provisioned before 3.1).

        Returns:
            The applications, each once.
        """
        found: dict[str, App] = {}
        # Only links that carry this very account: a link recorded from what
        # an application's .env says has none, and must never be handed the
        # new password of an account it merely named.
        for link in self.records.links(engine=engine, username=username):
            app = self.store.get_app_by_id(link.app_id)
            if app is not None:
                found[app.domain] = app
        if old:
            for row in self.store.list_databases(engine=engine):
                if row.username != username or row.app_id is None:
                    continue
                app = self.store.get_app_by_id(row.app_id)
                if app is not None:
                    found.setdefault(app.domain, app)
        return list(found.values())

    def _password_edit(
        self, app: App, engine: str, username: str, old: str | None, new: str
    ) -> Callable[[dict[str, str]], dict[str, str]]:
        """
        Build the change a rotation makes to one application's variables.

        Linked variables are rebuilt from scratch; any other variable whose
        value carries the old password, raw or percent-encoded (a recipe's
        own ``DATABASE_URL``), has it replaced.

        Args:
            app: The application.
            engine: Canonical engine name.
            username: The account.
            old: The old password, if known.
            new: The new password.

        Returns:
            The edit.
        """
        from urllib.parse import quote

        links = self.records.links(app_id=app.id, engine=engine, username=username)

        def edit(values: dict[str, str]) -> dict[str, str]:
            for link in links:
                values.update(self._link_values(link, password=new))
            if old:
                for name, value in list(values.items()):
                    if old in value or quote(old, safe="") in value:
                        values[name] = value.replace(
                            quote(old, safe=""), quote(new, safe="")
                        ).replace(old, new)
            return values

        return edit

    # ---------------------------------------------------------------- links

    def _url(self, link: DatabaseLink, *, password: str | None) -> str:
        """
        Build a link's connection string.

        Args:
            link: The link.
            password: The password to put in it.

        Returns:
            The URL, on the port the engine really listens on.
        """
        manager = self.manager(link.engine)
        options: dict[str, str] | None = None
        if engine_of(link.engine) == "mongodb" and link.username:
            home = getattr(manager, "_user_database", lambda _user: None)(link.username)
            if home and home != link.db_name:
                options = {"authSource": str(home)}
        return connection_url(
            link.engine,
            database=link.db_name,
            user=link.username,
            password=password,
            host="localhost",
            port=manager.server_port(),
            options=options,
        )

    def _link_values(self, link: DatabaseLink, *, password: str | None) -> dict[str, str]:
        """
        Build the variables a link writes.

        Args:
            link: The link.
            password: The account's password.

        Returns:
            The connection string, and the ``DB_*`` ones when asked for.
        """
        values = {link.env_var: self._url(link, password=password)}
        if link.extra_vars:
            manager = self.manager(link.engine)
            values.update(
                {
                    "DB_HOST": "127.0.0.1",
                    "DB_PORT": str(manager.server_port()),
                    "DB_NAME": link.db_name,
                    "DB_USER": link.username or "",
                    "DB_PASSWORD": password or "",
                }
            )
        return values

    def _link_views(self, links: list[DatabaseLink]) -> list[LinkView]:
        """
        Describe links for the console.

        Args:
            links: The links.

        Returns:
            One view each, the URL masked.
        """
        views: list[LinkView] = []
        details: dict[str, dict[str, Any]] = {}
        for link in links:
            domain = self._domain_of(link.app_id) or ""
            engine_details = details.get(link.engine)
            if engine_details is None:
                engine_details = {"sizes": {}, "version": None, "up": False}
                try:
                    manager = self.manager(link.engine)
                    if manager.is_installed() and manager.is_running():
                        engine_details["up"] = True
                        engine_details["version"] = manager.get_version()
                        engine_details["sizes"] = {
                            info.name: info.size for info in manager.list_databases()
                        }
                except DatabaseError as exc:
                    # A container that was removed, or a Docker that does not
                    # answer, is this link unavailable, not the whole tab.
                    self.logger.warning(f"Could not reach {link.engine}: {exc}")
                details[link.engine] = engine_details
            password = self._password(link.engine, link.username) if link.username else None
            url = self._url(link, password=password) if engine_details["up"] else None
            views.append(
                LinkView(
                    domain=domain,
                    engine=link.engine,
                    database=link.db_name,
                    username=link.username,
                    env_var=link.env_var,
                    extra_vars=link.extra_vars,
                    url=masked(url, password) if url else None,
                    exists=link.db_name in engine_details["sizes"],
                    size=engine_details["sizes"].get(link.db_name),
                    engine_version=engine_details["version"],
                    created_at=link.created_at,
                )
            )
        return views

    def app_databases(self, domain: str) -> list[LinkView]:
        """
        List the databases an application uses, for its Database tab.

        A database provisioned for the application before 3.1 (a recipe's,
        a monorepo's) has no link row; it is listed from the store's
        ownership, its variable unknown.

        Args:
            domain: The application.

        Returns:
            One view per database.
        """
        app = self._app(domain)
        links = self.records.links(app_id=app.id)
        known = {(link.engine, link.db_name) for link in links}
        views = self._link_views(links)
        for row in self.store.list_databases(app_id=app.id):
            if (row.engine, row.name) in known:
                continue
            views.append(
                LinkView(
                    domain=app.domain,
                    engine=row.engine,
                    database=row.name,
                    username=row.username,
                    env_var="",
                    extra_vars=False,
                    url=None,
                    created_at=row.created_at,
                )
            )
        return views

    def reveal_url(self, domain: str, engine: str, database: str) -> str:
        """
        Build a linked database's connection string with its password.

        Args:
            domain: The application.
            engine: The engine.
            database: The database.

        Returns:
            The URL.

        Raises:
            DatabaseError: When the database is not linked to the application
                or Noust does not know the password.
        """
        app = self._app(domain)
        manager = self.manager(engine)
        link = self.records.link(app.id or 0, manager.ENGINE_NAME, database)
        if link is None:
            raise DatabaseError(
                f"'{database}' is not linked to {domain}",
                details="Link it first: noust db link.",
            )
        password = self._password(link.engine, link.username) if link.username else None
        if link.username and password is None:
            raise DatabaseUserError(
                f"Noust does not know the password of '{link.username}'",
                details="Rotate it to set one Noust keeps: noust db user-password.",
            )
        self.audit("db.connection_string", f"{link.engine}/{database}", app=domain)
        return self._url(link, password=password)

    def link(
        self,
        domain: str,
        engine: str,
        database: str,
        *,
        username: str | None = None,
        env_var: str | None = None,
        extra_vars: bool = False,
        restart: bool = True,
    ) -> LinkOutcome:
        """
        Give an application a database's connection string.

        The account is the one named, or the one Noust provisioned for the
        database, or, when there is none, a new one created for the
        application (``<app>_user``) and made the database's owner, so the
        application can run its migrations. Noust must know the account's
        password: it is read from the secret store, never asked for.

        The variable is written with the application's secret mark and the
        application restarts behind its health gate; when it does not come
        up, its previous variables are back and nothing is recorded. The
        database is recorded as the application's when it was nobody's, so
        the application's backups include it.

        Args:
            domain: The application.
            engine: The engine.
            database: The database (a slot for Redis).
            username: The account to sign in as.
            env_var: The variable; ``DATABASE_URL`` (``REDIS_URL`` for Redis)
                by default.
            extra_vars: Also write ``DB_HOST``, ``DB_PORT``, ``DB_NAME``,
                ``DB_USER`` and ``DB_PASSWORD``.
            restart: Restart the application on the new variables.

        Returns:
            What was done.

        Raises:
            DatabaseNotFoundError: When the database does not exist.
            DatabaseError: When the variable is already taken by something
                else, or the account's password is unknown.
            DeploymentError: When the application did not come up.
        """
        app = self._app(domain)
        manager = self.running(engine)
        engine_name = manager.ENGINE_NAME
        database = manager.validate_database_name(database)
        if not manager.database_exists(database):
            raise DatabaseNotFoundError(f"Database '{database}' does not exist")
        variable = env_var or default_env_var(engine_name)
        target = f"{engine_name}/{database}"

        existing = self.records.link(app.id or 0, engine_name, database)
        taken = {
            name
            for other in self.records.links(app_id=app.id)
            if (other.engine, other.db_name) != (engine_name, database)
            for name in (other.env_var, *(EXTRA_VARIABLES if other.extra_vars else ()))
        }
        wanted = {variable, *(EXTRA_VARIABLES if extra_vars else ())}
        clash = sorted(wanted & taken)
        if clash:
            raise DatabaseError(
                f"{', '.join(clash)} already carries another database's settings in {domain}",
                details="Choose another variable name, or unlink the other database first.",
            )
        current = self._current_env(app)
        owned = (
            {existing.env_var, *(EXTRA_VARIABLES if existing.extra_vars else ())}
            if existing
            else set()
        )
        foreign = sorted(name for name in wanted if name in current and name not in owned)
        if foreign:
            raise DatabaseError(
                f"{', '.join(foreign)} is already set in {domain}'s environment",
                details=(
                    "Noust does not overwrite a variable it did not write. Choose another "
                    "variable name, or remove it from the environment first."
                ),
            )

        user, password, created_user = self._link_account(app.domain, manager, database, username)
        link = DatabaseLink(
            app_id=app.id or 0,
            engine=engine_name,
            db_name=database,
            username=user,
            env_var=variable,
            extra_vars=extra_vars,
        )
        values = self._link_values(link, password=password)

        def edit(env: dict[str, str]) -> dict[str, str]:
            for name in owned - set(values):
                env.pop(name, None)
            env.update(values)
            return env

        try:
            change = change_app_env(
                app,
                edit,
                store=self.store,
                secret_names=values,
                restart=restart,
                operation="database link",
                log=self.logger,
            )
        except (DeploymentError, NoustError):
            self.audit("db.link", target, "failure", app=domain)
            raise
        self.records.save_link(link)
        self._own(app, manager, database, user)
        self.audit("db.link", target, app=domain, user=user, env_var=variable)
        return LinkOutcome(
            domain=app.domain,
            engine=engine_name,
            database=database,
            username=user,
            env_vars=sorted(values),
            restarted=change.restarted,
            created_user=created_user,
        )

    def _link_account(
        self, domain: str, manager: BaseDatabaseManager, database: str, username: str | None
    ) -> tuple[str | None, str | None, bool]:
        """
        Choose, or create, the account a link signs in as.

        Args:
            domain: The application's domain. It need not be deployed yet:
                a first deploy asks before it creates the application.
            manager: The engine.
            database: The database.
            username: The account the caller named.

        Returns:
            The account, its password and whether it was created.

        Raises:
            DatabaseUserError: When the named account's password is unknown,
                or it is internal.
        """
        engine = manager.ENGINE_NAME
        if engine_of(engine) == "redis":
            known = getattr(manager, "client_password", None)
            name = username or "default"
            password = self._password(engine, name) if name != "default" else None
            if name == "default" and callable(known):
                password = known()
            return name, password, False

        if username:
            self._guard_account(manager, username, "link")
            password = self._password(engine, username)
            if password is None:
                raise DatabaseUserError(
                    f"Noust does not know the password of '{username}'",
                    details=(
                        f"Rotate it first (noust db user-password {username} --engine "
                        f"{engine}) so Noust keeps it, or link without naming an account."
                    ),
                )
            return username, password, False

        row = self.store.get_database(database, engine)
        if row and row.username:
            password = self._password(engine, row.username)
            if password is not None:
                return row.username, password, False

        _db, user = database_identifiers(domain_to_app_name(domain), engine)
        password = self._password(engine, user)
        if manager.user_exists(user):
            if password is None:
                raise DatabaseUserError(
                    f"The account '{user}' exists and Noust does not know its password",
                    details=f"Name an account Noust knows with --user, or rotate {user}'s.",
                )
            return user, password, False
        _info, password = manager.create_user(user, database=database)
        self.secrets.write(_password_secret(engine, user), password)
        self.secrets.write(_owner_secret(engine, user), domain)
        profile = LINK_PROFILE
        try:
            manager.apply_profile(user, database, profile)
        except DatabaseUserError:
            # An engine without profiles gets its full privileges instead.
            manager.grant_privileges(user, database)
            profile = "custom"
        self.records.save_account(
            engine, user, db_name=database, profile=profile, password_changed=True
        )
        self.audit("db.user.create", f"{engine}/{user}", database=database, app=domain)
        return user, password, True

    def _own(self, app: App, manager: BaseDatabaseManager, database: str, user: str | None) -> None:
        """
        Record a linked database as the application's when it was nobody's.

        Args:
            app: The application.
            manager: The engine.
            database: The database.
            user: The account the link signs in as.
        """
        row = self.store.get_database(database, manager.ENGINE_NAME)
        if row is None:
            self.store.create_database(
                Database(
                    app_id=app.id,
                    name=database,
                    engine=manager.ENGINE_NAME,
                    host="localhost",
                    port=manager.server_port(),
                    username=user,
                )
            )
        elif row.app_id is None:
            row.app_id = app.id
            row.username = row.username or user
            self.store.update_database(row)

    def _remove_link_variables(self, app: App, link: DatabaseLink, *, restart: bool) -> bool:
        """
        Remove a link's variables from an application.

        A link recorded from detection (:meth:`record_detected_link`, no
        account) names a variable the application's own ``.env`` carries,
        which Noust never wrote: removing it would take the application's
        database away. Such a link leaves the environment alone and the
        application running; only its store row goes, which is the
        caller's.

        Args:
            app: The application.
            link: The link.
            restart: Restart it behind its gate.

        Returns:
            Whether it restarted.
        """
        if link.username is None:
            return False
        names = {link.env_var, *(EXTRA_VARIABLES if link.extra_vars else ())}

        def edit(env: dict[str, str]) -> dict[str, str]:
            for name in names:
                env.pop(name, None)
            return env

        change = change_app_env(
            app,
            edit,
            store=self.store,
            restart=restart,
            operation="database unlink",
            log=self.logger,
        )
        return change.restarted

    def unlink(
        self,
        domain: str,
        engine: str,
        database: str,
        *,
        drop: bool = False,
        restart: bool = True,
    ) -> DropOutcome | None:
        """
        Take a database away from an application, and drop it if asked.

        Its variables are removed and the application restarts behind its
        gate: one that does not come up without the database gets them back
        and nothing is unlinked. The database stops being the application's,
        or passes to another application that still uses it. A link recorded
        from detection only loses its store row: the variable is the
        application's own, so its ``.env`` is not touched and it does not
        restart.

        Args:
            domain: The application.
            engine: The engine.
            database: The database.
            drop: Drop the database too, after a last dump.
            restart: Restart the application without the variables.

        Returns:
            The drop's outcome when ``drop``, otherwise None.

        Raises:
            DatabaseError: When the database is not the application's.
            DeploymentError: When the application did not come up.
        """
        app = self._app(domain)
        # The store and the application's environment are all an unlink
        # changes: a container that is gone does not stand in its way.
        engine_name, base = self.store_key(engine)
        database = base.validate_database_name(database)
        link = self.records.link(app.id or 0, engine_name, database)
        row = self.store.get_database(database, engine_name)
        if link is None and (row is None or row.app_id != app.id):
            raise DatabaseError(
                f"'{database}' is not linked to {domain}",
                details=f"See what it uses with: noust db links {domain}",
            )
        if link is not None:
            self._remove_link_variables(app, link, restart=restart)
            self.records.delete_link(app.id or 0, engine_name, database)
        if row is not None and row.app_id == app.id:
            others = [
                other.app_id
                for other in self.records.links(engine=engine_name, db_name=database)
                if other.app_id != app.id
            ]
            row.app_id = others[0] if others else None
            self.store.update_database(row)
        self.audit("db.unlink", f"{engine_name}/{database}", app=domain, drop=drop)
        if drop:
            return self.drop(engine_name, database, force=True, keep_backup=True, unlink=True)
        return None

    def _provision(
        self, domain: str, manager: BaseDatabaseManager, name: str | None, *, app: App | None
    ) -> tuple[str, str | None, bool]:
        """
        Create a database and the account that owns it, for an application.

        PostgreSQL and MySQL go through the one provisioning implementation
        (:func:`~noust.deployers.helpers.databases.provision_database`):
        idempotent, ownership-checked, the account created first and the
        database owned by it. MongoDB gets a database and an account defined
        in it, with ``dbOwner``. Redis creates nothing: a slot is linked.

        Args:
            domain: The application's domain; it owns what is created.
            manager: The engine, installed and running.
            name: The database; derived from the application when None.
            app: The application's row, or None when its first deploy has
                not created it yet.

        Returns:
            The database, the account (None for Redis, whose link chooses
            it) and whether the database was created.

        Raises:
            DatabaseError: When the engine refuses, or the names belong to
                another application.
        """
        engine_name = manager.ENGINE_NAME
        app_name = domain_to_app_name(domain)
        if engine_of(engine_name) == "redis":
            return name or "0", None, False

        if engine_of(engine_name) in SUPPORTED_ENGINES:
            default_db, user = database_identifiers(app_name, engine_name)
            db_name = name or default_db
            created = not manager.database_exists(db_name)
            provision_database(
                engine_name,
                name=db_name,
                user=user,
                domain=domain,
                logger=self.logger,
                store=self.store,
                secret_store=self.secrets,
                manager=manager,
            )
        else:
            base = app_name.replace("-", "_")
            db_name = name or f"{base}_db"
            user = f"{base}_user"
            created = not manager.database_exists(db_name)
            if created:
                manager.create_database(db_name)
            if not manager.user_exists(user):
                _info, password = manager.create_user(user, database=db_name)
                self.secrets.write(_password_secret(engine_name, user), password)
                self.secrets.write(_owner_secret(engine_name, user), domain)
                manager.apply_profile(user, db_name, "owner")
            if app is not None:
                self._own(app, manager, db_name, user)
        self.audit("db.create", f"{engine_name}/{db_name}", app=domain, created=created)
        return db_name, user, created

    def provision_for_app(
        self,
        domain: str,
        engine: str,
        *,
        name: str | None = None,
        env_var: str | None = None,
        extra_vars: bool = False,
        restart: bool = True,
    ) -> LinkOutcome:
        """
        Create a database and an account for an application, and link them.

        See :meth:`_provision` for what each engine gets; the link then
        writes the connection string and restarts behind the gate.

        Args:
            domain: The application.
            engine: The engine.
            name: The database; derived from the application when omitted.
            env_var: The variable; see :meth:`link`.
            extra_vars: See :meth:`link`.
            restart: See :meth:`link`.

        Returns:
            What was done.

        Raises:
            DatabaseError: When the engine refuses, or the names belong to
                another application.
            DeploymentError: When the application did not come up.
        """
        app = self._app(domain)
        manager = self.running(engine)
        db_name, user, created = self._provision(app.domain, manager, name, app=app)
        outcome = self.link(
            domain,
            manager.ENGINE_NAME,
            db_name,
            username=user,
            env_var=env_var,
            extra_vars=extra_vars,
            restart=restart,
        )
        outcome.created_database = created
        return outcome

    def prepare_for_new_app(
        self,
        domain: str,
        engine: str,
        *,
        name: str | None = None,
        env_var: str | None = None,
        extra_vars: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> NewAppDatabase:
        """
        Create a database for an application its first deploy is about to create.

        What ``POST /api/apps`` and ``noust create --database`` do before the
        first build: an application that needs its database at its first
        start cannot wait for :meth:`provision_for_app`, which needs the
        application to exist. The database and its account are created and
        recorded as the domain's (so a retried deploy reuses them and the
        password Noust stored), and the variables are handed back for the
        deploy to write with the operator's. Nothing is linked yet:
        :meth:`link_new_app` does that once the deploy created the
        application, and :meth:`keep_after_failed_deploy` says what was kept
        when it did not.

        Args:
            domain: The application's domain, not deployed yet.
            engine: The engine.
            name: The database; derived from the application when omitted.
            env_var: The variable; ``DATABASE_URL`` (``REDIS_URL`` for Redis)
                by default.
            extra_vars: Also write ``DB_HOST``, ``DB_PORT``, ``DB_NAME``,
                ``DB_USER`` and ``DB_PASSWORD``.
            env: The variables the operator gave the deploy. One the database
                would write is refused, never overwritten.

        Returns:
            The database and the variables to write.

        Raises:
            DatabaseError: When the domain is already deployed, a variable is
                already given, the engine refuses, or the names belong to
                another application.
            DatabaseEngineError: When the engine is unknown, not installed or
                stopped.
        """
        domain = validate_domain(domain)
        if self.store.get_app(domain) is not None:
            raise DatabaseError(
                f"{domain} is already deployed",
                details=f"Create its database with: noust db provision {domain} --engine {engine}",
            )
        manager = self.running(engine)
        engine_name = manager.ENGINE_NAME
        variable = env_var or default_env_var(engine_name)
        wanted = {variable, *(EXTRA_VARIABLES if extra_vars else ())}
        given = sorted(wanted & set(env or {}))
        if given:
            raise DatabaseError(
                f"{', '.join(given)} is already among the variables given to {domain}",
                details="Noust does not overwrite a variable it did not write. Leave it out, "
                "or choose another variable name for the database.",
            )

        db_name, user, created = self._provision(domain, manager, name, app=None)
        database = manager.validate_database_name(db_name)
        user, password, _created_user = self._link_account(domain, manager, database, user)
        link = DatabaseLink(
            app_id=0,
            engine=engine_name,
            db_name=database,
            username=user,
            env_var=variable,
            extra_vars=extra_vars,
        )
        return NewAppDatabase(
            domain=domain,
            engine=engine_name,
            database=database,
            username=user,
            env_var=variable,
            extra_vars=extra_vars,
            created_database=created,
            values=self._link_values(link, password=password),
        )

    def link_new_app(self, prepared: NewAppDatabase) -> LinkOutcome:
        """
        Record the link of a database a first deploy was given, once it succeeded.

        The variables are already in the application's environment, written
        by the deploy before its build; this records which variable carries
        the database and makes the database the application's, so it is in
        its backups and in its Database tab. Nothing restarts.

        Args:
            prepared: What :meth:`prepare_for_new_app` answered.

        Returns:
            What was done.

        Raises:
            NoustError: When the application does not exist.
        """
        app = self._app(prepared.domain)
        manager = self.manager(prepared.engine)
        target = f"{prepared.engine}/{prepared.database}"
        self._own(app, manager, prepared.database, prepared.username)
        if prepared.env_var not in self._current_env(app):
            # A deployer that writes its own environment (a monorepo's, a
            # compose project's) did not take the variable: a link row would
            # claim a variable the application does not have.
            self.logger.warning(
                f"{prepared.env_var} is not in {prepared.domain}'s environment; link the "
                f"database with: noust db link {prepared.domain} --engine {prepared.engine} "
                f"{prepared.database}"
            )
            self.audit("db.link", target, "failure", app=prepared.domain, reason="not written")
            return LinkOutcome(
                domain=prepared.domain,
                engine=prepared.engine,
                database=prepared.database,
                username=prepared.username,
                env_vars=[],
                restarted=False,
                created_database=prepared.created_database,
            )
        self.records.save_link(
            DatabaseLink(
                app_id=app.id or 0,
                engine=prepared.engine,
                db_name=prepared.database,
                username=prepared.username,
                env_var=prepared.env_var,
                extra_vars=prepared.extra_vars,
            )
        )
        self.audit(
            "db.link", target, app=prepared.domain, user=prepared.username, env_var=prepared.env_var
        )
        return LinkOutcome(
            domain=prepared.domain,
            engine=prepared.engine,
            database=prepared.database,
            username=prepared.username,
            env_vars=sorted(prepared.values),
            restarted=False,
            created_database=prepared.created_database,
        )

    def keep_after_failed_deploy(self, prepared: NewAppDatabase, error: NoustError) -> None:
        """
        Say, in a failed first deploy's error, that its database was kept.

        A database is never dropped behind the operator's back: a retry of
        the deploy reuses it and its password, and one who does not retry is
        told how to drop it. The note is appended to ``error.details``.

        Args:
            prepared: What :meth:`prepare_for_new_app` answered.
            error: What the deploy raised; its details are extended.
        """
        engine, database, user = prepared.engine, prepared.database, prepared.username
        if engine_of(engine) == "redis":
            note = f"Redis slot {database} was not linked; nothing was created in it."
        else:
            note = (
                f"The {engine} database {database} was kept, and a new deploy of "
                f"{prepared.domain} reuses it. If you do not deploy it again, drop it with: "
                f"noust db drop {database} --engine {engine}"
            )
            if user:
                note += f", and its account with: noust db user-delete {user} --engine {engine}"
            note += "."
        error.details = f"{error.details}\n{note}" if error.details else note
        self.audit(
            "db.link",
            f"{engine}/{database}",
            "failure",
            app=prepared.domain,
            reason="first deploy failed",
            kept=True,
        )

    def plan_for_app(self, domain: str, engine: str) -> dict[str, Any]:
        """
        Say what provisioning a database for an application would write.

        What the New-application wizard's Database step shows before the
        first deploy, when the application does not exist yet: nothing is
        created or read beyond the engine's state.

        Args:
            domain: The application's domain, existing or not.
            engine: The engine.

        Returns:
            ``engine``, ``display_name``, ``installed``, ``running``,
            ``database``, ``username``, ``env_vars`` (names), ``url`` (the
            connection string with the password masked) and
            ``database_exists``.
        """
        manager = self.manager(engine)
        engine_name = manager.ENGINE_NAME
        app_name = domain_to_app_name(validate_domain(domain))
        installed = manager.is_installed()
        running = installed and manager.is_running()
        if engine_of(engine_name) == "redis":
            database, user = "0", "default"
        elif engine_of(engine_name) in SUPPORTED_ENGINES:
            database, user = database_identifiers(app_name, engine_name)
        else:
            base = app_name.replace("-", "_")
            database, user = f"{base}_db", f"{base}_user"
        port = manager.server_port() if running else manager.DEFAULT_PORT
        url = masked(
            connection_url(
                engine_name,
                database=database,
                user=user,
                password=_PLACEHOLDER,
                host="localhost",
                port=port,
            ),
            _PLACEHOLDER,
        )
        return {
            "engine": engine_name,
            "display_name": manager.DISPLAY_NAME,
            "installed": installed,
            "running": running,
            "database": database,
            "username": user,
            "env_vars": [default_env_var(engine_name)],
            "url": url,
            "database_exists": bool(running and manager.database_exists(database)),
        }

    # ---------------------------------------------------------------- dumps

    def list_dumps(
        self, engine: str | None = None, database: str | None = None
    ) -> list[BackupInfo]:
        """
        List dumps on disk.

        Args:
            engine: Only this engine's.
            database: Only this database's.

        Returns:
            The dumps, newest first within each engine.
        """
        managers = [self.manager(engine)] if engine else self.all_managers()
        dumps: list[BackupInfo] = []
        for manager in managers:
            name = manager.validate_database_name(database) if database else None
            try:
                dumps.extend(manager.list_backups(database=name))
            except (DatabaseError, OSError) as exc:
                self.logger.warning(f"Could not list {manager.DISPLAY_NAME} dumps: {exc}")
        return dumps

    def dump_path(self, engine: str, dump_name: str) -> Path:
        """
        Resolve a dump named by its file name inside the engine's directory.

        A full path is never accepted from a caller: it would let a restore
        read any file on the host as the database superuser.

        Args:
            engine: The engine.
            dump_name: The file name.

        Returns:
            The path.

        Raises:
            DatabaseNotFoundError: When there is no such dump (a 404).
        """
        manager = self.manager(engine)
        path: Path = resolve_within(manager.BACKUP_DIR, validate_filename(dump_name))
        if not path.is_file():
            raise DatabaseNotFoundError(
                f"Backup not found: {dump_name}",
                details="List the dumps with: noust db backups",
            )
        return path

    def dump(
        self,
        engine: str,
        database: str,
        *,
        compress: bool = True,
        dump_format: str | None = None,
        output: Path | None = None,
    ) -> BackupInfo:
        """
        Dump a database.

        Args:
            engine: The engine.
            database: The database.
            compress: gzip the dump (a PostgreSQL custom-format dump Noust
                names is compressed by pg_dump already).
            dump_format: PostgreSQL's ``custom`` (default), ``plain`` or ``tar``.
            output: Where to write it; the engine's backup directory when None.

        Returns:
            The dump.

        Raises:
            DatabaseBackupError: When the dump fails.
        """
        manager = self.running(engine)
        database = manager.validate_database_name(database)
        target = f"{manager.ENGINE_NAME}/{database}"
        kwargs: dict[str, Any] = {"format": dump_format} if dump_format else {}
        try:
            info = manager.backup(database, output_path=output, compress=compress, **kwargs)
        except DatabaseError:
            self.audit("db.backup", target, "failure")
            raise
        self.audit("db.backup", target, file=info.path.name, size=info.size)
        return info

    def restore(
        self,
        engine: str,
        database: str,
        source: Path,
        *,
        drop_existing: bool = False,
        safety_backup: bool = True,
        new_name: str | None = None,
        on_safety_copy: Callable[[Path], None] | None = None,
    ) -> RestoreOutcome:
        """
        Restore a dump into a database, or into a new one beside it.

        Replacing a database never destroys without a safety copy (see
        :meth:`~noust.managers.database.base.BaseDatabaseManager.restore`):
        it is dumped first and put back when the restore fails. Restoring
        as a new database touches nothing that exists: the new one is
        created, loaded and tracked, and the original is left alone.

        Args:
            engine: The engine.
            database: The database the dump is of, and the target unless
                ``new_name`` is given.
            source: The dump.
            drop_existing: Drop and recreate the target before loading.
            safety_backup: Take the safety copy when nothing is dropped.
            new_name: Restore into a new database of this name instead.
            on_safety_copy: Called with the safety copy before anything is
                dropped or loaded (see the manager's ``restore``).

        Returns:
            What was done.

        Raises:
            DatabaseExistsError: When ``new_name`` already exists.
            DatabaseBackupError: When the restore fails.
        """
        manager = self.running(engine)
        database = manager.validate_database_name(database)
        engine_name = manager.ENGINE_NAME
        if new_name is not None:
            new_name = manager.validate_database_name(new_name)
            if manager.database_exists(new_name):
                raise DatabaseExistsError(
                    f"Database '{new_name}' already exists",
                    details="Restoring as a new database needs a name nothing uses yet.",
                )
        target_name = new_name or database
        target = f"{engine_name}/{target_name}"
        try:
            # Beside the original, the load must reach nothing else: a dump
            # that switches database would otherwise write into the original.
            isolation = {"isolated": True} if new_name is not None else {}
            outcome = manager.restore(
                target_name,
                source,
                drop_existing=drop_existing and new_name is None,
                safety_backup=safety_backup,
                on_safety_copy=on_safety_copy,
                **isolation,
            )
        except DatabaseError:
            self.audit("db.restore", target, "failure", source=source.name)
            raise
        if new_name is not None and self.store.get_database(new_name, engine_name) is None:
            original = self.store.get_database(database, engine_name)
            self.store.create_database(
                Database(
                    name=new_name,
                    engine=engine_name,
                    host="localhost",
                    port=manager.server_port(),
                    username=original.username if original else None,
                )
            )
        self.audit(
            "db.restore",
            target,
            source=source.name,
            safety_copy=outcome.safety_copy.name if outcome.safety_copy else None,
            replaced=outcome.replaced,
        )
        return outcome

    # ------------------------------------------------------ connect, exposure

    def connection_info(
        self,
        engine: str,
        database: str,
        *,
        username: str | None = None,
        server: str | None = None,
        ssh_user: str | None = None,
        local_port: int | None = None,
    ) -> dict[str, Any]:
        """
        Everything the Connect tab shows: from the app, from a computer, exposure.

        Args:
            engine: The engine.
            database: The database.
            username: The account; the one Noust provisioned by default.
            server: The address the operator reaches this server at; the
                ``server.public_address`` setting, then the first global IPv4
                address, by default. A central passes the node's address.
            ssh_user: The account the SSH command signs in as.
            local_port: The port to open on the operator's computer.

        Returns:
            ``engine``, ``database``, ``username``, ``port``, ``listen`` (the
            engine's own setting), ``apps`` (the variables that carry it,
            URLs masked), ``tunnel`` (the SSH instructions) and ``exposed``
            (this engine's ports open to the network).
        """
        manager = self.running(engine)
        engine_name = manager.ENGINE_NAME
        database = manager.validate_database_name(database)
        row = self.store.get_database(database, engine_name)
        user = username or (row.username if row else None)
        if engine_of(engine_name) == "redis":
            user = username or "default"
        port = manager.server_port()
        password = self._password(engine_name, user) if user else None
        config = Config()
        address = server or config.get(PUBLIC_ADDRESS_SETTING) or public_address() or "<server>"
        tunnel = tunnel_instructions(
            engine_name,
            database=database,
            username=user,
            remote_port=port,
            server=str(address),
            ssh_port=ssh_port(),
            ssh_user=ssh_user or "root",
            local_port=local_port,
            masked_url="",
        )
        through = masked(
            connection_url(
                engine_name,
                database=database,
                user=user,
                password=_PLACEHOLDER if password or user else None,
                host="127.0.0.1",
                port=tunnel.local_port,
            ),
            _PLACEHOLDER,
        )
        listen = manager.listen_addresses()
        exposed = [entry.to_dict() for entry in self.engine_exposure(manager)]
        return {
            "engine": engine_name,
            "database": database,
            "username": user,
            "password_known": password is not None,
            "port": port,
            "listen": listen.to_dict() if listen else None,
            "apps": [
                view.to_dict()
                for view in self._link_views(
                    self.records.links(engine=engine_name, db_name=database)
                )
            ],
            "tunnel": {**tunnel.to_dict(), "url": through},
            "exposed": exposed,
        }

    def exposure(
        self, *, extra_ports: dict[int, str] | None = None, include_firewalled: bool = False
    ) -> list[ExposedPort]:
        """
        Find the database ports open beyond this machine.

        Args:
            extra_ports: Ports an engine is known to listen on besides the
                defaults.
            include_firewalled: Also return the Docker-published ports the
                firewall keeps the Internet out of, flagged ``firewalled``.

        Returns:
            The exposed ports; with ``include_firewalled``, the closed ones too.
        """
        return find_exposed_database_ports(
            extra_ports=extra_ports, include_firewalled=include_firewalled
        )

    def engine_exposure(self, manager: BaseDatabaseManager) -> list[ExposedPort]:
        """
        Find the ports of one engine that are open beyond this machine.

        The host's engine owns what the server itself listens on for its
        family; a container owns the ports Docker publishes for it, by its
        name or its published host port. The exposure scan names a family
        (``postgresql``), never an instance key, which is why a container's
        ports were never matched.

        Args:
            manager: The engine, bound to a container or not.

        Returns:
            Its exposed ports.
        """
        entries = self.exposure(extra_ports={manager.server_port(): manager.engine_type})
        instance = manager.instance
        if instance is None:
            return [entry for entry in entries if entry.engine == manager.engine_type]
        published = {port.host_port for port in instance.published}
        return [
            entry
            for entry in entries
            if entry.source == "docker"
            and (entry.container == instance.container or entry.port in published)
        ]

    # ------------------------------------------------- installing engines

    def install_catalog(self) -> dict[str, Any]:
        """
        Describe what the install dialog can offer on this server.

        Returns:
            The distribution, whether apt is present, and per flavour whether
            it can be installed, why not, and in which versions.
        """
        return install_catalog(self.all_managers())

    def plan_engine_install(
        self, engine: str, *, flavour: str | None = None, version: str | None = None
    ) -> EnginePlan:
        """
        Decide what installing an engine means, before anything is touched.

        Args:
            engine: The engine name as typed (``mariadb`` and ``valkey`` name
                a flavour).
            flavour: A flavour named explicitly.
            version: A version asked for.

        Returns:
            The decision.

        Raises:
            DatabaseEngineError: When the engine is unknown.
            DatabaseExistsError: When the engine's other flavour is installed.
            ValidationError: When the flavour or version cannot be had here.
        """
        return plan_engine_install(self.manager(engine), engine, flavour=flavour, version=version)

    def install_engine(
        self, engine: str, *, flavour: str | None = None, version: str | None = None
    ) -> InstallOutcome:
        """
        Install an engine in the flavour and version asked for.

        Without a flavour or a version it does what 3.2 did: the
        distribution's package, MariaDB before MySQL and Redis before Valkey.

        Args:
            engine: The engine name as typed.
            flavour: A flavour named explicitly.
            version: A version asked for.

        Returns:
            What was done; nothing when it was already installed.

        Raises:
            DatabaseEngineError: When apt, the repository or the unit fails.
            DatabaseExistsError: When the engine's other flavour is installed.
            ValidationError: When the flavour or version cannot be had here.
        """
        return install_engine(self.plan_engine_install(engine, flavour=flavour, version=version))

    # ------------------------------------------------------ engine settings

    def _host_engine(self, engine: str) -> BaseDatabaseManager:
        """
        Resolve an engine on this host whose configuration Noust may change.

        Args:
            engine: The engine name.

        Returns:
            Its manager.

        Raises:
            ValidationError: For an engine in a container, whose image and
                compose file configure it (3.3 spec, 9.3).
            DatabaseEngineError: When it is unknown or not installed.
        """
        if "@" in engine:
            raise ValidationError(
                "Noust does not configure an engine that runs in a container",
                details="Its image and the project's compose file configure it.",
                field="engine",
            )
        manager = self.manager(engine)
        if not manager.is_installed():
            raise DatabaseEngineError(
                f"{manager.DISPLAY_NAME} is not installed",
                details=f"Install it with: noust db install {manager.ENGINE_NAME}",
            )
        return manager

    def engine_settings(self, engine: str) -> SettingsReport:
        """
        Describe an engine's settings: current, configured and recommended.

        Args:
            engine: The engine name.

        Returns:
            The report.

        Raises:
            ValidationError: For an engine in a container.
            DatabaseEngineError: When it is unknown, not installed, or has no
                place to write settings.
        """
        return settings_for(self._host_engine(engine)).report()

    def change_engine_settings(
        self,
        engine: str,
        values: Mapping[str, str],
        *,
        confirm: bool = False,
    ) -> SettingsOutcome:
        """
        Change an engine's settings, leaving it on the previous ones if it refuses them.

        Args:
            engine: The engine name.
            values: New values by key; ``default`` removes one from Noust's file.
            confirm: The operator accepts what the change costs (listening
                beyond loopback, writes refused or keys dropped, persistence
                off), each named by the refusal that asks for it.

        Returns:
            What changed and how it was applied.

        Raises:
            ValidationError: For an unknown setting, an unacceptable value,
                or an exposure the profile does not allow.
            ConfirmationRequired: When the change costs something and was not
                confirmed; nothing changed.
            DatabaseEngineError: When the engine is not running, or did not
                come back on the new settings (the previous ones are back).
        """
        self._host_engine(engine)
        manager = self.running(engine)
        try:
            outcome = settings_for(manager).apply(
                values,
                confirm=confirm,
                remote_listen_allowed=security_profile.database_remote_listen_allowed(),
            )
        except DatabaseEngineError:
            # The keys only: a value is not a secret here, but the trail has
            # never carried one and is not the place to start.
            self.audit(
                "db.settings.change", manager.ENGINE_NAME, outcome="failure", keys=sorted(values)
            )
            raise
        if outcome.changed:
            self.audit(
                "db.settings.change",
                manager.ENGINE_NAME,
                keys=outcome.changed,
                action=outcome.action,
                exposed=outcome.exposed,
            )
        return outcome
