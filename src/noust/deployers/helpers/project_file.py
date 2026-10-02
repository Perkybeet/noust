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

The file is untrusted input like the rest of the repository: the schema is
closed (an unknown key is refused, never ignored), a path that leaves the
application or climbs with ``..`` is refused, and every refusal is a
:class:`~noust.core.exceptions.ValidationError` whose ``field`` names what it is
about (``hooks.pre_deploy[0].timeout``). A file that is a link is refused
unread: Noust reads it as root.

The operator's hooks (:func:`noust.deployers.helpers.hooks.set_operator_hooks`)
are a document of the same shape, holding only ``hooks``.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import yaml  # type: ignore[import-untyped]

from noust.core.exceptions import ValidationError
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

_TOP_KEYS = ("hooks", "backup")
_HOOK_PHASES: tuple[HookPhase, ...] = ("pre_deploy", "post_deploy")
_HOOK_KEYS = ("run", "service", "workdir", "timeout", "migrates")
_BACKUP_KEYS = ("databases",)
_DATABASE_KEYS = ("service", "engine", "database", "user")

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
        path: The file read, when there was one.
    """

    hooks: HookSet = field(default_factory=HookSet)
    backup_databases: BackupDatabases | tuple[StackDatabaseSpec, ...] = "auto"
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
            field=name if name == "noust.yaml" else "hooks",
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
    return ProjectFile(
        hooks=_hooks(mapping.get("hooks"), source),
        backup_databases=_backup(mapping.get("backup")),
        path=path,
    )


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
    for key in mapping:
        if key not in _HOOK_PHASES:
            raise _unknown(f"hooks.{key}", str(key), _HOOK_PHASES)
    phases: dict[str, tuple[Hook, ...]] = {}
    for phase in _HOOK_PHASES:
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
            _hook(entry, f"hooks.{phase}[{index}]") for index, entry in enumerate(entries)
        )
    hooks = HookSet(pre_deploy=phases["pre_deploy"], post_deploy=phases["post_deploy"])
    if hooks.declared or source == "operator":
        return HookSet(pre_deploy=hooks.pre_deploy, post_deploy=hooks.post_deploy, source=source)
    return hooks


def _hook(value: object, where: str) -> Hook:
    """
    Args:
        value: One item of a phase.
        where: Its field path, ``hooks.pre_deploy[0]``.

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
    if service is not None and (not isinstance(service, str) or not _SERVICE.match(service)):
        raise ValidationError(
            f"{where}.service is not a Compose service name",
            details="Name one of the stack's services, as the compose file does.",
            field=f"{where}.service",
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
    if not isinstance(service, str) or not _SERVICE.match(service):
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
    "DEFAULT_HOOK_TIMEOUT",
    "MAX_HOOK_TIMEOUT",
    "MIN_HOOK_TIMEOUT",
    "PROJECT_FILES",
    "STACK_ENGINES",
    "ProjectFile",
    "StackDatabaseSpec",
    "StackEngine",
    "load_project_file",
    "parse_hooks_document",
]
