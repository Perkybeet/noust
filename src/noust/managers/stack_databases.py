# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The databases a Docker Compose stack runs, copied before the stack is updated.

Going back to the previous containers puts the previous *code* back; a
migration the failed attempt ran stays in the database. Without a dump taken
first there is nothing to go back to, and the files-only backup an update took
until 3.1 held a tarball of a live database's data directory at best, which is
not a copy anyone can restore. This module finds the databases, dumps each one
through the engine's own client inside its container, and puts a dump back.

**What is a database.** The services whose image is the official ``postgres``,
``mysql``, ``mariadb`` or ``mongo`` (any tag, ``-alpine`` included). The
user and the database come from the service's environment *as Compose
resolved it* (``docker compose config --format json``), so a ``${DB_USER}``
filled in from ``.env`` is the value the container really has. Anything else
(``bitnami/postgresql``, ``postgis/postgis``) is declared by the operator in
``noust.yaml`` (``backup.databases``); a declaration replaces detection, in the
same way the operator's hooks replace the repository's.

**Where a password goes.** Nowhere Noust can leak it. The dump runs as
``docker compose exec -T <service> sh -c <fixed script> ...`` and the script
reads the password from the container's *own* environment (``POSTGRES_PASSWORD``,
or the file ``POSTGRES_PASSWORD_FILE`` names) and hands it to the client the way
that client takes a secret outside a command line (``PGPASSWORD``,
``MYSQL_PWD``, or a ``0600`` config file for ``mongodump``). So the value is in
no argv on the host, in none in the container, and in no environment Noust
builds. The script itself is a constant; every value that varies reaches it as a
positional parameter, never spliced into the text.

**What a failure does.** A dump that cannot be taken raises
:class:`StackBackupError`, which carries the engine's own words and the command
that switches the copy off. The caller (the pre-update backup) lets it stop the
update: without a copy there is no way back from a migration, which is the one
thing the copy is for.

Every process goes through the :class:`~noust.core.runner.CommandRunner` of the
stack's deployer, so ``--dry-run`` and the tests see all of it.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import tarfile
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, get_args

import yaml

from noust.central import require_server_role
from noust.core import audit
from noust.core.exceptions import (
    BackupError,
    DeploymentError,
    NoustError,
    ServiceError,
    ValidationError,
)
from noust.core.runner import CommandResult, CommandRunner
from noust.core.store import AppType, get_store
from noust.validators.names import validate_filename


def _config_probes() -> tuple[tuple[object, ...], ...]:
    """
    The exact shapes of ``docker compose ... config --format json`` this module runs.

    Reading a stack's resolved configuration changes nothing, so a dry run may
    run it to say which databases an update would dump. The shapes are spelled
    out because a probe only matches a trailing ``...``, and ``-p`` and the
    profiles sit in the middle of the command.

    Returns:
        One shape per combination of an optional ``-p`` and up to three profiles.
    """
    shapes: list[tuple[object, ...]] = []
    for project in ((), ("-p", "*")):
        for profiles in range(4):
            shapes.append(
                (
                    "docker",
                    "compose",
                    *project,
                    "-f",
                    "*",
                    *(("--profile", "*") * profiles),
                    "config",
                    "--format",
                    "json",
                )
            )
    return tuple(shapes)


#: Read by :mod:`noust.core.runner` (listed in its ``PROBE_MODULES``).
READ_ONLY_PROBES: tuple[tuple[object, ...], ...] = _config_probes()

__all__ = [
    "AUTO",
    "CLIENT_SCRIPT",
    "DUMP_TIMEOUT",
    "ENGINES",
    "OFF",
    "Action",
    "DeclaredDatabase",
    "DeclaredSetting",
    "Engine",
    "StackBackupError",
    "StackDatabase",
    "StackHost",
    "client_command",
    "compose_volume_names",
    "compose_volumes_from_disk",
    "declared_databases",
    "detect_stack_databases",
    "dump_stack_database",
    "dump_stack_databases",
    "extract_stack_dumps",
    "image_engine",
    "read_compose_config",
    "restore_stack_database",
    "set_backup_before_update",
    "stack_dump_filename",
    "stack_host_for",
    "volume_names_from_compose_file",
]

Engine = Literal["postgres", "mysql", "mariadb", "mongo"]

#: Every engine a dump is known for, in the order they are documented.
ENGINES: tuple[str, ...] = get_args(Engine)

#: The engines by name, which is how a name read from a file becomes an :data:`Engine`.
_ENGINE_NAMES: dict[str, Engine] = {name: name for name in get_args(Engine)}

#: ``backup.databases`` values that are not a list.
AUTO = "auto"
OFF = "off"

#: Seconds a dump may take. A database of tens of gigabytes is a legitimate
#: input; what a stuck client must not do is hold an update forever.
DUMP_TIMEOUT = 3600

