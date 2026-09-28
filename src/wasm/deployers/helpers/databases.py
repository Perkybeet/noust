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


def _owning_row(store: WASMStore, engine: str, name: str, domain: str | None) -> Database | None:
    """
    Check whether an existing database row belongs to another application.

    Args:
        store: The store.
        engine: Canonical engine name.
        name: Database name.
        domain: The application asking for the database, or None when no
            application owns this provisioning attempt.

    Returns:
        The existing row, or None when there is none.

    Raises:
        DatabaseError: The row is linked to an application other than
            ``domain``'s, or the row is linked while ``domain``'s application
            does not exist.
    """
    row = store.get_database(name, engine)
    if row is None or row.app_id is None:
        return row

    requesting_app = store.get_app(domain) if domain else None
    if requesting_app is not None and requesting_app.id == row.app_id:
        return row

    owner = store.get_app_by_id(row.app_id)
    owner_domain = owner.domain if owner is not None else "another application"
    raise DatabaseError(
        f"The {engine} database {name!r} already belongs to {owner_domain}",
        details="Choose another name, or provision the database for that application instead.",
    )


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
    failed at a later step: the database, the user and its password (kept in
    the secret store) are reused rather than recreated, so a retry never
    generates a password that no longer matches the user WASM already made.

    Args:
        engine: Engine name or alias ("mariadb" resolves to "mysql").
        name: Database name.
        user: User name.
        domain: The application this database is for, when there is one.
            Used to own the store's record of the database and to tell one
            application's database from another's.
        createdb: Grant CREATEDB when creating the user. PostgreSQL only
            (Prisma's shadow database needs it); ignored on MySQL, whose
            ``create_user`` takes and ignores unknown keyword arguments.
        logger: Where progress is reported.
        store: The store to record the database in. Defaults to the
            process-wide store.
        secret_store: Where the user's password is kept. Defaults to one
            rooted at the store's own secrets directory.

    Returns:
        The credentials to reach the database with.

    Raises:
        DatabaseError: The engine is unknown or unsupported, is not
            installed, either name is invalid, the database belongs to
            another application, the user already exists with a password
            WASM does not know, or creating the database, the user or the
            grant fails.
    """
    canonical, manager = _resolve_manager(engine)
    if not manager.is_installed():
        raise DatabaseError(
            f"{manager.DISPLAY_NAME} is not installed",
            details=f"Install it with: wasm db install {canonical}",
        )

    manager.validate_database_name(name)
    manager.validate_user_name(user)

    store = store or get_store()
    secret_store = secret_store or SecretStore()

    existing_row = _owning_row(store, canonical, name, domain)

    if manager.database_exists(name):
        logger.substep(f"Reusing existing {manager.DISPLAY_NAME} database: {name}")
    else:
        manager.create_database(name)
        logger.substep(f"Created {manager.DISPLAY_NAME} database: {name}")

    secret_name = f"databases/{canonical}/{user}"
    if manager.user_exists(user):
        password = secret_store.read(secret_name)
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
        # A previous attempt may have written the password and then crashed
        # before creating the user; reuse it instead of orphaning it.
        password = secret_store.read(secret_name) or generate_database_password()
        # Written before the user exists: a crash between the two must not
        # lose the only copy of a password WASM just committed to using.
        secret_store.write(secret_name, password)
        manager.create_user(user, password=password, createdb=createdb)
        logger.substep(f"Created {manager.DISPLAY_NAME} user: {user}")

    manager.grant_privileges(username=user, database=name)

    if existing_row is None:
        app = store.get_app(domain) if domain else None
        store.create_database(
            Database(
                app_id=app.id if app is not None else None,
                name=name,
                engine=canonical,
                host="localhost",
                port=manager.server_port(),
                username=user,
            )
        )
    elif existing_row.app_id is None and domain:
        app = store.get_app(domain)
        if app is not None:
            store.link_database_to_app(name, canonical, domain)

    return DatabaseCredentials(
        engine=canonical,
        name=name,
        user=user,
        password=password,
        host="localhost",
        port=manager.server_port(),
    )
