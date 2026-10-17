# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust.yaml``: what a repository tells Noust about deploying it.

Read from the root of the tree being deployed (``noust.yaml``, or
``.noust.yaml``), versioned with the code::

    hooks:
      pre_deploy:
        - run: ./node_modules/.bin/prisma migrate deploy
          service: backend        # Docker Compose only: the service it runs in
          workdir: /app           # optional
          timeout: 600            # seconds, 1-3600
          migrates: true          # it changes the schema
      post_deploy:
        - run: ./scripts/purge-cache.sh
    backup:
      databases: auto             # auto | off | [{service, engine, database, user}]
    build:
      base_directory: apps/web    # the project lives in this subdirectory
      install: pnpm install --frozen-lockfile
      build: pnpm --filter web build
      start: node dist/server.js
      publish_directory: dist     # static output
      runtime: node@22
      timeout: 3600               # seconds each of install and build may take

The file is untrusted input like the rest of the repository: the schema is
closed (an unknown key is refused, never ignored), a path that leaves the
application or climbs with ``..`` is refused, and every refusal is a
:class:`~noust.core.exceptions.ValidationError` whose ``field`` names what it is
about (``hooks.pre_deploy[0].timeout``). A file that is a link is refused
unread: Noust reads it as root.

The operator's hooks (:func:`noust.deployers.helpers.hooks.set_operator_hooks`)
are a document of the same shape, holding only ``hooks``; so is the operator's
build block (:func:`noust.deployers.helpers.build_config.set_operator_build`),
holding only ``build``. A command is an argument vector, never a shell line.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import yaml  # type: ignore[import-untyped]

from noust.core.exceptions import ValidationError
from noust.core.versions import parse_runtime
from noust.deployers.helpers.build_config import (
    MAX_TIMEOUT,
    MIN_TIMEOUT,
    BuildConfig,
    BuildSource,
)
from noust.deployers.helpers.hooks import Hook, HookPhase, HookSet, HookSource

#: The names the project file is read under, in order.
PROJECT_FILES: tuple[str, ...] = ("noust.yaml", ".noust.yaml")

#: The largest project file read; a declaration of hooks is a few lines.
MAX_PROJECT_FILE_BYTES = 64 * 1024

#: The range a hook's timeout must fall in, and what it gets without one.
MIN_HOOK_TIMEOUT = 1
MAX_HOOK_TIMEOUT = 3600
DEFAULT_HOOK_TIMEOUT = 600

#: The database engines a stack's database can be declared as.
StackEngine = Literal["postgres", "mysql", "mariadb", "mongo"]
STACK_ENGINES: tuple[str, ...] = ("postgres", "mysql", "mariadb", "mongo")

#: What ``backup.databases`` says when it is not a list.
BackupDatabases = Literal["auto", "off"]

#: The blocks of a project file Noust applies from a deployment: what the repository may say about
#: how it is built, started and backed up.
_APPLIED_KEYS = ("hooks", "backup", "build")

#: The blocks that are declared here and read elsewhere. ``processes`` is read by
#: :mod:`noust.deployers.helpers.processes`; the others are what ``noust apply`` sets from the
#: file and ``noust app export --as-yaml`` writes (:mod:`noust.deployers.project_apply`), which
#: only an operator applies: a repository that could set its own domains, limits or cron jobs
#: would be setting the server's. A deployment checks that each is a block and reads no further.
DECLARATIVE_KEYS = ("source", "compose", "app", "env", "domains", "tasks", "processes")

_TOP_KEYS = (*_APPLIED_KEYS, *DECLARATIVE_KEYS)
_HOOK_PHASES: tuple[HookPhase, ...] = ("pre_deploy", "post_deploy")
_PREVIEW_HOOK_PHASES: tuple[HookPhase, ...] = ("preview_post_create", "preview_pre_destroy")
_HOOK_KEYS = ("run", "service", "workdir", "timeout", "migrates")
_BACKUP_KEYS = ("databases",)
_DATABASE_KEYS = ("service", "engine", "database", "user")
_BUILD_KEYS = (
    "base_directory",
    "install",
    "build",
    "start",
    "publish_directory",
    "runtime",
    "cache",
    "timeout",
)

#: One segment of a directory the build block names. Narrower than a file
#: name on purpose: the path ends up in a unit's WorkingDirectory and in the
#: web server's ``root``, where a space or a quote is a syntax error or worse.
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._@+~=-]+$")

#: The longest directory the build block names.
_MAX_BUILD_PATH = 200