#: Seconds a restore may take.
RESTORE_TIMEOUT = 7200

#: Seconds for the commands that only look or start a container.
COMPOSE_TIMEOUT = 300

#: Seconds ``docker compose config`` gets to resolve the stack.
CONFIG_TIMEOUT = 60

#: How long a restore waits for the engine to accept connections after its
#: container started, as attempts and seconds between them.
READY_ATTEMPTS = 30
READY_INTERVAL = 2.0

#: Registries that are Docker Hub under another spelling.
_HUB_REGISTRIES = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})

#: The repository an official image is published under.
_OFFICIAL_IMAGES: dict[str, Engine] = {
    "postgres": "postgres",
    "mysql": "mysql",
    "mariadb": "mariadb",
    "mongo": "mongo",
}

#: What a user or database name may be: never anything a client could read as
#: an option (a leading dash), a path or a shell word.
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@$-]{0,127}$")

#: What a Compose service is called.
_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

#: Where each engine's superuser password comes from when the user is root or
#: ``postgres``, and where the others' do. Names Noust writes, never names read
#: from a repository: the script that resolves them evaluates them.
_PASSWORD_SOURCES: dict[str, tuple[str, ...]] = {
    "postgres": ("POSTGRES_PASSWORD", "POSTGRESQL_PASSWORD", "POSTGRESQL_POSTGRES_PASSWORD"),
    "mysql-user": ("MYSQL_PASSWORD",),
    "mysql-root": ("MYSQL_ROOT_PASSWORD",),
    "mariadb-user": ("MARIADB_PASSWORD", "MYSQL_PASSWORD"),
    "mariadb-root": ("MARIADB_ROOT_PASSWORD", "MYSQL_ROOT_PASSWORD"),
    "mongo": ("MONGO_INITDB_ROOT_PASSWORD",),
}

#: The one script every client runs through, inside the container. Positional
#: parameters: the mode (``env`` or ``mongo``), the variable to hand the
#: password to (or the mongo user), the comma-separated environment variables
#: the password may be in, the comma-separated program names to try, then the
#: program's own arguments. Names reach ``eval`` only from the constants above.
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


class StackBackupError(BackupError):
    """
    A stack's database could not be copied.

    The message says which one; ``output`` is what the engine or Docker said,
    verbatim; ``details`` says how to carry on without the copy.
    """


@dataclass(frozen=True)
class StackDatabase:
    """
    One database a Compose service runs.

    Attributes:
        service: The Compose service whose container holds it.
        engine: Which engine, and so which client dumps and restores it.
        database: The database name. Empty means every database the server has
            (MySQL and MariaDB without ``MYSQL_DATABASE``, MongoDB without
            ``MONGO_INITDB_DATABASE``).
        user: The account the client connects as. Empty only for a MongoDB that
            runs without authentication.
    """

    service: str
    engine: Engine
    database: str
    user: str

    @property
    def label(self) -> str:
        """How the operator is told about it: ``postgres/proggest (service postgres)``."""
        return f"{self.engine}/{self.database or 'all databases'} (service {self.service})"

    def to_entry(self) -> dict[str, str]:
        """
        Describe it for a backup manifest.

        Returns:
            What :meth:`from_entry` reads back.
        """
        return {
            "service": self.service,
            "engine": self.engine,
            "database": self.database,
            "user": self.user,
        }

    @classmethod
    def from_entry(cls, entry: Mapping[str, Any]) -> StackDatabase | None:
        """
        Read back what :meth:`to_entry` wrote.

        Args:
            entry: The ``stack`` object of a manifest's database entry.

        Returns:
            The database, or None when the entry is not one a restore can use
            (a manifest from a hand-edited or newer archive).
        """
        service, name = entry.get("service"), entry.get("engine")
        database, user = entry.get("database", ""), entry.get("user", "")
        engine = _ENGINE_NAMES.get(name) if isinstance(name, str) else None
        if not (isinstance(service, str) and _SERVICE.match(service)) or engine is None:
            return None
        if not isinstance(database, str) or not isinstance(user, str):
            return None
        if (database and not _NAME.match(database)) or (user and not _NAME.match(user)):
            return None
        return cls(service, engine, database, user)


class DeclaredDatabase(Protocol):
    """
    One entry of ``backup.databases`` in a project file, as far as this module reads it.

    A structural type, so the project file's own entry class satisfies it with
    no import in either direction.
    """

    @property
    def service(self) -> str:
        """The Compose service."""
        ...

    @property
    def engine(self) -> str:
        """The engine name."""
        ...

    @property
    def database(self) -> str | None:
        """The database, or None to take it from the service's environment."""
        ...

    @property
    def user(self) -> str | None:
        """The account, or None to take it from the service's environment."""
        ...


