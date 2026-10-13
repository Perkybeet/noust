# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which compose file, which profiles and which extra arguments a Compose stack is run with.

``app_compose`` (``schema_v14.SOURCES_SQL``) holds one row per application the
operator chose something for. Until now a stack's file and profiles were only
what its unit's environment recorded when it was created, and an update did not
even read the profiles back; the row is the one place they live, and the unit is
written from it.

What a stack may be given beyond that is a closed set, because everything
here ends up in an argument of ``docker compose``, run as root:

- ``build_args``: flags of ``docker compose build`` from a short list
  (``--pull``, ``--no-cache``, ``--build-arg KEY=VALUE``, ``--progress MODE``...).
- ``up_args``: flags of ``docker compose up`` from a short list (``--wait``,
  ``--wait-timeout N``, ``--timeout N``, ``--force-recreate``, ``--scale
  SERVICE=N``...). ``--build`` and ``--no-build`` are not among them: whether
  Noust builds is Noust's to decide.
- ``services``: the services to build and to start, when it is not all of them.

A command that is not on the list is refused with the list, never ignored: a
flag that silently did nothing is worse than none. This module is the only one
that reads or writes the table and the one definition of what is allowed.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from noust.core import audit
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import NoustStore, get_store

_PROFILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")

#: Flags of ``docker compose build`` that take no value.
BUILD_SWITCHES: frozenset[str] = frozenset(
    {"--pull", "--no-cache", "--quiet", "-q", "--with-dependencies"}
)
#: Flags of ``docker compose build`` that take one value, and what it may be.
BUILD_VALUED: dict[str, str] = {
    "--build-arg": "KEY=VALUE",
    "--progress": "auto, plain, tty or quiet",
}

#: Flags of ``docker compose up`` that take no value.
UP_SWITCHES: frozenset[str] = frozenset(
    {
        "--wait",
        "--force-recreate",
        "--no-deps",
        "--renew-anon-volumes",
        "-V",
        "--always-recreate-deps",
    }
)
#: Flags of ``docker compose up`` that take one value.
UP_VALUED: dict[str, str] = {
    "--wait-timeout": "seconds",
    "--timeout": "seconds",
    "-t": "seconds",
    "--scale": "SERVICE=N",
    "--pull": "always, missing or never",
}

_PROGRESS = ("auto", "plain", "tty", "quiet")
_PULL = ("always", "missing", "never")
MAX_ARGS = 40


def _value_ok(flag: str, value: str) -> bool:
    """
    Args:
        flag: A flag that takes a value.
        value: What was given.

    Returns:
        Whether the value is one the flag may have.
    """
    if any(ord(char) < 32 or ord(char) == 127 for char in value) or len(value) > 512:
        return False
    if flag == "--build-arg":
        key, sep, _ = value.partition("=")
        return bool(sep) and _ENV_NAME.match(key) is not None
    if flag == "--progress":
        return value in _PROGRESS
    if flag == "--pull":
        return value in _PULL
    if flag == "--scale":
        name, sep, count = value.partition("=")
        return (
            bool(sep) and _SERVICE.match(name) is not None and count.isdigit() and int(count) <= 50
        )
    return value.isdigit() and 0 < int(value) <= 3600


