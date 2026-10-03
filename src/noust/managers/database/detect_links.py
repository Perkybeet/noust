# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Find the databases an application already uses, from its own environment.

Noust only knew a link it had written itself (``noust db link``): an
application configured by hand, or deployed before Noust provisioned
databases, showed beside no database even though its ``.env`` named one in
``DATABASE_URL``. This module reads that environment and says which engine,
port and database each reference points at. It never returns a password and
never runs anything of the application's: it parses text.

Resolving a reference to an engine needs to know which engines listen where;
that is given as :class:`Endpoint` values by the caller (the database
service), so this module stays a pure function of the environment.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

from noust.core.store import App
from noust.deployers.helpers.env_manager import EnvManager
from noust.deployers.helpers.layout import app_root, env_file_for

#: URL schemes, to the canonical engine they name.
SCHEMES: dict[str, str] = {
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "mysql": "mysql",
    "mysql2": "mysql",
    "mariadb": "mysql",
    "redis": "redis",
    "rediss": "redis",
    "valkey": "redis",
    "mongodb": "mongodb",
    "mongodb+srv": "mongodb",
}

#: A value that is a connection URL: the scheme, then ``//``. Prisma's
#: ``postgresql://`` and Laravel's ``mysql://`` alike; JDBC wrappers and
#: driver suffixes (``postgresql+asyncpg://``) are read by their first word.
_URL = re.compile(r"\A(?:jdbc:)?(?P<scheme>[a-z][a-z0-9]*(?:\+srv)?)(?:\+[a-z0-9]+)?://", re.I)

#: Laravel's ``DB_CONNECTION`` values, to the engine.
_CONNECTIONS: dict[str, str] = {
    "mysql": "mysql",
    "mariadb": "mysql",
    "pgsql": "postgresql",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "mongodb": "mongodb",
}

#: Each engine's port when a reference names none.
DEFAULT_PORTS: dict[str, int] = {"postgresql": 5432, "mysql": 3306, "redis": 6379, "mongodb": 27017}

#: Names a reference uses for this machine. ``host.docker.internal`` and the
#: default bridge gateway are how a container reaches a database on the host.
LOCAL_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal", "172.17.0.1"}  # noqa: S104 - names read, not bound
)


@dataclass(frozen=True)
class Reference:
    """
    One database an application's environment names.

    Attributes:
        variable: The variable that names it (``DATABASE_URL``, ``DB_HOST``).
        engine: The canonical engine it is for.
        host: The host it connects to, lowercased.
        port: The port, the engine's default when it names none.
        database: The database (a slot number for Redis), when it names one.
        username: The account it signs in as, when it names one.
    """

    variable: str
    engine: str
    host: str
    port: int
    database: str | None
    username: str | None


@dataclass(frozen=True)
class Endpoint:
    """
    Somewhere an engine listens, to resolve references against.

    Attributes:
        engine: The key the database service knows the engine by: ``mysql``
            for the host's, an instance key for a container.
        family: The canonical engine it is (``postgresql``...).
        ports: The ports it answers on from this machine.
        project: The Compose project of a container, when it has one.
        service: The Compose service of a container, when it has one.
    """

    engine: str
    family: str
    ports: frozenset[int]
    project: str | None = None
    service: str | None = None


@dataclass(frozen=True)
class Detected:
    """
    A reference, resolved.

    Attributes:
        domain: The application.
        reference: What its environment says.
        engine: The engine key it resolves to; None when it points outside
            this server or at nothing that listens here.
    """

    domain: str
    reference: Reference
    engine: str | None


def _url_reference(variable: str, value: str) -> Reference | None:
    """
    Read a connection URL.

    Args:
        variable: The variable holding it.
        value: Its value.

    Returns:
        The reference, or None for a value that is not a database URL.
    """
    match = _URL.match(value.strip())
    if match is None:
        return None
    scheme = match.group("scheme").lower()
    engine = SCHEMES.get(scheme)
    if engine is None:
        return None
    try:
        parts = urlsplit(value.strip().removeprefix("jdbc:"))
        port = parts.port
    except ValueError:
        return None
    # mongodb+srv and a replica set's "host1,host2" name more than one host:
    # the first is enough to tell this machine from another.
    host = (parts.hostname or "").split(",")[0].lower()
    database = unquote(parts.path.lstrip("/")).split("/")[0] or None
    return Reference(
        variable=variable,
        engine=engine,
        host=host,
        port=port or DEFAULT_PORTS[engine],
        database=database,
        username=unquote(parts.username) if parts.username else None,
    )