#: What ``backup.databases`` holds: ``"auto"``, ``"off"``, or the entries that
#: replace detection.
DeclaredSetting = str | Sequence[DeclaredDatabase]


class StackHost(Protocol):
    """
    What this module needs of the deployer that runs a stack.

    :class:`~noust.deployers.docker_compose.DockerComposeDeployer` satisfies it.
    """

    domain: str
    app_path: Path

    @property
    def runner(self) -> CommandRunner:
        """The runner every process goes through."""
        ...

    def _compose(self, *args: str, project: str | None = None) -> list[str]:
        """The ``docker compose`` argv for this stack, with its project and file."""
        ...


def switch_off_hint(domain: str) -> str:
    """
    Say how to update without the copy.

    Args:
        domain: The application's domain.

    Returns:
        A sentence naming the command.
    """
    return (
        f"To update {domain} without a copy of its databases, run: "
        f"noust app backup-before-update {domain} off. "
        "To keep the copy, fix what the output above says and run the update again."
    )


def image_engine(image: str | None) -> Engine | None:
    """
    Tell whether an image reference is an official database image.

    Args:
        image: What ``image:`` says: ``postgres:16-alpine``,
            ``docker.io/library/mysql``, ``bitnami/postgresql``.

    Returns:
        The engine, or None for any other image.
    """
    if not image or not isinstance(image, str):
        return None
    reference = image.split("@", 1)[0]
    parts = reference.split("/")
    first = parts[0]
    registry = None
    # A first component is a registry when it names a host: a dot, a port or localhost.
    if len(parts) > 1 and ("." in first or ":" in first or first == "localhost"):
        registry, parts = first, parts[1:]
    if registry is not None and registry not in _HUB_REGISTRIES:
        return None
    if len(parts) == 2 and parts[0] == "library":
        parts = parts[1:]
    if len(parts) != 1:
        return None
    repository = parts[0].split(":", 1)[0]
    return _OFFICIAL_IMAGES.get(repository)


def _environment(service: Mapping[str, Any]) -> dict[str, str]:
    """
    Read a service's environment, whichever shape Compose gave it.

    Args:
        service: One entry of ``services``.

    Returns:
        The variables that have a value.
    """
    raw = service.get("environment")
    if isinstance(raw, Mapping):
        return {str(key): str(value) for key, value in raw.items() if value is not None}
    found: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            key, separator, value = str(item).partition("=")
            if separator:
                found[key] = value
    return found


def _first(environment: Mapping[str, str], *names: str) -> str:
    """
    Return the first of several variables that has a value.

    Args:
        environment: The service's environment.
        names: Variable names, in order of preference.

    Returns:
        The value, or an empty string.
    """
    for name in names:
        if environment.get(name):
            return environment[name]
    return ""


def _credentials(engine: Engine, environment: Mapping[str, str]) -> tuple[str, str]:
    """
    Work out who connects and to what, from an official image's variables.

    Args:
        engine: The engine of the image.
        environment: The service's resolved environment.

    Returns:
        The user and the database; either may be empty (see :class:`StackDatabase`).
    """
    if engine == "postgres":
        user = environment.get("POSTGRES_USER") or "postgres"
        return user, environment.get("POSTGRES_DB") or user
    if engine == "mongo":
        return (
            environment.get("MONGO_INITDB_ROOT_USERNAME", ""),
            environment.get("MONGO_INITDB_DATABASE", ""),
        )
    prefixes = ("MARIADB_", "MYSQL_") if engine == "mariadb" else ("MYSQL_",)
    user = _first(environment, *(f"{prefix}USER" for prefix in prefixes))
    database = _first(environment, *(f"{prefix}DATABASE" for prefix in prefixes))
    # An application user with no database has no privileges to dump with.
    if user and database:
        return user, database
    return "root", database


def _checked_name(value: str, what: str, service: str) -> str:
    """
    Refuse a name a client could read as an option.

    Args:
        value: The user or database name.
        what: ``user`` or ``database``, for the message.
        service: The service it belongs to.

    Returns:
        The name, unchanged.

    Raises:
        StackBackupError: The name is not one Noust passes to a client.
    """
    if value and not _NAME.match(value):
        raise StackBackupError(
            f"The {what} name {value!r} of service {service!r} is not one Noust passes to a client",
            details=f"Use letters, digits and _ . @ $ -, starting with a letter or digit, in the "
            f"{what} of backup.databases in noust.yaml.",
        )
    return value