def check_args(
    args: object, *, switches: frozenset[str], valued: dict[str, str], field: str
) -> tuple[str, ...]:
    """
    Validate the extra arguments of a ``docker compose`` subcommand.

    Args:
        args: A command line (split like a shell would split it) or a list of
            words.
        switches: The flags without a value that are allowed.
        valued: The flags with one value that are allowed, and what it is.
        field: The setting's name, for the refusal.

    Returns:
        The words, as an argument vector.

    Raises:
        ValidationError: Anything not on the lists: another flag, a value out of
            its range, a word that is not a flag.
    """
    if args is None:
        return ()
    if isinstance(args, str):
        try:
            words = shlex.split(args)
        except ValueError as exc:
            raise ValidationError(
                f"{field} cannot be split into words: {exc}",
                details="Close every quote. Arguments are never given to a shell.",
                field=field,
            ) from exc
    elif isinstance(args, (list, tuple)) and all(isinstance(item, str) for item in args):
        words = list(args)
    else:
        raise ValidationError(
            f"{field} must be a list of arguments or a command line",
            details="Write it as: --pull --build-arg VERSION=1.2",
            field=field,
        )
    if len(words) > MAX_ARGS:
        raise ValidationError(f"{field} has more than {MAX_ARGS} arguments", field=field)
    allowed = ", ".join(sorted(switches | set(valued)))
    checked: list[str] = []
    iterator = iter(words)
    for word in iterator:
        if word in switches:
            checked.append(word)
            continue
        if word in valued:
            value = next(iterator, None)
            if value is None or not _value_ok(word, value):
                raise ValidationError(
                    f"{field}: {word} needs a value that is {valued[word]}",
                    details=f"Got {value!r}.",
                    field=field,
                )
            checked.extend([word, value])
            continue
        flag, sep, value = word.partition("=")
        if sep and flag in valued and _value_ok(flag, value):
            checked.extend([flag, value])
            continue
        raise ValidationError(
            f"{field}: {word!r} is not an argument Noust passes to docker compose",
            details=f"Allowed: {allowed}. The services to build and start are their own setting.",
            field=field,
        )
    return tuple(checked)


