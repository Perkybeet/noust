# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database instances: the engine on the host, and the ones Docker runs.

Until 3.3 an engine was "the one installed on this server", and the databases
most servers really run - the Postgres, MySQL, MariaDB and Redis of a Compose
stack - were invisible. An **instance** is where a database lives, and it is
named by a key that goes wherever an engine name went before:

- ``postgresql``, ``mysql``, ``redis``, ``mongodb``: the host's engine, as
  always.
- ``postgresql@<project>.<service>``: the service of a Compose project, stable
  while its container is recreated or renamed.
- ``postgresql@<container>``: a container with no project.

The store already keeps ``engine`` as an opaque key, so every link, dump and
policy works for an instance with no migration. This module is the only code
that builds or splits a key (:func:`instance_key`, :func:`parse_instance_key`).

It is also the one answer to two other questions:

- **Which engine an image runs** (:func:`image_engine`), by the image's
  repository name, never by a substring: ``zabbix/zabbix-server-mysql`` is not
  MySQL.
- **How a client inside a database container gets its password**
  (:data:`CLIENT_SCRIPT`). The password stays where it is: the script reads it
  from the container's own environment (or the file a ``*_FILE`` variable
  names) and hands it to the client the way that client takes a secret off the
  command line. It is in no argv on the host or in the container and in no
  environment Noust builds. A secret Noust itself holds (the read-only
  console's password) travels in the ``docker`` process's environment and is
  forwarded with ``-e NAME`` and no value, which Docker reads from there.

Discovery runs ``docker ps`` and ``docker inspect`` through the
:class:`~noust.core.runner.CommandRunner`; both are declared read-only probes.
What inspect says about the environment is reduced to the variables' names
and the few values that are not secret (who to sign in as, which database):
nothing else is kept, and nothing of it reaches the API.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any, Literal

from noust.central import require_server_role
from noust.core.exceptions import DatabaseQueryError, ValidationError
from noust.core.runner import CommandRunner, get_runner
from noust.core.utils import domain_to_app_name

__all__ = [
    "CLIENT_SCRIPT",
    "ENGINE_PORTS",
    "PASSWORD_SOURCES",
    "PROJECT_LABEL",
    "SERVICE_LABEL",
    "Access",
    "DatabaseInstance",
    "ImageMatch",
    "InstanceKey",
    "Launch",
    "PublishedPort",
    "assign_apps",
    "discover",
    "engine_of",
    "image_engine",
    "instance_key",
    "is_instance_key",
    "parse_instance_key",
    "storage_name",
]

#: The labels Compose puts on every container it creates.
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"

#: The engines an instance can be, with the port each listens on in its image.
ENGINE_PORTS: dict[str, int] = {
    "postgresql": 5432,
    "mysql": 3306,
    "redis": 6379,
    "mongodb": 27017,
}

#: Seconds ``docker ps`` and ``docker inspect`` may take.
DOCKER_TIMEOUT = 30

#: What follows the ``@`` of a key: a project and a service, or a container.
#: Compose and Docker names, which fit in a URL segment and a file name.
_KEY_REST = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,254}\Z")

#: One name of a project, a service or a container.
_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")

#: A project name as Compose writes it: no dot, which is what lets a key's
#: first dot separate the project from the service.
_PROJECT = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,127}\Z")

#: An image reference ``docker ps`` prints when the image lost its tag.
_IMAGE_ID = re.compile(r"\A(sha256:)?[0-9a-f]{12,64}\Z")

#: Registries that are Docker Hub under another spelling.
_HUB_REGISTRIES = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})

Flavour = Literal["postgres", "mysql", "mariadb", "redis", "valkey", "mongo"]

#: Docker Hub's official images, by repository, and the flavour they are.
_OFFICIAL: dict[str, Flavour] = {
    "postgres": "postgres",
    "mysql": "mysql",
    "mariadb": "mariadb",
    "redis": "redis",
    "valkey": "valkey",
    "mongo": "mongo",
}

#: Images published under an organisation that are known to run an engine, by
#: ``<namespace>/<repository>``. A trailing ``*`` matches any suffix
#: (``timescale/timescaledb-ha``).
_VENDOR_IMAGES: tuple[tuple[str, Flavour], ...] = (
    ("postgis/postgis", "postgres"),
    ("timescale/timescaledb*", "postgres"),
    ("pgvector/pgvector", "postgres"),
    ("valkey/valkey", "valkey"),
    ("bitnami/postgresql", "postgres"),
    ("bitnami/mysql", "mysql"),
    ("bitnami/mariadb", "mariadb"),
    ("bitnami/redis", "redis"),
    ("bitnami/mongodb", "mongo"),
)