def _declared_database(
    declared: DeclaredDatabase, services: Mapping[str, Any], config_name: str
) -> StackDatabase:
    """
    Turn one ``backup.databases`` entry into a database to dump.

    Args:
        declared: The entry.
        services: The ``services`` of the resolved configuration.
        config_name: What the configuration is called, for the message.

    Returns:
        The database; what the entry leaves out is read from the service.

    Raises:
        StackBackupError: The engine is unknown or the service does not exist.
    """
    engine = _ENGINE_NAMES.get(declared.engine)
    if engine is None:
        raise StackBackupError(
            f"Engine {declared.engine!r} of backup.databases is not one Noust can dump",
            details=f"Use one of: {', '.join(ENGINES)}. For any other database, back it up "
            "with a pre_deploy hook of your own.",
        )
    if declared.service not in services:
        raise StackBackupError(
            f"backup.databases names service {declared.service!r}, which {config_name} does not have",
            details=f"Services: {', '.join(sorted(services)) or 'none'}. Fix the service in "
            "noust.yaml, or remove the entry.",
        )
    user, database = _credentials(engine, _environment(services[declared.service]))
    chosen_user = _checked_name(declared.user or user, "user", declared.service)
    chosen_database = _checked_name(declared.database or database, "database", declared.service)
    return StackDatabase(declared.service, engine, chosen_database, chosen_user)


def detect_stack_databases(
    compose_config: Mapping[str, Any], declared: DeclaredSetting = AUTO
) -> list[StackDatabase]:
    """
    Find the databases a stack runs.

    Args:
        compose_config: The stack as ``docker compose config --format json``
            prints it, so every variable is already resolved.
        declared: ``backup.databases`` of the project file: ``"auto"`` to
            detect the official images, ``"off"`` for none, or the entries
            that replace detection.

    Returns:
        The databases, in the order the stack declares their services.

    Raises:
        StackBackupError: A declaration names an unknown engine or service, or
            a user or database that is not a plain name.
    """
    if declared == OFF:
        return []
    services = compose_config.get("services")
    services = services if isinstance(services, Mapping) else {}
    if declared != AUTO and not isinstance(declared, str):
        return [_declared_database(entry, services, "the stack") for entry in declared]

    found: list[StackDatabase] = []
    for name, service in services.items():
        if not isinstance(service, Mapping) or not _SERVICE.match(str(name)):
            continue
        engine = image_engine(service.get("image"))
        if engine is None:
            continue
        user, database = _credentials(engine, _environment(service))
        if (user and not _NAME.match(user)) or (database and not _NAME.match(database)):
            # Not something Noust will pass to a client; the operator declares it.
            continue
        found.append(StackDatabase(str(name), engine, database, user))
    return found


def compose_volume_names(compose_config: Mapping[str, Any]) -> list[str]:
    """
    List the named volumes of a stack as Docker knows them.

    Args:
        compose_config: The stack as ``docker compose config --format json``
            prints it, where every volume already carries its resolved name.

    Returns:
        The volume names: ``<project>_<key>`` unless the volume names itself.
    """
    volumes = compose_config.get("volumes")
    if not isinstance(volumes, Mapping):
        return []
    project = str(compose_config.get("name") or "")
    names: list[str] = []
    for key, spec in volumes.items():
        explicit = spec.get("name") if isinstance(spec, Mapping) else None
        names.append(str(explicit) if explicit else (f"{project}_{key}" if project else str(key)))
    return names


def volume_names_from_compose_file(document: Mapping[str, Any], project: str | None) -> list[str]:
    """
    List the named volumes a compose file declares, without asking Docker.

    The way back for a stack whose ``docker compose config`` fails (a variable
    it requires is missing): the file says the same thing, less conveniently.

    Args:
        document: The parsed compose file.
        project: The Compose project name the stack runs as.

    Returns:
        The volume names Docker would create or use.
    """
    volumes = document.get("volumes")
    if not isinstance(volumes, Mapping):
        return []
    names: list[str] = []
    for key, spec in volumes.items():
        spec = spec if isinstance(spec, Mapping) else {}
        external = spec.get("external")
        if spec.get("name"):
            names.append(str(spec["name"]))
        elif isinstance(external, Mapping) and external.get("name"):
            names.append(str(external["name"]))
        elif external or not project:
            names.append(str(key))
        else:
            names.append(f"{project}_{key}")
    return names


def stack_dump_filename(database: StackDatabase) -> str:
    """
    Name the file a database's dump is written to.

    Args:
        database: The database.

    Returns:
        A plain file name, unique per service, engine and database.
    """
    suffix = {"postgres": "dump", "mongo": "archive"}.get(database.engine, "sql")
    name = database.database or "all"
    return f"stack-{database.service}-{database.engine}-{name}.{suffix}.gz"


#: What a client is asked to do.
Action = Literal["dump", "restore", "ping"]