def _int(value: str | None) -> int | None:
    """
    Read a port.

    Args:
        value: The text.

    Returns:
        The number, or None when it is not one.
    """
    if value is None or not value.strip().isdigit():
        return None
    return int(value.strip())


def _set_references(env: Mapping[str, str]) -> list[Reference]:
    """
    Read the ``DB_*`` and ``REDIS_*`` sets Laravel, Symfony and hand-written
    configurations use.

    Args:
        env: The environment.

    Returns:
        A reference per set that names a host.
    """
    references: list[Reference] = []
    host = env.get("DB_HOST")
    if host:
        port = _int(env.get("DB_PORT"))
        engine = _CONNECTIONS.get(env.get("DB_CONNECTION", "").strip().lower())
        if engine is None:
            engine = "postgresql" if port == DEFAULT_PORTS["postgresql"] else "mysql"
        references.append(
            Reference(
                variable="DB_HOST",
                engine=engine,
                host=host.strip().lower(),
                port=port or DEFAULT_PORTS[engine],
                database=(env.get("DB_DATABASE") or env.get("DB_NAME") or "").strip() or None,
                username=(env.get("DB_USERNAME") or env.get("DB_USER") or "").strip() or None,
            )
        )
    redis_host = env.get("REDIS_HOST")
    if redis_host:
        references.append(
            Reference(
                variable="REDIS_HOST",
                engine="redis",
                host=redis_host.strip().lower(),
                port=_int(env.get("REDIS_PORT")) or DEFAULT_PORTS["redis"],
                database=(env.get("REDIS_DB") or "0").strip() or "0",
                username=(env.get("REDIS_USERNAME") or "").strip() or None,
            )
        )
    return references


def references_in(env: Mapping[str, str]) -> list[Reference]:
    """
    Every database an environment names.

    Args:
        env: Variable name to value.

    Returns:
        The references, URLs first, one per distinct target.
    """
    found: list[Reference] = []
    for variable in sorted(env):
        reference = _url_reference(variable, env[variable])
        if reference is not None:
            found.append(reference)
    found.extend(_set_references(env))
    unique: list[Reference] = []
    seen: set[tuple[str, str, int, str | None]] = set()
    for reference in found:
        key = (reference.engine, reference.host, reference.port, reference.database)
        if key not in seen:
            seen.add(key)
            unique.append(reference)
    return unique


def resolve(
    reference: Reference, endpoints: Iterable[Endpoint], *, project: str | None = None
) -> str | None:
    """
    Name the engine a reference reaches.

    Args:
        reference: What the environment says.
        endpoints: Where engines listen.
        project: The application's Compose project, so a service name such as
            ``db`` resolves to that project's service.

    Returns:
        The engine key, or None when nothing here matches.
    """
    candidates = [endpoint for endpoint in endpoints if endpoint.family == reference.engine]
    if reference.host in LOCAL_HOSTS:
        matching = [endpoint for endpoint in candidates if reference.port in endpoint.ports]
    else:
        matching = [
            endpoint
            for endpoint in candidates
            if endpoint.service == reference.host
            and (project is None or endpoint.project == project)
        ]
    # Two engines on one port cannot both answer it; a name two projects use
    # is only meaningful inside one. Either way, guessing would be wrong.
    return matching[0].engine if len(matching) == 1 else None


def read_environment(app: App, *, manager: EnvManager | None = None) -> dict[str, str]:
    """
    Read an application's ``.env``, refusing one that leads outside its tree.

    A repository is untrusted input: a ``.env`` that is a link to a file
    elsewhere is not read, because what this module parses out of it ends up
    in the console.

    Args:
        app: The application.
        manager: Parser to read with.

    Returns:
        Variable name to value; empty when there is none or it leads outside.
    """
    path = env_file_for(app)
    root = app_root(app)
    try:
        resolved = path.resolve(strict=True)
        inside = resolved.is_relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError):
        return {}
    if not inside or not resolved.is_file():
        return {}
    return (manager or EnvManager()).read_env_file(Path(resolved))


@dataclass
class AppReferences:
    """
    What one application's environment names.

    Attributes:
        app: The application.
        references: The references, in the order found.
        project: Its Compose project, for service names.
    """

    app: App
    references: list[Reference] = field(default_factory=list)
    project: str | None = None


def detect(apps: Iterable[AppReferences], endpoints: Iterable[Endpoint]) -> list[Detected]:
    """
    Resolve every application's references.

    Args:
        apps: Each application with what its environment names.
        endpoints: Where engines listen.

    Returns:
        One entry per reference, resolved or not.
    """
    endpoints = list(endpoints)
    return [
        Detected(entry.app.domain, reference, resolve(reference, endpoints, project=entry.project))
        for entry in apps
        for reference in entry.references
    ]