#: The engine each flavour is managed as.
_FLAVOUR_ENGINE: dict[Flavour, str] = {
    "postgres": "postgresql",
    "mysql": "mysql",
    "mariadb": "mysql",
    "redis": "redis",
    "valkey": "redis",
    "mongo": "mongodb",
}

#: Where each engine's password may be, by who signs in. Names Noust writes,
#: never names read from a container: :data:`CLIENT_SCRIPT` evaluates them.
PASSWORD_SOURCES: dict[str, tuple[str, ...]] = {
    "postgres": ("POSTGRES_PASSWORD", "POSTGRESQL_PASSWORD", "POSTGRESQL_POSTGRES_PASSWORD"),
    "postgres-bitnami-superuser": ("POSTGRESQL_POSTGRES_PASSWORD",),
    "mysql-user": ("MYSQL_PASSWORD",),
    "mysql-root": ("MYSQL_ROOT_PASSWORD",),
    "mariadb-user": ("MARIADB_PASSWORD", "MYSQL_PASSWORD"),
    "mariadb-root": ("MARIADB_ROOT_PASSWORD", "MYSQL_ROOT_PASSWORD"),
    "redis": ("REDIS_PASSWORD", "VALKEY_PASSWORD"),
    "mongo": ("MONGO_INITDB_ROOT_PASSWORD", "MONGODB_ROOT_PASSWORD"),
}

#: The one script every client inside a database container runs through.
#: Positional parameters: the mode (``env`` or ``mongo``), the variable to
#: hand the password to (or the mongo user), the comma-separated environment
#: variables the password may be in, the comma-separated program names to try,
#: then the program's own arguments. Names reach ``eval`` only from
#: :data:`PASSWORD_SOURCES`; a value never reaches the script's text.
CLIENT_SCRIPT = """\
mode=$1; target=$2; candidates=$3; programs=$4
shift 4
value=
program=
saved=$IFS; IFS=,
for name in $candidates; do
  eval "value=\\${$name:-}"
  eval "file=\\${${name}_FILE:-}"
  if [ -z "$value" ] && [ -n "$file" ] && [ -r "$file" ]; then value=$(cat "$file"); fi
  if [ -n "$value" ]; then break; fi
done
for name in $programs; do
  if command -v "$name" >/dev/null 2>&1; then program=$name; break; fi
done
IFS=$saved
if [ -z "$program" ]; then
  echo "none of $programs is installed in this container" >&2
  exit 127
fi
if [ "$mode" = mongo ]; then
  if [ -z "$target" ] || [ -z "$value" ]; then exec "$program" "$@"; fi
  config=$(mktemp) || exit 1
  trap 'rm -f "$config"' EXIT
  escaped=$(printf '%s' "$value" | sed -e 's/\\\\/\\\\\\\\/g' -e 's/"/\\\\"/g')
  printf 'password: "%s"\\n' "$escaped" > "$config"
  "$program" "$@" --username "$target" --authenticationDatabase admin --config "$config"
  exit $?
fi
if [ -n "$value" ]; then export "$target=$value"; fi
exec "$program" "$@"
"""

#: The names a client program goes by in the images that ship it, first
#: preferred: MariaDB 11 images dropped the ``mysql`` names, Valkey's have
#: their own, and MongoDB 6 removed the legacy shell.
PROGRAM_NAMES: dict[str, str] = {
    "mysql": "mariadb,mysql",
    "mysqldump": "mariadb-dump,mysqldump",
    "mysqladmin": "mariadb-admin,mysqladmin",
    "redis-cli": "redis-cli,valkey-cli",
    "valkey-cli": "valkey-cli,redis-cli",
    "redis-server": "redis-server,valkey-server",
    "valkey-server": "valkey-server,redis-server",
    "mongosh": "mongosh,mongo",
    "mongo": "mongosh,mongo",
}