def client_command(database: StackDatabase, action: Action) -> list[str]:
    """
    Build the command that runs one client inside a database's container.

    The result is ``sh -c <fixed script> sh <mode> <target> <sources>
    <programs> <arguments...>``: the same script for every engine and action,
    and every value that varies as a positional parameter. The script finds the
    password in the container's environment and hands it to the client; see the
    module docstring.

    Args:
        database: The database to act on.
        action: ``dump``, ``restore`` or ``ping`` (is the engine accepting
            connections).

    Returns:
        The argv to run after ``docker compose exec -T <service>``.
    """
    engine, name, user = database.engine, database.database, database.user
    mode, target, sources = "env", "NOUST_UNUSED", ""
    programs: str
    arguments: list[str]

    if engine == "postgres":
        mode, target, sources = "env", "PGPASSWORD", ",".join(_PASSWORD_SOURCES["postgres"])
        programs = {"dump": "pg_dump", "restore": "pg_restore", "ping": "pg_isready"}[action]
        connection = ["-U", user, "-d", name]
        arguments = {
            "dump": ["-Fc", "-Z", "0", *connection],
            # One transaction: a dump that does not fit leaves the database as it was.
            "restore": ["--single-transaction", "--clean", "--if-exists", *connection],
            "ping": connection,
        }[action]
    elif engine == "mongo":
        if action != "ping":
            mode, target = "mongo", user
            sources = ",".join(_PASSWORD_SOURCES["mongo"])
        programs = {"dump": "mongodump", "restore": "mongorestore", "ping": "mongosh,mongo"}[action]
        arguments = {
            "dump": ["--archive", *(["--db", name] if name else [])],
            "restore": ["--archive", "--drop"],
            "ping": ["--quiet", "--eval", "db.adminCommand('ping')"],
        }[action]
    else:
        if action != "ping":
            kind = "root" if user == "root" else "user"
            mode, target = "env", "MYSQL_PWD"
            sources = ",".join(_PASSWORD_SOURCES[f"{engine}-{kind}"])
        names = {
            "mysql": {"dump": "mysqldump", "restore": "mysql", "ping": "mysqladmin"},
            "mariadb": {
                "dump": "mariadb-dump,mysqldump",
                "restore": "mariadb,mysql",
                "ping": "mariadb-admin,mysqladmin",
            },
        }[engine]
        programs = names[action]
        dump = ["--single-transaction"]
        if engine == "mysql":
            # MySQL 8 asks for PROCESS to dump tablespaces; an application user has not got it.
            dump.append("--no-tablespaces")
        if user == "root":
            dump += ["--routines", "--events"]
        dump += ["-u", user, name or "--all-databases"]
        arguments = {
            "dump": dump,
            "restore": ["-u", user, *([name] if name else [])],
            "ping": ["-u", user, "ping", "--silent"],
        }[action]

    return ["sh", "-c", CLIENT_SCRIPT, "sh", mode, target, sources, programs, *arguments]


def _exec(host: StackHost, database: StackDatabase, action: Action) -> list[str]:
    """
    Build the full command for one client action.

    Args:
        host: The stack's deployer.
        database: The database.
        action: What to do.

    Returns:
        The ``docker compose exec`` argv.
    """
    return [*host._compose("exec", "-T", database.service), *client_command(database, action)]


def _failed(
    what: str, database: StackDatabase, result: CommandResult, advice: str
) -> StackBackupError:
    """
    Describe a command that did not work, with the tool's own words.

    Args:
        what: The sentence that starts the message: ``Could not dump``.
        database: The database the command was about.
        result: What the command returned.
        advice: How to carry on, for ``details``.

    Returns:
        The error to raise.
    """
    reason = result.stderr.strip() or f"exit code {result.exit_code}"
    return StackBackupError(
        f"{what} {database.label}",
        details=advice,
        output=reason,
    )


def dump_stack_database(host: StackHost, database: StackDatabase, dest: Path) -> Path:
    """
    Dump one database into a compressed file.

    The client runs inside the service's container and its output goes
    straight to the file through the runner, so the dump never passes through
    a shell or through Python's memory. The file is created ``0600``.

    Args:
        host: The deployer of the stack.
        database: The database to dump.
        dest: The directory to write into; created when missing.

    Returns:
        The file written, named by :func:`stack_dump_filename`.

    Raises:
        StackBackupError: The dump failed. Nothing is left behind, and the
            error carries the engine's own words and the command that
            switches the copy off.
    """
    target = dest / stack_dump_filename(database)
    result = host.runner.capture_to_file(
        _exec(host, database, "dump"),
        target,
        compress=True,
        cwd=host.app_path,
        timeout=DUMP_TIMEOUT,
    )
    if not result.success:
        raise _failed("Could not dump", database, result, switch_off_hint(host.domain))
    if not target.is_file():
        raise StackBackupError(
            f"The dump of {database.label} did not reach {target}",
            details=switch_off_hint(host.domain),
        )
    return target