# A Compose service name, as Compose itself accepts them.
_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class StackDatabaseSpec:
    """
    A database of a Compose stack, declared where detection cannot see it.

    Attributes:
        service: The service it runs in.
        engine: ``postgres``, ``mysql``, ``mariadb`` or ``mongo``.
        database: The database to dump; read from the service's environment
            when None.
        user: The user to dump it as; read from the service's environment
            when None.
    """

    service: str
    engine: StackEngine
    database: str | None = None
    user: str | None = None


@dataclass(frozen=True)
class ProjectFile:
    """
    What a repository's ``noust.yaml`` declares.

    Attributes:
        hooks: Its hooks; source ``none`` when it declares none or there is
            no file.
        backup_databases: ``auto`` (detect the stack's databases), ``off``, or
            the databases declared by hand.
        build: Its build block; source ``none`` when it declares none or there
            is no file.
        declared: The blocks of :data:`DECLARATIVE_KEYS` it holds, as written. A deployment
            applies none of them; each has its own reader.
        path: The file read, when there was one.
    """

    hooks: HookSet = field(default_factory=HookSet)
    backup_databases: BackupDatabases | tuple[StackDatabaseSpec, ...] = "auto"
    build: BuildConfig = field(default_factory=BuildConfig)
    declared: dict[str, Any] = field(default_factory=dict, compare=False)
    path: Path | None = field(default=None, compare=False)


def load_project_file(root: Path) -> ProjectFile:
    """
    Read the project file at the root of a tree.

    Args:
        root: The tree being deployed.

    Returns:
        What it declares; an empty declaration when there is no file.

    Raises:
        ValidationError: Both names exist, the file is a link, too large,
            not YAML, or outside the schema; ``field`` names what.
    """
    present = [
        name for name in PROJECT_FILES if (root / name).is_symlink() or (root / name).exists()
    ]
    if not present:
        return ProjectFile()
    if len(present) > 1:
        raise ValidationError(
            "The repository has both noust.yaml and .noust.yaml",
            details="Keep one of them; Noust will not guess which one you meant.",
            field="noust.yaml",
        )
    name = present[0]
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValidationError(
            f"{name} is a link or not a regular file, so it is not read",
            details=f"Commit {name} as a regular file at the root of the repository.",
            field="noust.yaml",
        )
    if path.stat().st_size > MAX_PROJECT_FILE_BYTES:
        raise ValidationError(
            f"{name} is larger than {MAX_PROJECT_FILE_BYTES // 1024} KiB",
            details="A project file declares hooks and backups in a few lines.",
            field="noust.yaml",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"{name} could not be read: {exc}",
            details=f"Commit {name} as UTF-8 text.",
            field="noust.yaml",
        ) from exc
    document = _load_yaml(text, name)
    return _project(document, source="repo", path=path)


def parse_hooks_document(text: str, *, source: str) -> HookSet:
    """
    Parse a document holding hooks: the operator's, or a repository's.

    Args:
        text: The YAML document, with ``hooks`` at its top.
        source: ``operator`` (only ``hooks`` is accepted) or ``repo`` (the
            whole project file's schema).

    Returns:
        The hooks, labelled with the source.

    Raises:
        ValidationError: Not YAML, or outside the schema; ``field`` names what.
    """
    label: HookSource = "repo" if source == "repo" else "operator"
    name = "noust.yaml" if label == "repo" else "hooks"
    document = _load_yaml(text, name)
    if label == "repo":
        return _project(document, source="repo", path=None).hooks
    mapping = _mapping(document, name, where="the document")
    for key in mapping:
        if key == "backup":
            raise ValidationError(
                "The operator's hooks do not carry the backup setting",
                details="Set it with: noust app backup-before-update DOMAIN on|off",
                field="backup",
            )
        if key != "hooks":
            raise _unknown(str(key), str(key), ("hooks",))
    return _hooks(mapping.get("hooks"), label)


def parse_build_document(text: str, *, source: str) -> BuildConfig:
    """
    Parse a document holding a build block: the operator's, or a repository's.

    Args:
        text: The YAML document, with ``build`` at its top.
        source: ``operator`` (only ``build`` is accepted) or ``repo`` (the
            whole project file's schema).

    Returns:
        The block, labelled with the source.

    Raises:
        ValidationError: Not YAML, or outside the schema; ``field`` names what.
    """
    label: BuildSource = "repo" if source == "repo" else "operator"
    name = "noust.yaml" if label == "repo" else "build"
    document = _load_yaml(text, name)
    if label == "repo":
        return _project(document, source="repo", path=None).build
    mapping = _mapping(document, name, where="the document")
    for key in mapping:
        if key in ("hooks", "backup"):
            raise ValidationError(
                f"The operator's build block does not carry {key}",
                details="Set hooks with: noust app hooks set DOMAIN --file FILE",
                field=str(key),
            )
        if key != "build":
            raise _unknown(str(key), str(key), ("build",))
    return _build(mapping.get("build"), label)


