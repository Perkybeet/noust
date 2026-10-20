# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What another platform's configuration says, in Noust's terms.

Each importer reads the files a platform keeps in the repository and answers
with one :class:`Proposal`: the application type, the commands the platform
ran, the port, the health check, the environment, the database it needs, the
domains, and a warning for everything that has no equivalent here. A
proposal changes nothing; the new-app wizard prefills its review with it and
``noust import --deploy`` hands it to the normal create path through
:func:`noust.deployers.app_export.proposal_document`.

The platform's commands are carried for the operator to compare, not run:
every Noust deployer builds and starts a project with the commands of its type
(``package.json`` scripts, the Python entry point), which is what makes a
redeploy and a rollback behave the same way as the first deploy.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.secret_detection import classify, name_looks_secret
from noust.validators.health import HEALTH_TIMEOUT_MAX, HEALTH_TIMEOUT_MIN

#: Larger than any configuration file a person writes.
MAX_CONFIG_SIZE = 512 * 1024

#: Engines a proposal can say the application needs, as ``noust db`` names them.
DATABASE_ENGINES = ("postgresql", "mysql", "mariadb", "redis", "valkey", "mongodb")

#: Engines Noust runs in a container of its own (``noust db instance create``) and not on the
#: host, so a proposal names them but does not put them in the import document.
INSTANCE_ENGINES = ("keydb", "dragonfly", "clickhouse")


@dataclass
class ProposedEnv:
    """
    One environment variable the platform's configuration declares.

    Attributes:
        name: Variable name.
        value: The default the configuration gives it, or None when it gives
            none (a value kept in the platform's dashboard, a generated one,
            one read from a database).
        secret: Whether it holds a credential: generated, or its name or its value says so.
        generated: The platform generates the value (Render's
            ``generateValue``, Heroku's ``generator: secret``); Noust generates
            a random one in its place.
        required: The application needs a value the configuration does not
            give, and nothing generates.
        note: Where the value came from on the platform, when that matters
            (``from database shop-db``).
    """

    name: str
    value: str | None = None
    secret: bool = False
    generated: bool = False
    required: bool = False
    note: str | None = None


@dataclass(frozen=True)
class RemoteApplication:
    """
    An application on another platform's server, as its API lists it.

    Attributes:
        ref: What names it there (Coolify's uuid, Dokploy's application id).
        name: Its name.
        build: How it is built there (``nixpacks``, ``dockerfile``, ``static``...).
        source: Where its code comes from, as the platform says; empty for none.
        domains: The names it answers on.
        project: The project (and environment) it is in, when the API says.
    """

    ref: str
    name: str
    build: str = ""
    source: str = ""
    domains: tuple[str, ...] = ()
    project: str = ""


@dataclass
class ProposedStorage:
    """
    Data the application kept outside its image on the other platform.

    Attributes:
        mount_path: Where the application saw it, inside its container.
        volume: The Docker volume that held it, when it was a named one.
        host_path: The directory of the other server that held it, when it was a bind mount.
        persistent_path: The path, relative to the application's directory here, that
            ``--persist`` keeps across releases for the same data; None when the mount is outside
            the application's own directory (say what to do instead).
        read_only: The mount was read-only there.
        export: The command to run on the other server to copy the data out, as a
            tarball on standard output. Never run by Noust.
        restore: The command to run here to put it back.
    """

    mount_path: str
    volume: str | None = None
    host_path: str | None = None
    persistent_path: str | None = None
    read_only: bool = False
    export: str | None = None
    restore: str | None = None

    def describe(self) -> str:
        """
        Returns:
            One sentence for an operator: what the data was and how to bring it.
        """
        held = (
            f"the volume {self.volume}"
            if self.volume
            else f"{self.host_path} on the server"
            if self.host_path
            else "storage"
        )
        text = f"It kept data in {held}, mounted at {self.mount_path}."
        if self.persistent_path:
            text += f" Here that is --persist {self.persistent_path}."
        else:
            text += (
                " That is outside the application's own directory, so there is no persistent"
                " path for it here: if the data matters, mount it with 'noust app mount' or"
                " keep it in a path of the application."
            )
        if self.export and self.restore:
            text += f" Copy it: on that server, {self.export}; here, {self.restore}."
        return text