def _inflated(dump: Path, workspace: Path) -> Path:
    """
    Give the client a plain copy of a dump that is compressed.

    Args:
        dump: The dump as it was stored; gzip, or not.
        workspace: A private directory to write the copy into.

    Returns:
        The file to feed the client: the dump itself when it is not gzip.
    """
    with dump.open("rb") as stored:
        magic = stored.read(2)
    if magic != b"\x1f\x8b":
        return dump
    plain = workspace / "dump"
    with gzip.open(dump, "rb") as source, plain.open("wb") as sink:
        shutil.copyfileobj(source, sink)
    return plain


def _is_running(host: StackHost, service: str) -> bool:
    """
    Tell whether a service has a running container.

    Args:
        host: The stack's deployer.
        service: The Compose service.

    Returns:
        True when ``docker compose ps -q`` names a container for it.
    """
    result = host.runner.run(
        host._compose("ps", "-q", service), cwd=host.app_path, timeout=COMPOSE_TIMEOUT
    )
    return result.success and bool(result.stdout.strip())


def _wait_until_ready(
    host: StackHost, database: StackDatabase, sleep: Callable[[float], None]
) -> None:
    """
    Wait for an engine to accept connections.

    A container that is "running" is not an engine that is listening: Postgres
    spends seconds replaying its log first, and a restore started then fails.

    Args:
        host: The stack's deployer.
        database: The database whose engine is awaited.
        sleep: Pauses between attempts.

    Raises:
        BackupError: It never answered.
    """
    last = ""
    for attempt in range(READY_ATTEMPTS):
        result = host.runner.run(
            _exec(host, database, "ping"), cwd=host.app_path, timeout=COMPOSE_TIMEOUT
        )
        if result.success:
            return
        last = result.stderr.strip() or result.stdout.strip() or f"exit code {result.exit_code}"
        if attempt + 1 < READY_ATTEMPTS:
            sleep(READY_INTERVAL)
    raise BackupError(
        f"The {database.engine} server of service {database.service} does not accept connections",
        details="Look at its log with: docker compose logs " + database.service,
        output=last,
    )


