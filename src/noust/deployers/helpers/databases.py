# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Provision a database and user for a deployer that needs one.

Every deployer that needs a shared database used to write this itself. The
monorepo deployer's copy swallowed "already exists" with a bare ``except
Exception``, and when the user it tried to create turned out to already
exist, the password it had just generated and written into ``.env`` did not
match the real one: the application deployed with a ``DATABASE_URL`` that
could never authenticate. This module is the one place that creates a
database, its user and the store's record of both, so every deployer that
needs one gets the same idempotent, ownership-checked behaviour.

Idempotency matters because a deploy that fails after provisioning and is
retried must not fail again for "database already exists": the database, the
user and its password (kept in the secret store, never regenerated once
written) are reused rather than recreated.

Reuse is only ever of what is recorded as the requesting application's. The
names can come from a repository (a monorepo's ``docker-compose.yml``), and
reusing a user by name alone once handed one application another's password.
The store's database rows say which application a database, and the user it
names, belongs to; a record beside each password
(``databases/<engine>/<user>.owner``) names the owning domain for the cases
the store cannot: a recipe provisions before its application row exists, and
a failed new deploy deletes its row.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from dataclasses import dataclass
from urllib.parse import quote

from wasm.core.exceptions import DatabaseError
from wasm.core.logger import Logger
from wasm.core.secrets import SecretStore
from wasm.core.store import Database, WASMStore, get_store
from wasm.managers.database.base import BaseDatabaseManager
from wasm.managers.database.registry import DatabaseRegistry

#: Engines this module knows how to provision. Other engines the registry
#: knows about (Redis, MongoDB) have no concept of a per-application
#: database-and-user pair, so a deployer that needs one asks for one of
#: these; "mariadb" resolves to "mysql" through the registry's aliases.
SUPPORTED_ENGINES: tuple[str, ...] = ("mysql", "postgresql")


@dataclass(frozen=True)
class DatabaseCredentials:
    """
    What a deployer needs to reach a provisioned database.

    Attributes:
        engine: Canonical engine name ("mysql" or "postgresql").
        name: Database name.
        user: User name.
        password: The user's password.
        host: Where the engine listens.
        port: The port the engine listens on.
    """

    engine: str
    name: str
    user: str
    password: str
    host: str = "localhost"
    port: int = 0

    @property
    def url(self) -> str:
        """
        A connection URL, with the user and password percent-encoded.

        A generated password never contains characters that need encoding
        (see :func:`generate_database_password`), but a user-chosen name or a
        password kept from before this module existed might, and an
        unencoded ``@`` or ``/`` in either would be parsed as part of the
        host or the path instead of the credential.

        Returns:
            ``<engine>://<user>:<password>@<host>:<port>/<name>``.
        """
        user = quote(self.user, safe="")
        password = quote(self.password, safe="")
        return f"{self.engine}://{user}:{password}@{self.host}:{self.port}/{self.name}"

    def context(self) -> dict[str, str]:
        """
        Render these credentials as template context.

        Returns:
            ``engine``, ``name``, ``user``, ``password``, ``host``, ``port``
            (as a string) and ``url``.
        """
        return {
            "engine": self.engine,
            "name": self.name,
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": str(self.port),
            "url": self.url,
        }


def _resolve_manager(engine: str) -> tuple[str, BaseDatabaseManager]:
    """
    Resolve an engine name or alias to its canonical name and manager.

    Args:
        engine: Engine name or alias, such as "mariadb" or "postgres".

    Returns:
        The canonical engine name (``manager.ENGINE_NAME``) and the manager.

    Raises:
        DatabaseError: The engine is not registered, or is registered but is
            not one WASM provisions a database-and-user pair for.
    """
    manager = DatabaseRegistry.get(engine)
    if manager is None:
        raise DatabaseError(
            f"Unknown database engine: {engine!r}",
            details=f"WASM provisions: {', '.join(SUPPORTED_ENGINES)}.",
        )
    canonical = manager.ENGINE_NAME
    if canonical not in SUPPORTED_ENGINES:
        raise DatabaseError(
            f"WASM does not provision a {manager.DISPLAY_NAME} database and user",
            details=f"WASM provisions: {', '.join(SUPPORTED_ENGINES)}.",
        )
    return canonical, manager


def _fit(base: str, suffix: str, max_length: int, digest: str) -> str:
    """
    Fit ``base + suffix`` inside ``max_length``, disambiguating truncation.

    Args:
        base: The candidate identifier's stem.
        suffix: What is appended to the stem ("_db" or "_user").
        max_length: The engine's limit for this kind of name.
        digest: A short hash of the untruncated app name, so two names that
            agree on their first ``max_length`` characters still end up with
            different identifiers.

    Returns:
        An identifier of at most ``max_length`` characters.
    """
    candidate = f"{base}{suffix}"
    if len(candidate) <= max_length:
        return candidate
    budget = max(max_length - len(suffix) - len(digest) - 1, 1)
    return f"{base[:budget]}_{digest}{suffix}"


def database_identifiers(app_name: str, engine: str) -> tuple[str, str]:
    """
    Derive a database name and a user name for an application.

    Args:
        app_name: The application's name, as used elsewhere for its
            directory and its unit (dashes for a domain's dots).
        engine: Engine name or alias.

    Returns:
        ``(database, user)``: ``<base>_db`` and ``<base>_user``, with dashes
        turned into underscores. When either is longer than the engine
        accepts, the base is truncated and a short stable hash of the
        untruncated app name is appended, so two long names that share a
        prefix never collide.

    Raises:
        DatabaseError: The engine is not registered, or is not one WASM
            provisions a database-and-user pair for.
    """
    _canonical, manager = _resolve_manager(engine)
    base = app_name.replace("-", "_")
    digest = hashlib.sha256(app_name.encode("utf-8")).hexdigest()[:8]
    database = _fit(base, "_db", manager.MAX_DATABASE_NAME_LENGTH, digest)
    user = _fit(base, "_user", manager.MAX_USER_NAME_LENGTH, digest)
    return database, user


def generate_database_password(length: int = 32) -> str:
    """
    Generate a password safe to embed in a ``DATABASE_URL``.

    Letters and digits only: an ``@``, ``#`` or ``/`` in a password breaks
    connection-string parsing in more than one client library, and this
    password is meant to be dropped straight into one.

    Args:
        length: Password length. Must be at least 3, to leave room for one
            lower case letter, one upper case letter and one digit.

    Returns:
        A password of the requested length, containing at least one lower
        case letter, one upper case letter and one digit.

    Raises:
        ValueError: ``length`` is too small to satisfy that guarantee.
    """
    if length < 3:
        raise ValueError("length must be at least 3")
    alphabet = string.ascii_letters + string.digits
    password = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
    ]
    password += [secrets.choice(alphabet) for _ in range(length - 3)]
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