def _load_yaml(text: str, name: str) -> object:
    """
    Args:
        text: The document.
        name: What to call it in an error.

    Returns:
        What it holds; None for an empty document.

    Raises:
        ValidationError: It is not YAML.
    """
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError(
            f"{name} is not valid YAML",
            details=str(exc),
            field=name if name in ("noust.yaml", "build") else "hooks",
        ) from exc


def _project(document: object, *, source: HookSource, path: Path | None) -> ProjectFile:
    """
    Args:
        document: The parsed project file.
        source: Where it came from.
        path: The file, when read from one.

    Returns:
        What it declares.

    Raises:
        ValidationError: Outside the schema.
    """
    mapping = _mapping(document, "noust.yaml", where="the project file")
    for key in mapping:
        if key not in _TOP_KEYS:
            raise _unknown(str(key), str(key), _TOP_KEYS)
    declared = {key: mapping[key] for key in DECLARATIVE_KEYS if mapping.get(key) is not None}
    for key, value in declared.items():
        if not isinstance(value, dict | list):
            raise ValidationError(
                f"{key} must be a block of keys" + (" or a list" if key == "tasks" else ""),
                details=f"Write it as keys and values, as in {key}: {{...}}.",
                field=key,
            )
    return ProjectFile(
        hooks=_hooks(mapping.get("hooks"), source),
        backup_databases=_backup(mapping.get("backup")),
        build=_build(mapping.get("build"), "repo" if source == "repo" else "operator"),
        declared=declared,
        path=path,
    )


def _build(value: object, source: BuildSource) -> BuildConfig:
    """
    Args:
        value: What ``build`` holds.
        source: Where it came from.

    Returns:
        The block; ``source`` is kept only when a key is set, except for the
        operator's, whose empty block still wins over the repository's.

    Raises:
        ValidationError: Outside the schema.
    """
    mapping = _mapping(value, "build", where="build")
    for key in mapping:
        if key not in _BUILD_KEYS:
            raise _unknown(f"build.{key}", str(key), _BUILD_KEYS)
    runtime = mapping.get("runtime")
    config = BuildConfig(
        base_directory=_build_directory(
            mapping.get("base_directory"), "build.base_directory", what="base directory"
        ),
        install=_build_command(mapping.get("install"), "build.install"),
        build=_build_command(mapping.get("build"), "build.build"),
        start=_build_command(mapping.get("start"), "build.start"),
        publish_directory=_build_directory(
            mapping.get("publish_directory"), "build.publish_directory", what="publish directory"
        ),
        runtime=None if runtime is None else parse_runtime(runtime, field="build.runtime"),
        cache=_build_flag(mapping.get("cache"), "build.cache"),
        timeout=_build_timeout(mapping.get("timeout"), "build.timeout"),
    )
    return config.with_source(source)


def _build_flag(value: object, where: str) -> bool | None:
    """
    Args:
        value: What a yes-or-no key holds.
        where: Its field path.

    Returns:
        The flag, or None when the key is left out.

    Raises:
        ValidationError: Not true or false.
    """
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValidationError(
            f"{where} must be true or false",
            details=f"Write {where.rsplit('.', 1)[-1]}: false.",
            field=where,
        )
    return value


def _build_timeout(value: object, where: str) -> int | None:
    """
    Args:
        value: What the ``timeout`` key of the build block holds.
        where: Its field path.

    Returns:
        Seconds, or None when the key is left out.

    Raises:
        ValidationError: Not a whole number of seconds in the range builds are given.
    """
    if value is None:
        return None
    # bool is an int to Python, and "yes" is True to YAML.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{where} must be a number of seconds",
            details=f"Give a whole number from {MIN_TIMEOUT} to {MAX_TIMEOUT}.",
            field=where,
        )
    if not MIN_TIMEOUT <= value <= MAX_TIMEOUT:
        raise ValidationError(
            f"{where} is {value} seconds, outside {MIN_TIMEOUT}-{MAX_TIMEOUT}",
            details="A build that needs more than a day is not one a deployment waits for.",
            field=where,
        )
    return value