@dataclass
class ProposedDatabase:
    """
    A database the application used on the other platform.

    Attributes:
        engine: The engine, as ``noust db`` names it (``postgresql``, ``mysql``...).
        name: What the other platform calls it.
        reference: What names it there: a container, a service, the host the application
            connected to.
        image: The image it ran.
        version: Its version, when the image says.
        database: The database inside the engine, when the platform says.
        user: The account that owns it, when the platform says.
        linked: An environment variable of the application points at it; False for one that
            only shares the application's environment.
        dump: The command to run on the other server to write it to standard output, never
            run by Noust. The password is read inside the container, not given on the line.
        restore: The command to run here to load that dump.
        note: What else is worth knowing.
    """

    engine: str
    name: str
    reference: str
    image: str | None = None
    version: str | None = None
    database: str | None = None
    user: str | None = None
    linked: bool = False
    dump: str | None = None
    restore: str | None = None
    note: str | None = None

    def describe(self) -> str:
        """
        Returns:
            One sentence for an operator: what the database was and how to bring it.
        """
        text = f"It uses the {self.engine} database {self.name!r} ({self.reference})."
        if self.engine in INSTANCE_ENGINES:
            text += (
                f" Noust runs {self.engine} in a container of its own: create one with"
                f" 'noust db instance create NAME --engine {self.engine}'."
            )
        elif self.dump and self.restore:
            text += (
                f" Create the engine if this server lacks it ('noust db install {self.engine}'),"
                f" dump it on that server with: {self.dump}; and load it here with: {self.restore}."
            )
        elif self.engine in DUMP_SCRIPTS and not self.dump:
            text += (
                " No command is written for it: its name is not one that can go on a command line"
                " as it is. Dump it with the engine's own tool and load it here with"
                " 'noust db import'."
            )
        if self.note:
            text += f" {self.note}"
        return text


#: How each engine's official image dumps itself, as a script for ``sh -c`` inside its container,
#: and the extension of the file it writes. The container's own variables hold the account, the
#: database and the password (``POSTGRES_*``, ``MYSQL_*``, ``MARIADB_*``, ``MONGO_INITDB_*``): the
#: password is read there and given to the client through its environment, never on a command line.
#: A variable the image leaves to its default is spelled with that default.
DUMP_SCRIPTS: dict[str, tuple[str, str]] = {
    "postgresql": (
        'pg_dump -U "${POSTGRES_USER:-postgres}" -Fc "${POSTGRES_DB:-${POSTGRES_USER:-postgres}}"',
        "dump",
    ),
    "mysql": (
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump --single-transaction --no-tablespaces '
        '-uroot "${MYSQL_DATABASE:---all-databases}"',
        "sql",
    ),
    "mariadb": (
        'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb-dump --single-transaction -uroot '
        '"${MARIADB_DATABASE:---all-databases}"',
        "sql",
    ),
    "mongodb": (
        'mongodump --archive --username "$MONGO_INITDB_ROOT_USERNAME" '
        '--password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin',
        "archive",
    ),
}


#: A name another platform gave something (a container, a service, a file) that may be written into
#: a command as it is. The platform's owner is not always the person who pastes the command, so a
#: name with a space, a quote, a semicolon or a substitution in it is never put on a command line.
_PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")

#: The one expression that finds a Swarm or Compose service's container, built only by
#: :func:`service_container`.
_SERVICE_CONTAINER = re.compile(r"\$\(docker ps -qf name=\^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\\\.\)")


def is_plain_name(value: str | None) -> bool:
    """
    Args:
        value: A name another platform gave something.

    Returns:
        Whether it may be written into a command as it is.
    """
    return value is not None and _PLAIN_NAME.fullmatch(value) is not None