#: Names no application's database or user may take. These are the engines'
#: own databases and superuser accounts: granting an application ALL on
#: ``postgres`` or handing it root's password is taking over the server.
#: Compared case-insensitively, because MySQL's names are not case-sensitive
#: on every platform and a repository controls the case it asks for.
RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "postgres",
        "root",
        "mysql",
        "template0",
        "template1",
        "information_schema",
        "performance_schema",
        "sys",
    }
)


def _refuse_reserved(name: str, *, kind: str, display_name: str) -> None:
    """
    Refuse a database or user name reserved by the engine itself.

    Args:
        name: Candidate name.
        kind: "database" or "user", for the message.
        display_name: The engine's display name, for the message.

    Raises:
        DatabaseError: The name is reserved.
    """
    if name.lower() in RESERVED_NAMES:
        raise DatabaseError(
            f"{name!r} is a reserved {display_name} {kind} name",
            details=f"Choose another {kind} name; WASM never gives an application "
            f"the server's own {kind}s.",
        )


def _password_secret(engine: str, user: str) -> str:
    """Name the secret holding a provisioned user's password."""
    return f"databases/{engine}/{user}"


def _owner_secret(engine: str, user: str) -> str:
    """
    Name the record of which application a provisioned user belongs to.

    It sits beside the password because the store cannot hold it: a recipe
    provisions before its application row exists, and a failed new deploy
    deletes its row, which unlinks the database. The domain survives both.
    A user name never contains a dot, so this never collides with a password.
    """
    return f"databases/{engine}/{user}.owner"


def _owner_key(domain: str | None) -> str:
    """The value an owner record holds for ``domain`` (empty for none)."""
    return domain or ""


def _domain_of(store: WASMStore, app_id: int) -> str:
    """The domain of an application id, for messages."""
    owner = store.get_app_by_id(app_id)
    return owner.domain if owner is not None else "another application"