def restore_stack_database(
    host: StackHost,
    database: StackDatabase,
    dump: Path,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """
    Put a dump back into its service.

    The application must be stopped by the caller: this replaces what the
    database holds (``pg_restore --clean --if-exists`` and its equivalents).
    The service's container is started alone when it is not running, and
    stopped again afterwards, so a restore leaves the stack as it found it.

    Args:
        host: The deployer of the stack.
        database: The database the dump holds.
        dump: The dump file, as :func:`dump_stack_database` wrote it.
        sleep: Pauses while the engine starts; replaceable for tests.

    Raises:
        BackupError: The service could not be started, the engine never
            answered, or the client refused the dump. The engine's words are
            in ``output``.
    """
    was_running = _is_running(host, database.service)
    if not was_running:
        started = host.runner.run(
            host._compose("up", "-d", "--no-deps", database.service),
            cwd=host.app_path,
            timeout=COMPOSE_TIMEOUT,
        )
        if not started.success:
            raise BackupError(
                f"Could not start service {database.service} to restore {database.label}",
                details="Fix what the output says and restore again.",
                output=started.stderr.strip() or f"exit code {started.exit_code}",
            )
    try:
        _wait_until_ready(host, database, sleep)
        with tempfile.TemporaryDirectory(prefix="noust-stack-restore-") as workspace:
            result = host.runner.run(
                _exec(host, database, "restore"),
                cwd=host.app_path,
                stdin_path=_inflated(dump, Path(workspace)),
                timeout=RESTORE_TIMEOUT,
            )
        if not result.success:
            raise BackupError(
                f"Could not restore {database.label}",
                details="The database was not replaced when the client could not read the "
                "whole dump (Postgres restores in one transaction); otherwise check it "
                "before starting the application.",
                output=result.stderr.strip() or f"exit code {result.exit_code}",
            )
    finally:
        if not was_running:
            host.runner.run(
                host._compose("stop", database.service),
                cwd=host.app_path,
                timeout=COMPOSE_TIMEOUT,
            )


def read_compose_config(host: StackHost) -> dict[str, Any]:
    """
    Ask Compose for the stack as it resolves it.

    ``docker compose config --format json`` has every variable interpolated and
    every ``env_file`` merged, so the user and database it shows are the ones the
    container really has. The output holds the stack's secrets: it is parsed
    and dropped, never logged.

    Args:
        host: The deployer of the stack.

    Returns:
        The parsed configuration.

    Raises:
        StackBackupError: Compose could not resolve the stack (a required
            variable is missing, the file is invalid, Docker is down), or what
            it printed is not a configuration.
    """
    result = host.runner.run(
        host._compose("config", "--format", "json"), cwd=host.app_path, timeout=CONFIG_TIMEOUT
    )
    if not result.success:
        raise StackBackupError(
            f"Docker Compose could not resolve the stack of {host.domain}",
            details=switch_off_hint(host.domain),
            output=result.stderr.strip() or f"exit code {result.exit_code}",
        )
    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise StackBackupError(
            f"Docker Compose printed a configuration for {host.domain} that cannot be read",
            details=switch_off_hint(host.domain),
            output=str(exc),
        ) from exc
    if not isinstance(config, dict):
        raise StackBackupError(
            f"Docker Compose printed no configuration for {host.domain}",
            details=switch_off_hint(host.domain),
        )
    return config


def dump_stack_databases(
    host: StackHost,
    destination: Path,
    *,
    archive_dir: str,
    declared: DeclaredSetting = AUTO,
    log: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """
    Dump every database a stack runs, for a backup's manifest.

    Args:
        host: The deployer of the stack.
        destination: The directory the dumps are written into.
        archive_dir: Where that directory is inside the archive, without a
            trailing slash; the manifest's ``archive_path`` is built from it.
        declared: ``backup.databases`` of the project file.
        log: Told about each dump as it starts.

    Returns:
        One manifest entry per database: the keys of the entries the store's
        databases have (``engine``, ``name``, ``archive_path``, ``size_bytes``,
        ``created``) and ``stack``, which says how to put it back.

    Raises:
        StackBackupError: The stack could not be read or a dump failed; see
            :func:`dump_stack_database`. Nothing is partially reported: the
            caller gets all of them or the error.
    """
    if declared == OFF:
        return []
    databases = detect_stack_databases(read_compose_config(host), declared)
    entries: list[dict[str, Any]] = []
    for database in databases:
        if log is not None:
            log(f"Dumping {database.label}")
        written = dump_stack_database(host, database, destination)
        entries.append(
            {
                "engine": database.engine,
                "name": database.database or database.service,
                "archive_path": f"{archive_dir}/{written.name}",
                "size_bytes": written.stat().st_size,
                "created": datetime.now().isoformat(),
                "stack": database.to_entry(),
            }
        )
    return entries


def stack_host_for(
    domain: str,
    app_path: Path,
    *,
    runner: CommandRunner | None = None,
    verbose: bool = False,
) -> StackHost:
    """
    Build the deployer that talks to an application's stack, the way an update does.

    The compose file is the one the deployment chose (named in its unit) or the
    first of the default names; both are read through
    :func:`~noust.deployers.docker_compose.compose_file_in`, which refuses a way
    out of the application directory.

    Args:
        domain: The application's domain.
        app_path: The application's directory, as the store records it.
        runner: The runner every process goes through; the process-wide one
            when None.
        verbose: Verbosity of the deployer.

    Returns:
        A deployer ready to build ``docker compose`` commands.

    Raises:
        StackBackupError: There is no compose file in the application.
    """
    from noust.deployers.docker_compose import (
        COMPOSE_FILE_PRIORITY,
        DockerComposeDeployer,
        compose_file_from_unit,
        compose_file_in,
        compose_file_option,
    )
    from noust.managers.service_manager import ServiceManager

    deployer = DockerComposeDeployer(verbose=verbose, runner=runner)
    deployer.domain = domain
    deployer.app_path = app_path
    deployer.app_name = app_path.name
    try:
        unit_text = ServiceManager(verbose=verbose, runner=runner).get_service_config(app_path.name)
        named = compose_file_from_unit(unit_text)
        candidates = [str(compose_file_option(named))] if named else list(COMPOSE_FILE_PRIORITY)
        for candidate in candidates:
            if (app_path / candidate).exists():
                deployer.compose_path = compose_file_in(app_path, candidate)
                deployer.compose_file = candidate if named else None
                break
        else:
            raise DeploymentError(
                "No Docker Compose file found",
                f"Looked for {', '.join(candidates)} in {app_path}.",
            )
    except (DeploymentError, ServiceError, ValidationError) as exc:
        raise StackBackupError(
            f"Could not find the compose file of {domain}",
            details=switch_off_hint(domain),
            output=str(exc),
        ) from exc
    return deployer


def set_backup_before_update(domain: str, enabled: bool) -> bool:
    """
    Switch the copy of a stack's databases before an update on or off.

    The one place the setting changes, for ``noust app backup-before-update``
    and anything else that offers it. It is a decision about data safety, so it
    is audited with what it was.

    Args:
        domain: The application's domain.
        enabled: True to dump the stack's databases before each update.

    Returns:
        What the setting was.

    Raises:
        NoustError: The application is not deployed.
        ValidationError: The application is not a Docker Compose stack, which
            is the only kind whose update has databases to copy.
    """
    require_server_role("Applications")
    store = get_store()
    app = store.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}",
            details="Run 'noust list' to see what is deployed.",
        )
    if app.app_type != AppType.DOCKER_COMPOSE.value:
        raise ValidationError(
            f"{domain} is not a Docker Compose application",
            details="Only a stack's update copies its databases first; there is nothing here to "
            "switch. Its own databases are backed up with 'noust db backup'.",
            field="domain",
        )
    previous = bool(app.backup_before_update)
    store.set_app_backup_before_update(app.domain, enabled)
    audit.record(
        "apps.backup_before_update",
        target=f"app:{app.domain}",
        details={"enabled": enabled, "previous": previous},
    )
    return previous