def service_container(service: str) -> str | None:
    """
    Write the expression a shell turns into the container of a Swarm service.

    Args:
        service: The service's name, as the other platform gave it.

    Returns:
        ``$(docker ps -qf name=^SERVICE\\.)``, or None when the name is not a plain one.
    """
    if not is_plain_name(service):
        return None
    return f"$(docker ps -qf name=^{service}\\.)"


def database_commands(engine: str, container: str, name: str, label: str) -> tuple[str, str] | None:
    """
    Write the commands that move a database's data from the other server to this one.

    Args:
        engine: As ``noust db`` names it.
        container: What ``docker exec`` is given to reach its container: a name, or an expression
            that finds it (``$(docker ps -qf name=...)``).
        name: The database's name here (a plain identifier).
        label: Names the file the dump is written to, without its extension.

    Returns:
        ``(dump, restore)``, or None for an engine the official images have no portable dump for
        (a cache: Redis and its kin are rebuilt, or copied with their own tools), and for a
        container or a label that is not a plain name: nothing the other platform supplied reaches
        a command unchecked.
    """
    if engine not in DUMP_SCRIPTS:
        return None
    if not (
        is_plain_name(label)
        and is_plain_name(name)
        and (is_plain_name(container) or _SERVICE_CONTAINER.fullmatch(container))
    ):
        return None
    script, extension = DUMP_SCRIPTS[engine]
    return (
        f"docker exec {container} sh -c '{script}' > {label}.{extension}",
        f"noust db import {name} {label}.{extension} -e {engine}",
    )


def database_name_for(name: str) -> str:
    """
    Args:
        name: What the other platform calls a database (``production-db``).

    Returns:
        A name for the database here: lower-case letters, digits and underscores.
    """
    cleaned = "".join(char if char.isalnum() else "_" for char in name).strip("_").lower()
    return cleaned or "imported"


def volume_export(volume: str) -> str:
    """
    Write the command that copies a Docker volume out as a tarball.

    Args:
        volume: The volume's name.

    Returns:
        A command for the other server's shell; it never writes there, and the volume is
        mounted read-only.
    """
    return (
        f"docker run --rm -v {shlex.quote(volume)}:/data:ro alpine tar -C /data -czf - . "
        f"> {shlex.quote(volume + '.tar.gz')}"
    )


def directory_export(path: str) -> str:
    """
    Write the command that copies a directory of the other server out as a tarball.

    Args:
        path: The directory.

    Returns:
        A command for the other server's shell.
    """
    name = path.strip("/").replace("/", "-") or "data"
    return f"tar -C {shlex.quote(path)} -czf {shlex.quote(name + '.tar.gz')} ."