def _build_command(value: object, where: str) -> tuple[str, ...] | None:
    """
    Args:
        value: What an ``install``, ``build`` or ``start`` key holds.
        where: Its field path.

    Returns:
        The argument vector, or None when the key is left out.

    Raises:
        ValidationError: Empty, unbalanced quotes, or a program that climbs.
    """
    if value is None:
        return None
    return _argv(value, where)


def _build_directory(value: object, where: str, *, what: str) -> str | None:
    """
    Args:
        value: What a directory key holds.
        where: Its field path.
        what: What to call it in a message.

    Returns:
        The directory as a normalised relative POSIX path; None for the root
        of the repository (``.``, ``./``) or when the key is left out.

    Raises:
        ValidationError: Not text, absolute, climbing with ``..``, with a
            segment a unit or a site cannot carry, or too long.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(
            f"{where} must be a path",
            details=f"Name the {what} relative to the project, as in {where.rsplit('.', 1)[-1]}: dist.",
            field=where,
        )
    text = value.strip()
    if text in ("", ".", "./"):
        return None
    if text.startswith(("/", "~")) or "\\" in text:
        raise ValidationError(
            f"{where} is not a path inside the repository",
            details="Use a path relative to the repository, without a leading '/' or '~'.",
            field=where,
        )
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise ValidationError(
            f"{where} climbs out of the repository with '..'",
            details=f"Name the {what} directly, without '..'.",
            field=where,
        )
    if not parts or any(not _PATH_SEGMENT.fullmatch(part) for part in parts):
        raise ValidationError(
            f"{where} has a name Noust cannot serve or run from",
            details="Use letters, digits and . _ - @ + ~ = in each name; no spaces or quotes.",
            field=where,
        )
    normalised = "/".join(parts)
    if len(normalised) > _MAX_BUILD_PATH:
        raise ValidationError(
            f"{where} is longer than {_MAX_BUILD_PATH} characters",
            details=f"Name a shorter {what}.",
            field=where,
        )
    return normalised


def _hooks(value: object, source: HookSource) -> HookSet:
    """
    Args:
        value: What ``hooks`` holds.
        source: Where it came from.

    Returns:
        The hooks; ``source`` is kept only when a hook is declared, except
        for the operator's, whose empty document still wins over the
        repository's.

    Raises:
        ValidationError: Outside the schema.
    """
    mapping = _mapping(value, "hooks", where="hooks")
    known = (*_HOOK_PHASES, *_PREVIEW_HOOK_PHASES)
    for key in mapping:
        if key not in known:
            raise _unknown(f"hooks.{key}", str(key), known)
    phases: dict[str, tuple[Hook, ...]] = {}
    for phase in known:
        entries = mapping.get(phase)
        if entries is None:
            phases[phase] = ()
            continue
        if not isinstance(entries, list):
            raise ValidationError(
                f"hooks.{phase} must be a list of hooks",
                details=f"Write each hook as an item of the list, as in {phase}: "
                "[{run: ./script.sh}].",
                field=f"hooks.{phase}",
            )
        phases[phase] = tuple(
            _hook(entry, f"hooks.{phase}[{index}]", preview=phase in _PREVIEW_HOOK_PHASES)
            for index, entry in enumerate(entries)
        )
    hooks = HookSet(
        pre_deploy=phases["pre_deploy"],
        post_deploy=phases["post_deploy"],
        preview_post_create=phases["preview_post_create"],
        preview_pre_destroy=phases["preview_pre_destroy"],
    )
    if hooks.declared or source == "operator":
        return HookSet(
            pre_deploy=hooks.pre_deploy,
            post_deploy=hooks.post_deploy,
            source=source,
            preview_post_create=hooks.preview_post_create,
            preview_pre_destroy=hooks.preview_pre_destroy,
        )
    return hooks


def _hook(value: object, where: str, *, preview: bool = False) -> Hook:
    """
    Args:
        value: One item of a phase.
        where: Its field path, ``hooks.pre_deploy[0]``.
        preview: It belongs to a preview's life (``preview_post_create``,
            ``preview_pre_destroy``): it runs in the preview as its own application, where
            there is no tree to be relative to and no deployment whose schema it could change.

    Returns:
        The hook.

    Raises:
        ValidationError: Outside the schema.
    """
    if not isinstance(value, dict):
        raise ValidationError(
            f"{where} must be a mapping with at least 'run'",
            details="Write it as: - run: ./script.sh",
            field=where,
        )
    for key in value:
        if key not in _HOOK_KEYS:
            raise _unknown(f"{where}.{key}", str(key), _HOOK_KEYS)
    service = value.get("service")
    if service is not None and (not isinstance(service, str) or not _SERVICE.fullmatch(service)):
        raise ValidationError(
            f"{where}.service is not a Compose service name",
            details="Name one of the stack's services, as the compose file does.",
            field=f"{where}.service",
        )
    if preview:
        refused = {
            "workdir": "A preview hook runs where the application runs: commit a script that "
            "changes directory.",
            "migrates": "A preview hook is not a deployment, so it has no schema change to "
            "confirm.",
        }
        for key, reason in refused.items():
            if value.get(key) is not None:
                raise ValidationError(
                    f"{where}.{key} is not for a preview hook",
                    details=reason,
                    field=f"{where}.{key}",
                )
    run = _argv(value.get("run"), f"{where}.run")
    workdir = _workdir(value.get("workdir"), f"{where}.workdir", in_container=service is not None)
    return Hook(
        run=run,
        service=service,
        workdir=workdir,
        timeout=_timeout(value.get("timeout"), f"{where}.timeout"),
        migrates=_flag(value.get("migrates"), f"{where}.migrates"),
    )


def _argv(value: object, where: str) -> tuple[str, ...]:
    """
    Turn ``run`` into an argv, never a shell command line.

    Args:
        value: A string, split as a shell would split it, or a list.
        where: Its field path.

    Returns:
        The argv.

    Raises:
        ValidationError: Missing, empty, not text, unbalanced quotes, or a
            program path that climbs with ``..``.
    """
    if isinstance(value, str):
        try:
            argv = tuple(shlex.split(value))
        except ValueError as exc:
            raise ValidationError(
                f"{where} cannot be split into a command: {exc}",
                details="Close every quote. A hook runs one program, never a shell; for "
                "pipes or redirections, commit a script and run it.",
                field=where,
            ) from exc
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        argv = tuple(value)
    else:
        raise ValidationError(
            f"{where} must be the command to run",
            details="Write it as text (run: ./scripts/migrate.sh --yes) or as a list of arguments.",
            field=where,
        )
    if not argv or not argv[0]:
        raise ValidationError(
            f"{where} is empty", details="Name the program the hook runs.", field=where
        )
    if ".." in PurePosixPath(argv[0]).parts:
        raise ValidationError(
            f"{where} climbs out of the application with '..'",
            details="Run a program of the repository by its path from the root "
            "(./scripts/x.sh) or a program on the PATH.",
            field=where,
        )
    if any("\x00" in part for part in argv):
        raise ValidationError(f"{where} holds a NUL byte", details="Remove it.", field=where)
    return argv


def _workdir(value: object, where: str, *, in_container: bool) -> str | None:
    """
    Args:
        value: What ``workdir`` holds.
        where: Its field path.
        in_container: The hook runs in a Compose service, where an absolute
            path is a path inside the container.

    Returns:
        The working directory, or None.

    Raises:
        ValidationError: Not text, ``..`` anywhere, or absolute outside a
            container.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"{where} must be a path", details="Name a directory, or leave it out.", field=where
        )
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValidationError(
            f"{where} climbs with '..'",
            details="Name the directory directly, without '..'.",
            field=where,
        )
    if path.is_absolute() and not in_container:
        raise ValidationError(
            f"{where} is an absolute path outside the application",
            details="Without 'service' a hook runs in the application's own tree: name a "
            "directory relative to the root of the repository.",
            field=where,
        )
    return value