def declared_databases(root: Path) -> DeclaredSetting:
    """
    Read ``backup.databases`` of the project file in an application's code.

    Args:
        root: The directory holding the code (``noust.yaml`` lives there).

    Returns:
        ``"auto"`` (the default), ``"off"``, or the declared entries.

    Raises:
        ValidationError: The project file is not valid; its message names the
            field. The same file is refused by a deploy, so an update that
            reaches this point with an invalid one would fail a step later.
    """
    try:
        from noust.deployers.helpers.project_file import load_project_file
    except ImportError:
        # The reader ships with the project file itself; a build without it has
        # no file to read and detection is all there is.
        return AUTO
    return load_project_file(root).backup_databases


def compose_volumes_from_disk(
    app_path: Path, compose_path: Path | None = None, project: str | None = None
) -> list[str]:
    """
    List a stack's named volumes from its compose file, without asking Docker.

    For a stack whose ``docker compose config`` does not resolve: the file
    declares the same volumes, and the project name decides their prefix.

    Args:
        app_path: The application directory.
        compose_path: The compose file in use; the first default name found in
            ``app_path`` when None.
        project: The Compose project the stack runs as, when the store records
            one; otherwise the file's own ``name:``, ``COMPOSE_PROJECT_NAME``
            in its ``.env``, or the one Compose derives from the directory.

    Returns:
        The volume names, or an empty list when there is no readable file.
    """
    from noust.deployers.docker_compose import (
        COMPOSE_FILE_PRIORITY,
        _env_project_name,
        compose_project_name,
    )

    if compose_path is None:
        compose_path = next(
            (app_path / name for name in COMPOSE_FILE_PRIORITY if (app_path / name).is_file()),
            None,
        )
    if compose_path is None:
        return []
    try:
        document = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return []
    if not isinstance(document, Mapping):
        return []
    named = document.get("name")
    chosen = (
        project
        or (str(named) if named else None)
        or compose_project_name(app_path, compose_path)
        or _env_project_name(compose_path)
    )
    return volume_names_from_compose_file(document, chosen)


def extract_stack_dumps(
    archive: Path, names: Sequence[str], staging: Path, *, prefix: str, max_bytes: int
) -> list[Path]:
    """
    Copy only the dump files out of a backup archive.

    The rest of the archive is not unpacked: putting back a database must not
    cost the size of the application's tree. Members are read by the exact name
    the manifest gives, never extracted by path, so nothing the archive says can
    put a file anywhere but ``staging``.

    Args:
        archive: The ``.tar.gz``.
        names: Archive paths of the dumps wanted, as the manifest records them.
        staging: A private directory to copy them into.
        prefix: Where in the archive dumps live; a name outside it is refused.
        max_bytes: The most one dump may hold.

    Returns:
        One file per name, in order.

    Raises:
        BackupError: A dump is missing, is not a regular file, is outside
            ``prefix``, or is larger than ``max_bytes``; or the archive cannot
            be read.
    """
    files: list[Path] = []
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for position, name in enumerate(names):
                if not name.startswith(prefix) or ".." in PurePosixPath(name).parts:
                    raise BackupError(
                        "A database entry of the backup points outside the archive's databases",
                        details=f"Entry: {name!r}. The archive was not written by Noust.",
                    )
                try:
                    member = tar.getmember(name)
                except KeyError as exc:
                    raise BackupError(
                        f"Backup claims a database dump it does not carry: {name}",
                        details="The archive is incomplete; do not trust it as a backup.",
                    ) from exc
                source = tar.extractfile(member) if member.isfile() else None
                if source is None or member.size > max_bytes:
                    raise BackupError(
                        f"The database dump {name} cannot be read from the archive",
                        details="It is not a regular file, or it is larger than "
                        "backup.max_bytes allows.",
                    )
                target = staging / f"{position}-{validate_filename(PurePosixPath(name).name)}"
                with source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                files.append(target)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError(
            f"Cannot read {archive.name}",
            details=f"{exc}. The archive is corrupted or was not written by Noust.",
        ) from exc
    return files