def tarball_restore(persistent_path: str | None) -> str:
    """
    Write the command that puts a tarball back under an application's persistent path.

    Args:
        persistent_path: The path ``--persist`` keeps, relative to the application's directory;
            None writes the command with a placeholder for the folder.

    Returns:
        A command for this server, to run as root.
    """
    safe = persistent_path is not None and all(
        re.fullmatch(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*", part) for part in persistent_path.split("/")
    )
    target = f"/var/www/apps/APP/shared/{persistent_path}" if safe else "FOLDER"
    return f"tar -xzf FILE.tar.gz -C {target}"


def persistent_path_for(mount_path: str, work_dir: str = "/app") -> str | None:
    """
    Turn a path inside a container into a path of the application's directory.

    Args:
        mount_path: Where the data was mounted.
        work_dir: The directory the application ran in there (``/app`` for the buildpack
            builders Coolify and CapRover use).

    Returns:
        The path relative to it, or None when the mount is outside it (or is the directory itself).
    """
    clean = posixpath.normpath(mount_path)
    prefix = work_dir.rstrip("/") + "/"
    if not clean.startswith(prefix) or ".." in clean.split("/"):
        return None
    relative = clean[len(prefix) :]
    if not relative or not all(
        re.fullmatch(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*", part) for part in relative.split("/")
    ):
        # A path with a space, a quote or a substitution in it is not written into a command.
        return None
    return relative


@dataclass
class ProposedCron:
    """
    A scheduled task of the other platform.

    Attributes:
        name: What the platform calls it.
        schedule: Its cron expression.
        command: What it runs, as written there.
    """

    name: str
    schedule: str
    command: str


@dataclass
class Proposal:
    """
    Noust's reading of another platform's configuration.

    Attributes:
        platform: ``vercel``, ``railway``, ``render``, ``heroku``, ``coolify``
            or ``dokploy``.
        files: The configuration files read, relative to the repository.
        app_type: The Noust application type, or None to let detection decide
            (the platform's own builder detected it too).
        install_command: The platform's install command, as written there.
        build_command: The platform's build command.
        start_command: The platform's start command.
        output_directory: Where the platform took the built site from.
        port: The port the application listens on, when the configuration
            fixes one.
        health_path: The path the platform's health check probes.
        health_timeout: Seconds the platform's health check waits.
        env: The environment variables declared, in file order.
        databases: Engines of the databases the application needs.
        domains: Custom domains the configuration names.
        persistent_paths: Paths, relative to the application, that the
            platform kept on a persistent disk.
        warnings: Everything read that Noust has no equivalent for, one
            sentence each, with what to do instead.
        name: What the platform calls the application (an API import; a
            repository's file has no name of its own).
        source: The repository the platform pulled the code from, when it
            says; None for a file read from a checkout.
        branch: The branch it followed.
        base_directory: The subdirectory of the repository the project lives in.
        watch_paths: The globs a push had to touch to deploy it.
        image: A registry image the platform ran instead of building code.
        memory_max_mb: Its memory limit, in MB.
        cpu_quota_percent: Its CPU limit, as a percent of one CPU.
        cron: Its scheduled tasks.
        storage: The data it kept outside its image, and how to bring it.
        database_details: The databases it used, and how to bring their data.
        submodules: Whether it initialised submodules; None when it does not say.
        equivalent: The ``noust`` command that makes what Noust cannot yet
            deploy from a proposal (an image, a Dockerfile, a Compose file).
    """

    platform: str
    files: list[str] = field(default_factory=list)
    app_type: str | None = None
    install_command: str | None = None
    build_command: str | None = None
    start_command: str | None = None
    output_directory: str | None = None
    port: int | None = None
    health_path: str | None = None
    health_timeout: int | None = None
    env: list[ProposedEnv] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    persistent_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    name: str | None = None
    source: str | None = None
    branch: str | None = None
    base_directory: str | None = None
    watch_paths: list[str] = field(default_factory=list)
    image: str | None = None
    memory_max_mb: int | None = None
    cpu_quota_percent: int | None = None
    cron: list[ProposedCron] = field(default_factory=list)
    storage: list[ProposedStorage] = field(default_factory=list)
    database_details: list[ProposedDatabase] = field(default_factory=list)
    submodules: bool | None = None
    equivalent: str | None = None

    def add_env(self, variable: ProposedEnv) -> None:
        """
        Add a variable, replacing an earlier one of the same name.

        Args:
            variable: The variable.
        """
        self.env = [existing for existing in self.env if existing.name != variable.name]
        self.env.append(variable)

    def need_database(self, engine: str) -> None:
        """
        Record that the application needs a database of an engine, once.

        Args:
            engine: One of :data:`DATABASE_ENGINES`.
        """
        if engine not in self.databases:
            self.databases.append(engine)

    def warn(self, message: str) -> None:
        """
        Record something without an equivalent, once.

        Args:
            message: What it is and what to do instead.
        """
        if message not in self.warnings:
            self.warnings.append(message)

    def build_document(self) -> str | None:
        """
        Write the platform's commands as a ``build:`` block, where Noust can run them.

        A block replaces the commands of the application's type, and Noust runs
        a command as an argument vector in the build sandbox, never through a
        shell. A command the platform ran through one (``npm ci && npm run
        build``) is not carried: it is a warning, to be put in a script or in
        ``package.json``.

        Returns:
            The YAML document, or None when the platform says nothing Noust
            would apply.
        """
        import yaml

        block: dict[str, str] = {}
        if self.base_directory:
            block["base_directory"] = self.base_directory
        for key, command in (
            ("install", self.install_command),
            ("build", self.build_command),
            ("start", self.start_command),
        ):
            if not command:
                continue
            if is_plain_command(command):
                block[key] = command
            else:
                self.warn(
                    f"The {key} command ({command!r}) uses a shell. Noust runs a command as "
                    "a program and its arguments: put it in a script, or in package.json's "
                    "scripts, so every deploy runs it."
                )
        if self.output_directory:
            block["publish_directory"] = self.output_directory
        return yaml.safe_dump({"build": block}, sort_keys=False) if block else None

    def note_commands(self) -> None:
        """
        Say once that the platform's commands are shown, not run.

        Only when the configuration names a command at all: a proposal with
        none has nothing to explain.
        """
        if self.install_command or self.build_command or self.start_command:
            self.warn(
                "Noust installs, builds and starts the project with the commands of its "
                "type (the package.json scripts, the Python entry point); the platform's "
                "commands are shown to compare. Put a custom one in package.json's "
                "scripts so every deploy runs it."
            )


#: What a shell does with a command line and an argument vector cannot.
_SHELL_WORDS = frozenset({"&&", "||", "|", ";", "&", ">", ">>", "<", "<<", "2>&1", "2>", "&>"})


def is_plain_command(text: str) -> bool:
    """
    Tell whether a command is a program and its arguments, with no shell in it.

    Args:
        text: A command as another platform wrote it.

    Returns:
        True when splitting it like a shell does gives a program and plain
        arguments: no operator, no substitution, no variable assignment first.
    """
    if any(token in text for token in ("`", "$(")):
        return False
    try:
        words = shlex.split(text)
    except ValueError:
        return False
    if not words or "=" in words[0].split("/")[-1]:
        return False
    return not any(word in _SHELL_WORDS or word.endswith((";", "&&", "||")) for word in words)


def declared_env(
    name: str, value: str | None, *, generated: bool = False, required: bool | None = None
) -> ProposedEnv:
    """
    Build a variable from what a configuration file says about it.

    Args:
        name: Variable name.
        value: The default given, or None.
        generated: The platform generates it.
        required: Whether a value is needed; None decides it: needed when
            there is no default and nothing generates one.

    Returns:
        The variable. A secret-looking name never keeps a default: a
        configuration file committed to a repository is not where a real
        credential belongs, and a proposal must not spread one further.
    """
    # What the value looks like counts as well as the name: a connection string with a password in
    # it (DATABASE_URL) is a credential, and so is a token under a plain name.
    secret = (
        generated or name_looks_secret(name) or (value is not None and classify(name, value).secret)
    )
    shown = None if secret else value
    needed = (value is None and not generated) if required is None else required
    if secret and value is not None and not generated:
        # The file had a value for a secret: it is withheld, so it must be
        # given again at deploy time.
        needed = True
    return ProposedEnv(name=name, value=shown, secret=secret, generated=generated, required=needed)


def read_text(root: Path, name: str) -> str | None:
    """
    Read one configuration file of a repository, if it is there.

    A link is not followed: a repository is untrusted input, and a link to
    ``/etc/shadow`` named ``vercel.json`` must not be read as root.

    Args:
        root: The repository.
        name: Path relative to it.

    Returns:
        The file's text, or None when it does not exist.

    Raises:
        ValidationError: The file is a link, too large, unreadable, or not
            UTF-8.
    """
    path = root / name
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValidationError(
            f"{name} is not a regular file",
            details="Noust reads configuration files that are committed as files, not links.",
        )
    try:
        size = path.stat().st_size
        if size > MAX_CONFIG_SIZE:
            raise ValidationError(
                f"{name} is too large to be a configuration file ({size} bytes)",
                details=f"The limit is {MAX_CONFIG_SIZE} bytes.",
            )
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{name} is not UTF-8 text", details=str(exc)) from exc
    except OSError as exc:
        raise ValidationError(f"Cannot read {name}", details=str(exc)) from exc


def too_deep(name: str) -> ValidationError:
    """
    Build the refusal for a file nested deeper than a parser follows.

    Python's JSON, YAML and TOML parsers recurse once per level and raise
    ``RecursionError`` past the interpreter's limit; turned into this, it is
    an unreadable file like any other instead of a crash of the inspection.

    Args:
        name: The file, relative to the repository.

    Returns:
        The error.
    """
    return ValidationError(
        f"{name} nests too deeply to be read",
        details="A configuration file nests a few levels; check it is the one the platform reads.",
    )


#: Deepest nesting a configuration file (or an export document) may have. They
#: nest a handful of levels; anything past this is not one of them.
MAX_NESTING = 64


def nesting_depth(value: Any) -> int:
    """
    Measure how deeply lists and mappings nest, without recursing.

    Python 3.14's JSON parser reads documents far deeper than the interpreter's
    recursion limit that earlier versions stopped at, so the limit is checked
    here, the same on every version, rather than left to the parser.

    Args:
        value: A parsed document.

    Returns:
        The depth: 0 for a scalar, 1 for a flat list or mapping.
    """
    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children: Any = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        depth += 1
        deepest = max(deepest, depth)
        if depth > MAX_NESTING:
            return depth
        stack.extend((child, depth) for child in children)
    return deepest


def check_nesting(name: str, value: Any) -> Any:
    """
    Refuse a parsed file nested deeper than :data:`MAX_NESTING`.

    Args:
        name: The file, relative to the repository.
        value: What it parsed to.

    Returns:
        ``value``, unchanged.

    Raises:
        ValidationError: It nests too deeply (see :func:`too_deep`).
    """
    if nesting_depth(value) > MAX_NESTING:
        raise too_deep(name)
    return value


def read_json_object(root: Path, name: str) -> dict[str, Any] | None:
    """
    Read a JSON configuration file that must hold an object.

    Args:
        root: The repository.
        name: Path relative to it.

    Returns:
        The object, or None when the file does not exist.

    Raises:
        ValidationError: The file cannot be read, is not JSON (or nests too
            deeply to parse), or is not an object.
    """
    text = read_text(root, name)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{name} is not valid JSON", details=str(exc)) from exc
    except RecursionError as exc:
        raise too_deep(name) from exc
    check_nesting(name, data)
    if not isinstance(data, dict):
        raise ValidationError(f"{name} does not hold a JSON object")
    return data


def text_value(data: dict[str, Any], key: str) -> str | None:
    """
    Read a string setting, ignoring one of another type.

    Args:
        data: The mapping.
        key: The setting.

    Returns:
        The stripped string, or None when it is absent, empty or not a string.
    """
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def int_value(data: dict[str, Any], key: str) -> int | None:
    """
    Read a whole-number setting, accepting one written as a string.

    Args:
        data: The mapping.
        key: The setting.

    Returns:
        The number, or None when it is absent or not a whole number.
    """
    value = data.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def health_timeout(seconds: int | None, proposal: Proposal, *, source: str) -> int | None:
    """
    Fit a platform's health-check wait into the range the health gate accepts.

    Args:
        seconds: The platform's wait.
        proposal: Where a warning goes when it had to be changed.
        source: The setting's name on the platform, for the warning.

    Returns:
        The wait, clamped to what :func:`noust.validators.health.check_health_timeout`
        accepts, or None when there was none.
    """
    if seconds is None:
        return None
    clamped = min(max(seconds, HEALTH_TIMEOUT_MIN), HEALTH_TIMEOUT_MAX)
    if clamped != seconds:
        proposal.warn(
            f"{source} is {seconds} seconds; Noust's health gate waits from "
            f"{HEALTH_TIMEOUT_MIN} to {HEALTH_TIMEOUT_MAX}, so {clamped} is proposed."
        )
    return clamped