def _timeout(value: object, where: str) -> int:
    """
    Args:
        value: What ``timeout`` holds.
        where: Its field path.

    Returns:
        Seconds, the default when it is left out.

    Raises:
        ValidationError: Not a whole number from 1 to 3600.
    """
    if value is None:
        return DEFAULT_HOOK_TIMEOUT
    # bool is an int to Python, and "yes" is True to YAML.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{where} must be a number of seconds",
            details=f"Give a whole number from {MIN_HOOK_TIMEOUT} to {MAX_HOOK_TIMEOUT}.",
            field=where,
        )
    if not MIN_HOOK_TIMEOUT <= value <= MAX_HOOK_TIMEOUT:
        raise ValidationError(
            f"{where} is {value} seconds, outside {MIN_HOOK_TIMEOUT}-{MAX_HOOK_TIMEOUT}",
            details="A hook that needs more than an hour belongs in a job of its own.",
            field=where,
        )
    return value


def _flag(value: object, where: str) -> bool:
    """
    Args:
        value: What ``migrates`` holds.
        where: Its field path.

    Returns:
        The flag; False when it is left out.

    Raises:
        ValidationError: Not true or false.
    """
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValidationError(
            f"{where} must be true or false", details="Write migrates: true.", field=where
        )
    return value