def check_names(values: object, *, field: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    """
    Validate a list of profile or service names.

    Args:
        values: A list, or names separated by commas or spaces.
        field: The setting's name.
        pattern: What a name looks like.

    Returns:
        The names, without duplicates, in order.

    Raises:
        ValidationError: A name is not one.
    """
    if values is None:
        return ()
    if isinstance(values, str):
        items: Iterable[Any] = re.split(r"[,\s]+", values.strip()) if values.strip() else ()
    elif isinstance(values, (list, tuple)):
        items = values
    else:
        raise ValidationError(f"{field} must be a list of names", field=field)
    found: list[str] = []
    for item in items:
        if not isinstance(item, str) or pattern.match(item) is None:
            raise ValidationError(
                f"{item!r} is not a valid name for {field}",
                details="Use letters, digits, '.', '_' and '-', as Compose does.",
                field=field,
            )
        if item not in found:
            found.append(item)
    return tuple(found)


@dataclass(frozen=True)
class ComposeOptions:
    """
    What a Compose stack is run with, beyond the compose file's own content.

    Attributes:
        compose_file: The file, relative to the application; None finds the
            default names.
        profiles: The profiles to activate.
        build_args: Extra arguments of ``docker compose build``.
        up_args: Extra arguments of ``docker compose up``.
        services: The services to build and to start; empty for all of them.
        customised: Whether a row exists.
    """

    compose_file: str | None = None
    profiles: tuple[str, ...] = ()
    build_args: tuple[str, ...] = ()
    up_args: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    customised: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the options for the API and ``--json``.

        Returns:
            Every option; lists as lists.
        """
        return {
            "compose_file": self.compose_file,
            "profiles": list(self.profiles),
            "build_args": list(self.build_args),
            "up_args": list(self.up_args),
            "services": list(self.services),
            "customised": self.customised,
        }


def check_options(
    *,
    compose_file: object = None,
    profiles: object = None,
    build_args: object = None,
    up_args: object = None,
    services: object = None,
) -> dict[str, Any]:
    """
    Validate the options someone gave, without storing anything.

    Args:
        compose_file: A path inside the application.
        profiles: Names.
        build_args: Arguments of ``docker compose build``.
        up_args: Arguments of ``docker compose up``.
        services: Names.

    Returns:
        The options that were given, as stored.

    Raises:
        ValidationError: A value is not acceptable; ``field`` names it.
    """
    changes: dict[str, Any] = {}
    if compose_file is not None:
        if not isinstance(compose_file, str):
            raise ValidationError("compose_file must be a path", field="compose_file")
        if compose_file.strip():
            # Imported here: the deployer module builds on the store.
            from noust.core.exceptions import DeploymentError
            from noust.deployers.docker_compose import compose_file_option

            try:
                changes["compose_file"] = str(compose_file_option(compose_file.strip()))
            except DeploymentError as exc:
                raise ValidationError(
                    exc.message, details=exc.details, field="compose_file"
                ) from exc
        else:
            changes["compose_file"] = None
    if profiles is not None:
        changes["profiles"] = check_names(profiles, field="profiles", pattern=_PROFILE)
    if build_args is not None:
        changes["build_args"] = check_args(
            build_args, switches=BUILD_SWITCHES, valued=BUILD_VALUED, field="build_args"
        )
    if up_args is not None:
        changes["up_args"] = check_args(
            up_args, switches=UP_SWITCHES, valued=UP_VALUED, field="up_args"
        )
    if services is not None:
        changes["services"] = check_names(services, field="services", pattern=_SERVICE)
    return changes


def get_options(domain: str, *, store: NoustStore | None = None) -> ComposeOptions:
    """
    Read what a stack is run with.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        The options; empty for a stack with no row.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT c.compose_file, c.profiles, c.build_args, c.up_args, c.services "
            "FROM app_compose c JOIN apps a ON a.id = c.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return ComposeOptions()

    def listed(text: str | None) -> tuple[str, ...]:
        try:
            value = json.loads(text or "[]")
        except ValueError:
            return ()
        return tuple(str(item) for item in value) if isinstance(value, list) else ()

    return ComposeOptions(
        compose_file=row[0],
        profiles=listed(row[1]),
        build_args=listed(row[2]),
        up_args=listed(row[3]),
        services=listed(row[4]),
        customised=True,
    )


def set_options(
    domain: str,
    changes: dict[str, Any],
    *,
    actor: str | None,
    store: NoustStore | None = None,
) -> ComposeOptions:
    """
    Store options already checked by :func:`check_options`, keeping the others.

    Args:
        domain: The application's domain.
        changes: What :func:`check_options` returned.
        actor: Who changed it.
        store: The store; the process-wide one when None.

    Returns:
        The options now in force.

    Raises:
        NoustError: The application is unknown.
    """
    target = store or get_store()
    current = get_options(domain, store=target)
    merged = {
        "compose_file": current.compose_file,
        "profiles": current.profiles,
        "build_args": current.build_args,
        "up_args": current.up_args,
        "services": current.services,
        **changes,
    }
    now = datetime.now().isoformat()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is None:
            raise NoustError(
                f"Application not found: {domain}",
                details="Run 'noust list' to see what is deployed.",
            )
        cursor.execute(
            "INSERT INTO app_compose (app_id, compose_file, profiles, build_args, up_args, "
            "services, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(app_id) DO UPDATE SET compose_file = excluded.compose_file, "
            "profiles = excluded.profiles, build_args = excluded.build_args, "
            "up_args = excluded.up_args, services = excluded.services, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (
                row[0],
                merged["compose_file"],
                json.dumps(list(merged["profiles"])),
                json.dumps(list(merged["build_args"])),
                json.dumps(list(merged["up_args"])),
                json.dumps(list(merged["services"])),
                actor,
                now,
            ),
        )
    audit.record(
        "apps.compose",
        target=f"app:{domain}",
        details={"changed": sorted(changes)},
    )
    return get_options(domain, store=target)


def reset_options(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget a stack's options; its unit's own record applies again.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        Whether there was a row.
    """
    target = store or get_store()
    with target._transaction() as cursor:
        cursor.execute(
            "DELETE FROM app_compose WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (domain,),
        )
        removed = cursor.rowcount > 0
    if removed:
        audit.record("apps.compose", target=f"app:{domain}", details={"changed": ["reset"]})
    return removed


def words(values: Sequence[str]) -> str:
    """
    Write arguments back as the line they were typed as.

    Args:
        values: An argument vector.

    Returns:
        The words joined with shell quoting, which splits back to the same list.
    """
    return shlex.join(values)


__all__ = [
    "BUILD_SWITCHES",
    "BUILD_VALUED",
    "UP_SWITCHES",
    "UP_VALUED",
    "ComposeOptions",
    "check_args",
    "check_names",
    "check_options",
    "get_options",
    "reset_options",
    "set_options",
    "words",
]