def _check_database(
    store: WASMStore,
    manager: BaseDatabaseManager,
    engine: str,
    *,
    name: str,
    user: str,
    domain: str | None,
    app_id: int | None,
    user_owner: str | None,
) -> Database | None:
    """
    Refuse a database that is not the requesting application's to use.

    A database is this application's when its row is linked to it, or when
    its row is unlinked, names ``user`` and ``user`` is recorded as this
    application's (a recipe's database before its first deploy succeeds, or
    one whose failed new deploy removed the application row).

    Args:
        store: The store.
        manager: The engine's manager.
        engine: Canonical engine name.
        name: Database name.
        user: The user the database is being provisioned for.
        domain: The requesting application's domain, or None.
        app_id: The requesting application's id, when it has a row.
        user_owner: The owner recorded for ``user``, or None when none is.

    Returns:
        The database's row, or None when it has none and does not exist.

    Raises:
        DatabaseError: The database belongs to another application, was
            created outside an application, or exists and WASM did not
            create it.
    """
    row = store.get_database(name, engine)
    if row is None:
        if manager.database_exists(name):
            raise DatabaseError(
                f"The {manager.DISPLAY_NAME} database {name!r} already exists and "
                "WASM did not create it",
                details="Choose another database name; WASM does not give an application "
                "a database it did not provision for it.",
            )
        return None
    if row.app_id is not None:
        if app_id is not None and row.app_id == app_id:
            return row
        raise DatabaseError(
            f"The {engine} database {name!r} already belongs to {_domain_of(store, row.app_id)}",
            details="Choose another name, or provision the database for that application instead.",
        )
    if row.username == user and user_owner == _owner_key(domain):
        return row
    raise DatabaseError(
        f"The {engine} database {name!r} is not recorded as {domain or 'this deployment'}'s",
        details="It was created outside this application (with `wasm db create`, or by "
        "an application since deleted). Choose another database name.",
    )


def _check_user(
    store: WASMStore,
    manager: BaseDatabaseManager,
    engine: str,
    *,
    user: str,
    domain: str | None,
    app_id: int | None,
    user_owner: str | None,
) -> bool:
    """
    Refuse a user that is not the requesting application's to use.

    A user is this application's when a database row linked to it names
    the user (what every WASM before 2.3 recorded), or when the owner record
    beside its password names this application's domain. A user linked to
    another application, or recorded for another domain, is refused even if
    it is also linked here: its password would reach this application's
    ``.env``.

    Args:
        store: The store.
        manager: The engine's manager.
        engine: Canonical engine name.
        user: User name.
        domain: The requesting application's domain, or None.
        app_id: The requesting application's id, when it has a row.
        user_owner: The owner recorded for ``user``, or None when none is.

    Returns:
        Whether the user exists on the server (and is this application's).

    Raises:
        DatabaseError: The user belongs to another application, or exists
            and WASM did not create it.
    """
    exists = manager.user_exists(user)
    linked = {
        row.app_id
        for row in store.list_databases(engine=engine)
        if row.username == user and row.app_id is not None
    }
    foreign = linked - {app_id}
    if foreign:
        raise DatabaseError(
            f"The {manager.DISPLAY_NAME} user {user!r} already belongs to "
            f"{_domain_of(store, min(foreign))}",
            details="Choose another user name; an application never gets another's credentials.",
        )
    if not exists:
        # A record left by a user dropped by hand is stale, not an owner:
        # there is nothing left to take over, and a new password is generated.
        return False
    if user_owner is not None and user_owner != _owner_key(domain):
        raise DatabaseError(
            f"The {manager.DISPLAY_NAME} user {user!r} already belongs to "
            f"{user_owner or 'a database provisioned without an application'}",
            details="Choose another user name; an application never gets another's credentials.",
        )
    if user_owner is None and app_id not in linked:
        raise DatabaseError(
            f"The {manager.DISPLAY_NAME} user {user!r} already exists and WASM did not create it",
            details="Choose another user name; WASM does not take over a user it did not "
            "provision for this application.",
        )
    return True