def _backup(value: object) -> BackupDatabases | tuple[StackDatabaseSpec, ...]:
    """
    Args:
        value: What ``backup`` holds.

    Returns:
        What ``backup.databases`` says; ``auto`` when it is left out.

    Raises:
        ValidationError: Outside the schema.
    """
    mapping = _mapping(value, "backup", where="backup")
    for key in mapping:
        if key not in _BACKUP_KEYS:
            raise _unknown(f"backup.{key}", str(key), _BACKUP_KEYS)
    databases = mapping.get("databases", "auto")
    # YAML 1.1 reads a bare off as false; it is what the operator wrote.
    if databases is False or databases == "off":
        return "off"
    if databases is None or databases == "auto":
        return "auto"
    if not isinstance(databases, list):
        raise ValidationError(
            "backup.databases must be auto, off or a list of databases",
            details="Write databases: auto, databases: off, or a list of "
            "{service, engine, database, user}.",
            field="backup.databases",
        )
    return tuple(
        _database(entry, f"backup.databases[{index}]") for index, entry in enumerate(databases)
    )


def _database(value: object, where: str) -> StackDatabaseSpec:
    """
    Args:
        value: One declared database.
        where: Its field path.

    Returns:
        The declaration.

    Raises:
        ValidationError: Outside the schema.
    """
    if not isinstance(value, dict):
        raise ValidationError(
            f"{where} must be a mapping",
            details="Write it as {service: db, engine: postgres, database: app, user: app}.",
            field=where,
        )
    for key in value:
        if key not in _DATABASE_KEYS:
            raise _unknown(f"{where}.{key}", str(key), _DATABASE_KEYS)
    service = value.get("service")
    if not isinstance(service, str) or not _SERVICE.fullmatch(service):
        raise ValidationError(
            f"{where}.service must name the service the database runs in",
            details="Name one of the stack's services, as the compose file does.",
            field=f"{where}.service",
        )
    engine = value.get("engine")
    if engine not in STACK_ENGINES:
        raise ValidationError(
            f"{where}.engine must be one of {', '.join(STACK_ENGINES)}",
            details="Noust dumps these engines with their own tools.",
            field=f"{where}.engine",
        )
    names: dict[str, str | None] = {}
    for key in ("database", "user"):
        item = value.get(key)
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ValidationError(
                f"{where}.{key} must be a name",
                details="Leave it out to read it from the service's environment.",
                field=f"{where}.{key}",
            )
        names[key] = item
    return StackDatabaseSpec(
        service=service,
        engine=cast(StackEngine, engine),
        database=names["database"],
        user=names["user"],
    )


def _mapping(value: object, name: str, *, where: str) -> dict[Any, Any]:
    """
    Args:
        value: What a key holds; None is an empty mapping.
        name: Its field path.
        where: What to call it in the message.

    Returns:
        The mapping.

    Raises:
        ValidationError: It is something else.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(
            f"{where.capitalize()} must be a mapping of keys",
            details="Write it as keys and values, as in hooks: {pre_deploy: [{run: ./x.sh}]}.",
            field=name,
        )
    return value


def _unknown(where: str, key: str, known: tuple[str, ...]) -> ValidationError:
    """
    Args:
        where: The field path of the unknown key.
        key: The key.
        known: What is accepted there.

    Returns:
        The refusal: a key that is not understood is never ignored, because
        a misspelt ``pre_deploy`` would be a migration that silently never runs.
    """
    return ValidationError(
        f"Unknown key {key!r} in {where.rsplit('.', 1)[0] if '.' in where else 'the document'}",
        details=f"Accepted here: {', '.join(known)}.",
        field=where,
    )


__all__ = [
    "DECLARATIVE_KEYS",
    "DEFAULT_HOOK_TIMEOUT",
    "MAX_HOOK_TIMEOUT",
    "MIN_HOOK_TIMEOUT",
    "PROJECT_FILES",
    "STACK_ENGINES",
    "ProjectFile",
    "StackDatabaseSpec",
    "StackEngine",
    "load_project_file",
    "parse_build_document",
    "parse_hooks_document",
]