#: Variables whose value is who signs in or what to open, never a secret, and
#: so the only values discovery keeps.
_SETTING_NAMES = frozenset(
    {
        "POSTGRES_USER",
        "POSTGRES_DB",
        "POSTGRESQL_USERNAME",
        "POSTGRESQL_DATABASE",
        "MYSQL_USER",
        "MYSQL_DATABASE",
        "MARIADB_USER",
        "MARIADB_DATABASE",
        "MYSQL_ALLOW_EMPTY_PASSWORD",
        "MARIADB_ALLOW_EMPTY_ROOT_PASSWORD",
        "ALLOW_EMPTY_PASSWORD",
        "MONGO_INITDB_ROOT_USERNAME",
        "MONGO_INITDB_DATABASE",
        "MONGODB_ROOT_USER",
    }
)

#: How much access the credentials an instance carries give Noust.
Access = Literal["full", "limited"]


# ==================== Keys ====================


@dataclass(frozen=True)
class InstanceKey:
    """
    A key, split.

    Attributes:
        engine: The engine part, before the ``@``.
        project: The Compose project, for a service's key.
        service: The Compose service, for a service's key.
        container: The container, for a key of a container with no project.
    """

    engine: str
    project: str | None = None
    service: str | None = None
    container: str | None = None

    @property
    def is_host(self) -> bool:
        """Whether the key names the host's engine."""
        return self.project is None and self.container is None

    def __str__(self) -> str:
        """The key, as :func:`instance_key` builds it."""
        return instance_key(
            self.engine, project=self.project, service=self.service, container=self.container
        )


def instance_key(
    engine: str,
    *,
    project: str | None = None,
    service: str | None = None,
    container: str | None = None,
) -> str:
    """
    Build the key of an instance.

    Args:
        engine: The engine (``postgresql``, ``mysql``, ``redis``, ``mongodb``).
        project: The Compose project, with ``service``.
        service: The Compose service, with ``project``.
        container: The container, for one with no project.

    Returns:
        ``engine`` alone for the host, ``engine@project.service`` for a
        service, ``engine@container`` for a container.

    Raises:
        ValidationError: When a name could not be part of a key, or a project
            comes without its service.
    """
    if project is not None or service is not None:
        if not (project and service and _PROJECT.match(project) and _NAME.match(service)):
            raise ValidationError(
                f"Not a Compose project and service Noust can name: {project!r}, {service!r}",
                details="A project is lowercase letters, digits, '_' and '-'; a service "
                "adds upper case and '.'.",
                field="engine",
            )
        return f"{engine}@{project}.{service}"
    if container is not None:
        if not _NAME.match(container):
            raise ValidationError(
                f"Not a container name Noust can name: {container!r}",
                details="Container names are letters, digits, '_', '.' and '-'.",
                field="engine",
            )
        return f"{engine}@{container}"
    return engine


def is_instance_key(key: str) -> bool:
    """
    Tell whether a key names a container rather than the host's engine.

    Args:
        key: An engine name or an instance key.

    Returns:
        True for ``engine@...``.
    """
    return "@" in key


def parse_instance_key(key: str) -> InstanceKey:
    """
    Split a key.

    A key with a dot after the ``@`` is read as ``project.service``, because a
    Compose project never has one; a container named with a dot reads the
    same way, which is harmless because instances are looked up by the whole
    key, never by its parts.

    Args:
        key: An engine name or an instance key.

    Returns:
        Its parts.

    Raises:
        ValidationError: When what follows the ``@`` is not a name.
    """
    engine, separator, rest = key.partition("@")
    engine = engine.lower()
    if not separator:
        return InstanceKey(engine=engine)
    if not engine or not _KEY_REST.match(rest):
        raise ValidationError(
            f"Not a database instance: {key!r}",
            details="An instance is written engine@project.service (a Compose service) or "
            "engine@container, as 'noust db engines' lists it.",
            field="engine",
        )
    project, dot, service = rest.partition(".")
    if dot and service and _PROJECT.match(project):
        return InstanceKey(engine=engine, project=project, service=service)
    return InstanceKey(engine=engine, container=rest)


def engine_of(key: str) -> str:
    """
    Name the engine of a key, which is what engine-specific code compares.

    Args:
        key: An engine name or an instance key.

    Returns:
        The part before the ``@``, lower case.
    """
    return key.partition("@")[0].lower()


def storage_name(key: str) -> str:
    """
    Spell a key where only file-name characters are allowed.

    A dump's file name and a secret's path accept letters, digits, ``.``,
    ``_`` and ``-``. The host's engines keep their names, so nothing already
    on disk moves; an instance's ``@`` becomes a ``.``, which a database name
    never contains, so the database that follows stays unambiguous.

    Args:
        key: An engine name or an instance key.

    Returns:
        The key, with its ``@`` replaced.
    """
    return key.replace("@", ".", 1)