def provision_database(
    engine: str,
    *,
    name: str,
    user: str,
    domain: str | None = None,
    createdb: bool = False,
    logger: Logger,
    store: WASMStore | None = None,
    secret_store: SecretStore | None = None,
) -> DatabaseCredentials:
    """
    Create a database and user, or reuse them if a previous attempt already did.

    Safe to call again after a deploy that provisioned a database and then
    failed at a later step, the grant included: the database, the user and
    its password (kept in the secret store) are recorded as the requesting
    application's before they are created, so a retry reuses them rather
    than generating a password that no longer matches the user WASM made.

    Both names can come from a repository (a monorepo's
    ``docker-compose.yml``), so neither is trusted: only a database and a user
    recorded as ``domain``'s are ever reused, and the engine's own databases
    and accounts are refused outright.

    Args:
        engine: Engine name or alias ("mariadb" resolves to "mysql").
        name: Database name.
        user: User name.
        domain: The application this database is for, when there is one.
            Owns the store's record of the database and the record of the
            user, and tells one application's database from another's.
        createdb: Grant CREATEDB when creating the user. PostgreSQL only
            (Prisma's shadow database needs it); ignored on MySQL, whose
            ``create_user`` takes and ignores unknown keyword arguments.
        logger: Where progress is reported.
        store: The store to record the database in. Defaults to the
            process-wide store.
        secret_store: Where the user's password and owner are kept. Defaults
            to one rooted at the store's own secrets directory.

    Returns:
        The credentials to reach the database with.

    Raises:
        DatabaseError: The engine is unknown or unsupported, is not
            installed, either name is invalid or reserved, the database or
            the user belongs to another application or was not created by
            WASM, the user is this application's but WASM does not know its
            password, or creating the database, the user or the grant fails.
    """
    canonical, manager = _resolve_manager(engine)
    if not manager.is_installed():
        raise DatabaseError(
            f"{manager.DISPLAY_NAME} is not installed",
            details=f"Install it with: wasm db install {canonical}",
        )

    manager.validate_database_name(name)
    manager.validate_user_name(user)
    _refuse_reserved(name, kind="database", display_name=manager.DISPLAY_NAME)
    _refuse_reserved(user, kind="user", display_name=manager.DISPLAY_NAME)

    store = store or get_store()
    secret_store = secret_store or SecretStore()
    app = store.get_app(domain) if domain else None
    app_id = app.id if app is not None else None
    password_secret = _password_secret(canonical, user)
    owner_secret = _owner_secret(canonical, user)
    user_owner = secret_store.read(owner_secret)

    # Every check runs before anything is created, granted or read from the
    # secret store: a refused request must not have touched the server.
    user_exists = _check_user(
        store, manager, canonical, user=user, domain=domain, app_id=app_id, user_owner=user_owner
    )
    existing_row = _check_database(
        store,
        manager,
        canonical,
        name=name,
        user=user,
        domain=domain,
        app_id=app_id,
        user_owner=user_owner,
    )

    if user_exists:
        password = secret_store.read(password_secret)
        if password is None:
            raise DatabaseError(
                f"The {manager.DISPLAY_NAME} user {user} already exists and "
                "WASM does not know its password",
                details=(
                    f"Drop it with: wasm db user-delete {user} --engine {canonical}, "
                    "then retry the deployment."
                ),
            )
    else:
        # A previous attempt by this same application may have written the
        # password and then crashed before creating the user; reuse it. One
        # recorded for anyone else is not this application's to learn.
        stored = secret_store.read(password_secret) if user_owner == _owner_key(domain) else None
        password = stored or generate_database_password()
        # Written before the user exists: a crash between the two must not
        # lose the only copy of a password WASM just committed to using.
        secret_store.write(password_secret, password)
    # Recorded before the user or the database is created, so a failure at
    # any later step (the grant, say) leaves both recognisably this
    # application's and the retry reuses them.
    if user_owner != _owner_key(domain):
        secret_store.write(owner_secret, _owner_key(domain))

    if existing_row is None:
        store.create_database(
            Database(
                app_id=app_id,
                name=name,
                engine=canonical,
                host="localhost",
                port=manager.server_port(),
                username=user,
            )
        )
    elif existing_row.app_id is None and app is not None and domain:
        store.link_database_to_app(name, canonical, domain)

    if manager.database_exists(name):
        logger.substep(f"Reusing existing {manager.DISPLAY_NAME} database: {name}")
    else:
        manager.create_database(name)
        logger.substep(f"Created {manager.DISPLAY_NAME} database: {name}")

    if not user_exists:
        manager.create_user(user, password=password, createdb=createdb)
        logger.substep(f"Created {manager.DISPLAY_NAME} user: {user}")

    manager.grant_privileges(username=user, database=name)

    return DatabaseCredentials(
        engine=canonical,
        name=name,
        user=user,
        password=password,
        host="localhost",
        port=manager.server_port(),
    )