# ==================== Images ====================


@dataclass(frozen=True)
class ImageMatch:
    """
    What an image runs.

    Attributes:
        engine: The engine Noust manages it as (``postgresql``, ``mysql``,
            ``redis``, ``mongodb``).
        flavour: The image's own engine (``postgres``, ``mysql``,
            ``mariadb``, ``redis``, ``valkey``, ``mongo``).
        official: A Docker Hub official image, whose variables are the ones
            the image documents.
        repository: The repository, without registry, tag or digest.
    """

    engine: str
    flavour: Flavour
    official: bool
    repository: str


def image_engine(image: str | None) -> ImageMatch | None:
    """
    Tell which database engine an image runs, from its repository name.

    The repository is compared whole: ``postgres:16-alpine`` and
    ``docker.io/library/postgres`` are Postgres, ``zabbix/zabbix-server-mysql``
    is not MySQL and ``my-postgres-wrapper`` is not Postgres. An image on
    another registry is not recognised, and an image whose name says nothing
    is not guessed from the port it exposes.

    Args:
        image: An image reference: ``postgres:16-alpine``,
            ``docker.io/library/mysql``, ``bitnami/postgresql:16``, with or
            without a ``@sha256:`` digest.

    Returns:
        What it runs, or None for any other image.
    """
    if not image or not isinstance(image, str):
        return None
    reference = image.strip().split("@", 1)[0].lower()
    parts = reference.split("/")
    first = parts[0]
    # A first component is a registry when it names a host: a dot, a port or localhost.
    if len(parts) > 1 and ("." in first or ":" in first or first == "localhost"):
        if first not in _HUB_REGISTRIES:
            return None
        parts = parts[1:]
    if len(parts) == 2 and parts[0] == "library":
        parts = parts[1:]
    parts[-1] = parts[-1].split(":", 1)[0]
    repository = "/".join(parts)
    if len(parts) == 1:
        flavour = _OFFICIAL.get(repository)
        if flavour is None:
            return None
        return ImageMatch(_FLAVOUR_ENGINE[flavour], flavour, True, repository)
    if len(parts) != 2:
        return None
    for pattern, vendor_flavour in _VENDOR_IMAGES:
        if pattern.endswith("*"):
            matched = repository.startswith(pattern[:-1])
        else:
            matched = repository == pattern
        if matched:
            return ImageMatch(_FLAVOUR_ENGINE[vendor_flavour], vendor_flavour, False, repository)
    return None


# ==================== Instances ====================


@dataclass(frozen=True)
class PublishedPort:
    """
    A container port Docker publishes on the host.

    Attributes:
        container_port: The port inside the container.
        host_ip: The host address it is bound to (``0.0.0.0`` for all).
        host_port: The host port.
    """

    container_port: int
    host_ip: str
    host_port: int


@dataclass(frozen=True)
class Launch:
    """
    How :data:`CLIENT_SCRIPT` starts one client.

    Attributes:
        mode: ``env`` (export the password as ``target``) or ``mongo`` (a
            0600 ``--config`` file and ``--username target``).
        target: The variable the password goes to, or MongoDB's user.
        candidates: Where the password may be in the container.
    """

    mode: Literal["env", "mongo"] = "env"
    target: str = "NOUST_UNUSED"
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class DatabaseInstance:
    """
    A database engine running in a container.

    Attributes:
        key: Its instance key.
        engine: The engine it is managed as.
        flavour: The image's own engine (``mariadb`` for a MySQL-managed one).
        container: The container's name, which ``docker exec`` is given.
        container_id: The container's id.
        image: The image reference it was created from.
        project: Its Compose project, if any.
        service: Its Compose service, if any.
        state: Docker's state word: ``running``, ``exited``...
        env_names: The names of the variables of its environment.
        settings: The values of the variables that are not secrets: who
            signs in, which database (:data:`_SETTING_NAMES`).
        published: The ports it publishes.
        exposed: The ports the image exposes.
        official: Whether the image is a Docker Hub official one.
        app: The domain of the application it belongs to, once known.
        command_password: A ``--requirepass`` given on Redis's command line:
            already in ``ps`` on the host, and only ever passed on as an
            environment variable. Never rendered.
        command_password_variable: The variable ``--requirepass`` names
            instead, when the command line says ``$NAME``.
    """

    key: str
    engine: str
    flavour: Flavour
    container: str
    container_id: str
    image: str
    project: str | None
    service: str | None
    state: str
    env_names: frozenset[str] = frozenset()
    settings: Mapping[str, str] = field(default_factory=dict)
    published: tuple[PublishedPort, ...] = ()
    exposed: tuple[int, ...] = ()
    official: bool = True
    app: str | None = None
    command_password: str | None = field(default=None, repr=False)
    command_password_variable: str | None = None

    # ---------------------------------------------------------------- state

    @property
    def running(self) -> bool:
        """Whether the container is running."""
        return self.state == "running"

    @property
    def default_port(self) -> int:
        """The port the engine listens on inside its container."""
        engine_port = ENGINE_PORTS.get(self.engine, 0)
        if engine_port in self.exposed or not self.exposed:
            return engine_port
        return self.exposed[0]

    @property
    def host_port(self) -> int | None:
        """The host port the engine's port is published on, if it is."""
        for port in self.published:
            if port.container_port == self.default_port:
                return port.host_port
        return None

    @property
    def port(self) -> int:
        """The port a client on the host reaches it on, else its own port."""
        return self.host_port or self.default_port

    # ------------------------------------------------------------ signing in

    def _has(self, *names: str) -> bool:
        """
        Tell whether any of these variables, or their ``*_FILE`` form, is set.

        Args:
            *names: Variable names.

        Returns:
            True when one is in the environment.
        """
        return any(name in self.env_names or f"{name}_FILE" in self.env_names for name in names)

    def _setting(self, *names: str) -> str:
        """
        Read the first of several non-secret variables that has a value.

        Args:
            *names: Variable names, in order of preference.

        Returns:
            The value, or an empty string.
        """
        for name in names:
            value = self.settings.get(name)
            if value:
                return value
        return ""

    @property
    def admin_user(self) -> str:
        """
        The account Noust signs in as.

        Postgres: ``POSTGRES_USER`` (the image's superuser) or ``postgres``;
        Bitnami's superuser when its password is set. MySQL and MariaDB:
        ``root`` when the root password is known or empty, else the
        application's user. MongoDB: the root user the image created, or
        none on a server without authentication. Redis: none.
        """
        if self.engine == "postgresql":
            if not self.official and self._has("POSTGRESQL_POSTGRES_PASSWORD"):
                return "postgres"
            return self._setting("POSTGRES_USER", "POSTGRESQL_USERNAME") or "postgres"
        if self.engine == "mysql":
            if self._root_reachable():
                return "root"
            return self._setting("MARIADB_USER", "MYSQL_USER") or "root"
        if self.engine == "mongodb":
            if self._has(*PASSWORD_SOURCES["mongo"]):
                return self._setting("MONGO_INITDB_ROOT_USERNAME", "MONGODB_ROOT_USER") or "root"
            return ""
        return ""

    def _root_reachable(self) -> bool:
        """
        Tell whether a MySQL-family image lets root in with what it carries.

        Returns:
            True when the root password is in the environment or root's is
            empty by the image's own switch.
        """
        if self._has("MYSQL_ROOT_PASSWORD", "MARIADB_ROOT_PASSWORD"):
            return True
        return any(
            self.settings.get(name, "").lower() not in ("", "no", "false", "0")
            for name in (
                "MYSQL_ALLOW_EMPTY_PASSWORD",
                "MARIADB_ALLOW_EMPTY_ROOT_PASSWORD",
                "ALLOW_EMPTY_PASSWORD",
            )
        )

    @property
    def access(self) -> Access:
        """
        How much the credentials the container carries let Noust do.

        ``limited`` when Noust can only sign in as the application's own
        account (a MySQL whose root password is random, a Bitnami Postgres
        without its superuser's password): it lists and dumps what that
        account sees, and cannot create accounts.
        """
        if self.engine == "mysql":
            return "full" if self._root_reachable() else "limited"
        if self.engine == "postgresql" and not self.official:
            return "full" if self.admin_user == "postgres" else "limited"
        return "full"

    def launch(self, program: str) -> Launch:
        """
        Say how a client signs in, for :data:`CLIENT_SCRIPT`.

        Args:
            program: The client being started (``psql``, ``mongodump``...).

        Returns:
            Where its password is and what it is handed to.
        """
        if self.engine == "postgresql":
            table = (
                "postgres-bitnami-superuser"
                if not self.official and self.admin_user == "postgres"
                else "postgres"
            )
            return Launch("env", "PGPASSWORD", PASSWORD_SOURCES[table])
        if self.engine == "mysql":
            family = "mariadb" if self.flavour == "mariadb" else "mysql"
            kind = "root" if self.admin_user == "root" else "user"
            return Launch("env", "MYSQL_PWD", PASSWORD_SOURCES[f"{family}-{kind}"])
        if self.engine == "redis":
            variable = self.command_password_variable
            sources = PASSWORD_SOURCES["redis"]
            return Launch("env", "REDISCLI_AUTH", (variable, *sources) if variable else sources)
        if self.engine == "mongodb" and program in ("mongodump", "mongorestore"):
            return Launch("mongo", self.admin_user, PASSWORD_SOURCES["mongo"])
        return Launch()

    def exec_user(self, user: str | None) -> str | None:
        """
        Decide the ``-u`` of a ``docker exec`` for a host-side ``user=``.

        The official Postgres image has the ``postgres`` account the host's
        peer authentication runs as; Bitnami's images run as an account with
        no name, so a command there runs as the image's own user.

        Args:
            user: The account the host-side command would run as.

        Returns:
            The account to pass to ``-u``, or None.
        """
        if user is None or not self.official:
            return None
        return user

    def exec_argv(
        self,
        argv: Sequence[str],
        *,
        user: str | None = None,
        env: Mapping[str, str] | None = None,
        interactive: bool = False,
    ) -> list[str]:
        """
        Turn a client command into the ``docker exec`` that runs it here.

        ``docker exec -i [-u user] [-e NAME ...] <container> sh -c
        CLIENT_SCRIPT sh <mode> <target> <sources> <programs> <args...>``:

        - ``user`` becomes ``-u`` (the host's ``runuser``).
        - Every variable of ``env`` is forwarded by name, ``-e NAME`` with no
          value, so Docker reads it from its own environment: the value is in
          no argv. A secret the caller hands over this way wins over the
          container's: the sources are left empty when ``env`` already sets
          the password variable.
        - The non-secret defaults a client needs to sign in as the image's
          own account (``PGUSER``) are given as ``-e NAME=value``.

        Args:
            argv: The client command, program first, as it would run on the
                host.
            user: The account it would run as on the host.
            env: The variables the caller passes to it.
            interactive: Give the client a terminal (``-it``), for a session
                the operator types into.

        Returns:
            The argv to run on the host.
        """
        program = PurePosixPath(str(argv[0])).name
        launch = self.launch(program)
        provided = dict(env or {})
        candidates = "" if launch.target in provided else ",".join(launch.candidates)
        command = ["docker", "exec", "-it" if interactive else "-i"]
        exec_user = self.exec_user(user)
        if exec_user:
            command += ["-u", exec_user]
        for name in provided:
            command += ["-e", name]
        if self.engine == "postgresql" and "PGUSER" not in provided:
            command += ["-e", f"PGUSER={self.admin_user}"]
        command.append(self.container)
        return [
            *command,
            "sh",
            "-c",
            CLIENT_SCRIPT,
            "sh",
            launch.mode,
            launch.target,
            candidates,
            PROGRAM_NAMES.get(program, program),
            *[str(part) for part in argv[1:]],
        ]

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and the CLI, without a single value of its environment.

        Returns:
            ``key``, ``engine``, ``flavour``, ``container``, ``project``,
            ``service``, ``image``, ``state``, ``running``, ``port``, ``app``
            and ``access``.
        """
        return {
            "key": self.key,
            "engine": self.engine,
            "flavour": self.flavour,
            "container": self.container,
            "project": self.project,
            "service": self.service,
            "image": self.image,
            "state": self.state,
            "running": self.running,
            "port": self.port,
            "app": self.app,
            "access": self.access,
        }


# ==================== Discovery ====================


def _labels(value: object) -> dict[str, str]:
    """
    Read a container's labels.

    Args:
        value: ``Config.Labels`` of ``docker inspect``.

    Returns:
        The labels, as strings.
    """
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def _environment(value: object) -> tuple[frozenset[str], dict[str, str]]:
    """
    Reduce a container's environment to its names and its non-secret values.

    Args:
        value: ``Config.Env`` of ``docker inspect``: ``NAME=value`` strings.

    Returns:
        Every name, and the values of :data:`_SETTING_NAMES`.
    """
    names: set[str] = set()
    settings: dict[str, str] = {}
    if isinstance(value, list):
        for item in value:
            name, separator, setting = str(item).partition("=")
            if not separator or not name:
                continue
            names.add(name)
            if name in _SETTING_NAMES:
                settings[name] = setting
    return frozenset(names), settings


def _ports(raw: object) -> list[PublishedPort]:
    """
    Read the published ports of ``NetworkSettings.Ports`` or ``HostConfig.PortBindings``.

    Args:
        raw: The mapping of ``"5432/tcp"`` to its host bindings.

    Returns:
        One entry per TCP binding.
    """
    found: list[PublishedPort] = []
    if not isinstance(raw, Mapping):
        return found
    for spec, bindings in raw.items():
        port, _, protocol = str(spec).partition("/")
        if protocol not in ("", "tcp") or not port.isdigit() or not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, Mapping):
                continue
            host_port = str(binding.get("HostPort") or "")
            if not host_port.isdigit():
                continue
            found.append(
                PublishedPort(
                    container_port=int(port),
                    host_ip=str(binding.get("HostIp") or "0.0.0.0"),  # noqa: S104 - reporting
                    host_port=int(host_port),
                )
            )
    return found


def _exposed(raw: object) -> tuple[int, ...]:
    """
    Read ``Config.ExposedPorts``.

    Args:
        raw: The mapping of ``"5432/tcp"`` to an empty object.

    Returns:
        The TCP ports, sorted.
    """
    if not isinstance(raw, Mapping):
        return ()
    ports = set()
    for spec in raw:
        port, _, protocol = str(spec).partition("/")
        if port.isdigit() and protocol in ("", "tcp"):
            ports.add(int(port))
    return tuple(sorted(ports))


def _requirepass(command: object) -> tuple[str | None, str | None]:
    """
    Find a ``--requirepass`` on a Redis container's command line.

    Args:
        command: ``Config.Cmd`` (and ``Config.Entrypoint``): a list, possibly
            ``sh -c "redis-server --requirepass ..."``.

    Returns:
        The password, or the variable it names (``$REDIS_PASSWORD``); one of
        the two, or neither.
    """
    if not isinstance(command, list):
        return None, None
    words: list[str] = []
    for part in command:
        text = str(part)
        if " " in text:
            try:
                words.extend(shlex.split(text))
            except ValueError:
                words.extend(text.split())
        else:
            words.append(text)
    for position, word in enumerate(words):
        value: str | None = None
        if word == "--requirepass" and position + 1 < len(words):
            value = words[position + 1]
        elif word.startswith("--requirepass="):
            value = word.partition("=")[2]
        if value is None:
            continue
        reference = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", value)
        if reference:
            return None, reference.group(1)
        return (value or None), None
    return None, None


def _instance(entry: Mapping[str, Any]) -> DatabaseInstance | None:
    """
    Build an instance from one object of ``docker inspect``.

    Args:
        entry: The container, as inspect describes it.

    Returns:
        The instance, or None when its image is not a database's or its
        names cannot form a key.
    """
    raw_config = entry.get("Config")
    config: Mapping[str, Any] = raw_config if isinstance(raw_config, Mapping) else {}
    image = str(config.get("Image") or entry.get("Image") or "")
    match = image_engine(image)
    if match is None:
        return None
    name = str(entry.get("Name") or "").lstrip("/")
    labels = _labels(config.get("Labels"))
    project = labels.get(PROJECT_LABEL) or None
    service = labels.get(SERVICE_LABEL) or None
    try:
        if project and service and _PROJECT.match(project) and _NAME.match(service):
            key = instance_key(match.engine, project=project, service=service)
        else:
            project = service = None
            key = instance_key(match.engine, container=name)
    except ValidationError:
        return None
    state_raw = entry.get("State")
    state = str(state_raw.get("Status") or "") if isinstance(state_raw, Mapping) else ""
    network = entry.get("NetworkSettings")
    host_config = entry.get("HostConfig")
    published = _ports(network.get("Ports") if isinstance(network, Mapping) else None)
    if not published and isinstance(host_config, Mapping):
        published = _ports(host_config.get("PortBindings"))
    env_names, settings = _environment(config.get("Env"))
    password, variable = (None, None)
    if match.engine == "redis":
        command = [*(config.get("Entrypoint") or []), *(config.get("Cmd") or [])]
        password, variable = _requirepass(command)
    return DatabaseInstance(
        key=key,
        engine=match.engine,
        flavour=match.flavour,
        container=name,
        container_id=str(entry.get("Id") or ""),
        image=image,
        project=project,
        service=service,
        state=state.lower(),
        env_names=env_names,
        settings=settings,
        published=tuple(published),
        exposed=_exposed(config.get("ExposedPorts")),
        official=match.official,
        command_password=password,
        command_password_variable=variable,
    )


def _candidates(stdout: str) -> list[str]:
    """
    Pick the containers worth inspecting from ``docker ps``.

    Args:
        stdout: ``docker ps -a --no-trunc --format '{{.ID}}\\t{{.Image}}'``.

    Returns:
        The ids whose image is a database's, or is an id ``docker ps``
        printed because the image lost its tag (inspect still knows the
        reference it was created from).
    """
    ids: list[str] = []
    for line in stdout.splitlines():
        container_id, _, image = line.strip().partition("\t")
        if not container_id:
            continue
        if image_engine(image) is not None or _IMAGE_ID.match(image.strip()):
            ids.append(container_id)
    return ids


def discover(runner: CommandRunner | None = None) -> list[DatabaseInstance]:
    """
    Find the database containers on this server, running and stopped.

    Args:
        runner: The runner to ask through; the process-wide one when None.

    Returns:
        One instance per key, the running container first when a service
        has several (a scaled service, a leftover). Empty without Docker.

    Raises:
        RoleError: On a hub, which runs no databases.
        DatabaseQueryError: When Docker is installed and does not answer,
            with its own words: an empty list would read as "no databases".
    """
    require_server_role("Databases")
    active = runner or get_runner()
    if not active.exists("docker"):
        return []
    listed = active.run(
        ["docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}}\t{{.Image}}"],
        timeout=DOCKER_TIMEOUT,
    )
    if not listed.success:
        raise DatabaseQueryError(
            "Docker did not list its containers, so the databases they run cannot be shown",
            details="Check the daemon: systemctl status docker",
            output=(listed.stderr or listed.stdout).strip(),
        )
    ids = _candidates(listed.stdout)
    if not ids:
        return []
    inspected = active.run(["docker", "inspect", *ids], timeout=DOCKER_TIMEOUT)
    # A container removed between the two commands makes inspect fail for it
    # alone while it still prints the others, so only a failure that printed
    # nothing is one.
    if not inspected.success and not inspected.stdout.strip():
        raise DatabaseQueryError(
            "Docker did not describe its database containers",
            details="Check the daemon: systemctl status docker",
            output=(inspected.stderr or inspected.stdout).strip(),
        )
    try:
        entries = json.loads(inspected.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise DatabaseQueryError(
            "Docker described its containers in a form Noust cannot read",
            details="Run 'docker inspect' on one of them to see what it prints.",
            output=(inspected.stderr or str(exc)).strip(),
        ) from exc
    found: dict[str, DatabaseInstance] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        instance = _instance(entry)
        if instance is None:
            continue
        current = found.get(instance.key)
        if current is None or (instance.running and not current.running):
            found[instance.key] = instance
    return sorted(found.values(), key=lambda item: item.key)


def assign_apps(
    instances: Iterable[DatabaseInstance], apps: Iterable[Any]
) -> list[DatabaseInstance]:
    """
    Say which application each instance belongs to.

    The Compose project of the container is an application's
    ``compose_project``, or its ``app_name`` (the domain with dashes):
    ``empleo-arennalabs-com`` is the database of empleo.arennalabs.com even
    when that application is not itself a Compose stack.

    Args:
        instances: The instances.
        apps: The store's applications (anything with ``domain`` and
            ``compose_project``).

    Returns:
        The instances, each with ``app`` set when one matched.
    """
    by_project: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for app in apps:
        domain = getattr(app, "domain", None)
        if not domain:
            continue
        project = getattr(app, "compose_project", None)
        if project:
            by_project.setdefault(str(project), str(domain))
        by_name.setdefault(domain_to_app_name(str(domain)), str(domain))
    owned: list[DatabaseInstance] = []
    for instance in instances:
        domain = None
        if instance.project:
            domain = by_project.get(instance.project) or by_name.get(instance.project)
        owned.append(replace(instance, app=domain) if domain else instance)
    return owned
