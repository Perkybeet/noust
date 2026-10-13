# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The manifest: what a server should hold, written down (J-15).

A manifest is a YAML document the operator keeps in git and applies to a server
(``noust --context prod manifest apply noust-manifest.yaml``): the projects and
applications that should exist, where each lives, its variables, its domains,
its limits, its health check, its databases and its backup schedule. It is
declarative and *non-destructive*: applying it creates what is missing and
brings what exists to what is written, and never deletes anything the manifest
does not mention (an application missing from it is reported, not touched).

The schema is closed, like ``noust.yaml``'s: an unknown key is an error that
names the path, never ignored, because a typo in a key (``memory_mb``) that is
silently dropped is a limit that was never set. The document is the
operator's, and trusted as far as the operator's token goes; a manifest found
in an application's repository is not this and is never read as one.

Secrets do not belong in a file in git. A variable's value is a plain value, or
``{from_env: NAME}`` / ``{from_file: PATH}``, read *where the manifest is
applied from* (a pipeline's secrets, a file only the operator can read): they
are refused when the manifest is previewed on a server, which must never read
its own environment on someone else's behalf.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

from noust.core.exceptions import ValidationError
from noust.validators.domain import is_valid_domain
from noust.validators.environment import validate_env_name

#: The manifest format this release reads.
VERSION = 1

#: Most applications and variables a manifest may hold: a manifest is a
#: document a person reads, not a database.
MAX_APPLICATIONS = 200
MAX_VARIABLES = 200
MAX_BYTES = 1024 * 1024

_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")
_CALENDAR = re.compile(r"^[A-Za-z0-9 :*,/.\-]{1,100}$")


class ManifestError(ValidationError):
    """The manifest is not valid; the message names where and the details say what to write."""


@dataclass(frozen=True)
class Secret:
    """
    A variable's value that is read where the manifest is applied from.

    Attributes:
        kind: ``from_env`` (an environment variable there) or ``from_file`` (a file there).
        source: The variable's name or the file's path.
    """

    kind: str
    source: str


@dataclass(frozen=True)
class ProjectSpec:
    """
    A project, and the environments it should have.

    Attributes:
        team: The team's slug.
        project: The project's slug.
        name: Its display name, when it has to be created.
        environments: Environment slugs it should have (``production`` is always there).
    """

    team: str
    project: str
    name: str | None = None
    environments: tuple[str, ...] = ()


@dataclass(frozen=True)
class DatabaseSpec:
    """
    A database an application should use.

    Attributes:
        engine: Its engine.
        name: Its name, or None for the one derived from the application.
        env_var: The variable holding its connection string, or None for the engine's.
    """

    engine: str
    name: str | None = None
    env_var: str | None = None


@dataclass(frozen=True)
class BackupSpec:
    """
    How an application is backed up.

    Attributes:
        schedule: ``hourly``, ``daily``, ``weekly``, ``monthly`` or a systemd calendar.
        retention_count: Backups the schedule keeps, or None for the server's rotation.
        retention_days: Days it keeps them, or None for no age limit.
        include_databases: Whether each backup dumps the databases too.
    """

    schedule: str
    retention_count: int | None = 7
    retention_days: int | None = 30
    include_databases: bool = True


@dataclass(frozen=True)
class AppSpec:
    """
    One application, as it should be.

    Attributes:
        domain: Its domain.
        source: Where it is deployed from (required to create it).
        recipe: A recipe of the catalogue to deploy instead of a source.
        app_type: Its type; ``auto`` detects it.
        branch: The branch it follows.
        port: Its port, chosen by the server when None.
        ssl: Whether to obtain a certificate when it is created.
        team: Where it lives; None leaves the server to choose (creation) or
            leaves it where it is (existing).
        project: Its project.
        environment: Its environment.
        env: Its variables. ``None`` leaves them alone; a mapping is merged
            into what the application has (variables not named stay).
        domains: Aliases it should answer on.
        limits: Memory, CPU and task limits, set together; None leaves them.
        health: The health gate's path, statuses and timeout, set together; None leaves them.
        databases: Databases it should use.
        backups: Its backup schedule; None leaves it.
    """

    domain: str
    source: str | None = None
    recipe: str | None = None
    app_type: str = "auto"
    branch: str | None = None
    port: int | None = None
    ssl: bool = True
    team: str | None = None
    project: str | None = None
    environment: str | None = None
    env: Mapping[str, str | Secret] | None = None
    domains: tuple[str, ...] = ()
    limits: Mapping[str, int | None] | None = None
    health: Mapping[str, Any] | None = None
    databases: tuple[DatabaseSpec, ...] = ()
    backups: BackupSpec | None = None


@dataclass(frozen=True)
class Manifest:
    """
    A parsed manifest.

    Attributes:
        name: What it is called, for people.
        projects: The projects and environments that should exist.
        applications: The applications that should exist.
    """

    name: str = ""
    projects: tuple[ProjectSpec, ...] = ()
    applications: tuple[AppSpec, ...] = field(default_factory=tuple)

    def secrets(self) -> list[tuple[str, str, Secret]]:
        """
        Returns:
            ``(domain, variable, secret)`` for every value read where the
            manifest is applied from.
        """
        found = []
        for app in self.applications:
            for name, value in (app.env or {}).items():
                if isinstance(value, Secret):
                    found.append((app.domain, name, value))
        return found


def _fail(path: str, message: str, details: str = "") -> ManifestError:
    """
    Args:
        path: Where in the document, such as ``applications[0].env``.
        message: What is wrong.
        details: What to write instead.

    Returns:
        The error to raise.
    """
    return ManifestError(f"{path}: {message}", details=details, field=path)


def _mapping(
    value: Any, path: str, allowed: set[str], required: set[str] | None = None
) -> dict[str, Any]:
    """
    Args:
        value: What the document holds there.
        path: Where.
        allowed: The keys it may have.
        required: The keys it must have.

    Returns:
        The mapping.

    Raises:
        ManifestError: When it is not a mapping, has a key that is not allowed or
            lacks a required one.
    """
    if not isinstance(value, dict):
        raise _fail(path, "must be a mapping")
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise _fail(
            f"{path}.{unknown[0]}" if path else unknown[0],
            "is not a key of the manifest",
            f"Allowed here: {', '.join(sorted(allowed))}.",
        )
    for key in sorted(required or ()):
        if value.get(key) in (None, ""):
            raise _fail(f"{path}.{key}" if path else key, "is required")
    return value


def _text(value: Any, path: str, *, maximum: int = 253) -> str:
    """
    Args:
        value: What the document holds.
        path: Where.
        maximum: Longest accepted.

    Returns:
        It, as trimmed text.

    Raises:
        ManifestError: When it is not text, is empty or too long.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _fail(path, f"must be a text of 1 to {maximum} characters")
    if any(ord(char) < 32 for char in value):
        raise _fail(path, "must not contain control characters")
    return value.strip()


def _slug(value: Any, path: str) -> str:
    """
    Args:
        value: A slug as written.
        path: Where.

    Returns:
        It, lower-cased.

    Raises:
        ManifestError: When it is not a slug.
    """
    text = _text(value, path, maximum=40).lower()
    if not _SLUG.fullmatch(text):
        raise _fail(
            path, f"{text!r} is not a short name", "Use lower-case letters, digits and '-'."
        )
    return text


def _integer(value: Any, path: str, low: int, high: int) -> int:
    """
    Args:
        value: A number as written.
        path: Where.
        low: The least accepted.
        high: The most.

    Returns:
        The integer.

    Raises:
        ManifestError: When it is not one in range.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _fail(path, f"must be a whole number from {low} to {high}")
    return int(value)


def _variable(value: Any, path: str) -> str | Secret:
    """
    Args:
        value: A variable's value as written.
        path: Where.

    Returns:
        Its text, or the :class:`Secret` it is read from.

    Raises:
        ManifestError: For anything but a scalar or ``{from_env: NAME}`` / ``{from_file: PATH}``.
    """
    if isinstance(value, dict):
        data = _mapping(value, path, {"from_env", "from_file"})
        if len(data) != 1:
            raise _fail(path, "needs exactly one of from_env and from_file")
        kind, source = next(iter(data.items()))
        return Secret(kind=kind, source=_text(source, f"{path}.{kind}", maximum=1024))
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    raise _fail(path, "must be a text, a number, true or false, or {from_env: NAME}")


def _application(raw: Any, path: str) -> AppSpec:
    """
    Args:
        raw: One application as written.
        path: Where.

    Returns:
        The application.

    Raises:
        ManifestError: When it is not valid.
    """
    data = _mapping(
        raw,
        path,
        {
            "domain",
            "source",
            "recipe",
            "type",
            "branch",
            "port",
            "ssl",
            "team",
            "project",
            "environment",
            "env",
            "domains",
            "limits",
            "health",
            "databases",
            "backups",
        },
        {"domain"},
    )
    domain = _text(data["domain"], f"{path}.domain").lower()
    valid, why = is_valid_domain(domain)
    if not valid:
        raise _fail(f"{path}.domain", why, "Write a domain such as shop.example.com.")
    if data.get("source") and data.get("recipe"):
        raise _fail(
            path, "source and recipe cannot be given together", "A recipe brings its own source."
        )
    env: dict[str, str | Secret] | None = None
    if "env" in data and data["env"] is not None:
        values = _mapping(
            data["env"], f"{path}.env", set(data["env"]) if isinstance(data["env"], dict) else set()
        )
        if len(values) > MAX_VARIABLES:
            raise _fail(f"{path}.env", f"has more than {MAX_VARIABLES} variables")
        env = {}
        for name, value in values.items():
            try:
                validate_env_name(str(name))
            except ValidationError as exc:
                raise _fail(f"{path}.env.{name}", exc.message, exc.details) from exc
            env[str(name)] = _variable(value, f"{path}.env.{name}")
    aliases: tuple[str, ...] = ()
    if data.get("domains"):
        if not isinstance(data["domains"], list):
            raise _fail(f"{path}.domains", "must be a list of domains")
        names = []
        for index, item in enumerate(data["domains"]):
            alias = _text(item, f"{path}.domains[{index}]").lower()
            ok, why = is_valid_domain(alias)
            if not ok:
                raise _fail(f"{path}.domains[{index}]", why)
            if alias != domain and alias not in names:
                names.append(alias)
        aliases = tuple(names)
    limits = None
    if data.get("limits") is not None:
        raw_limits = _mapping(
            data["limits"], f"{path}.limits", {"memory_max_mb", "cpu_quota_percent", "tasks_max"}
        )
        limits = {
            "memory_max_mb": _optional_int(
                raw_limits, "memory_max_mb", f"{path}.limits", 64, 1_048_576
            ),
            "cpu_quota_percent": _optional_int(
                raw_limits, "cpu_quota_percent", f"{path}.limits", 1, 6400
            ),
            "tasks_max": _optional_int(raw_limits, "tasks_max", f"{path}.limits", 16, 10_000_000),
        }
    health = None
    if data.get("health") is not None:
        raw_health = _mapping(data["health"], f"{path}.health", {"path", "expect", "timeout"})
        health = {
            "path": _optional_text(raw_health, "path", f"{path}.health"),
            "expect": _optional_text(raw_health, "expect", f"{path}.health"),
            "timeout": _optional_int(raw_health, "timeout", f"{path}.health", 5, 600),
        }
    databases = []
    for index, item in enumerate(data.get("databases") or []):
        spec = _mapping(
            item, f"{path}.databases[{index}]", {"engine", "name", "env_var"}, {"engine"}
        )
        databases.append(
            DatabaseSpec(
                engine=_text(
                    spec["engine"], f"{path}.databases[{index}].engine", maximum=40
                ).lower(),
                name=_optional_text(spec, "name", f"{path}.databases[{index}]"),
                env_var=_optional_text(spec, "env_var", f"{path}.databases[{index}]"),
            )
        )
    backups = None
    if data.get("backups") is not None:
        raw_backups = _mapping(
            data["backups"],
            f"{path}.backups",
            {"schedule", "retention_count", "retention_days", "include_databases"},
            {"schedule"},
        )
        schedule = _text(raw_backups["schedule"], f"{path}.backups.schedule", maximum=100)
        if not _CALENDAR.fullmatch(schedule):
            raise _fail(
                f"{path}.backups.schedule",
                "is not a schedule",
                "Use hourly, daily, weekly, monthly or a systemd calendar.",
            )
        include = raw_backups.get("include_databases", True)
        if not isinstance(include, bool):
            raise _fail(f"{path}.backups.include_databases", "must be true or false")
        backups = BackupSpec(
            schedule=schedule,
            retention_count=(
                _optional_int(raw_backups, "retention_count", f"{path}.backups", 1, 365)
                if "retention_count" in raw_backups
                else 7
            ),
            retention_days=(
                _optional_int(raw_backups, "retention_days", f"{path}.backups", 1, 3650)
                if "retention_days" in raw_backups
                else 30
            ),
            include_databases=include,
        )
    ssl = data.get("ssl", True)
    if not isinstance(ssl, bool):
        raise _fail(f"{path}.ssl", "must be true or false")
    return AppSpec(
        domain=domain,
        source=_optional_text(data, "source", path, maximum=1024),
        recipe=_optional_text(data, "recipe", path, maximum=64),
        app_type=_optional_text(data, "type", path, maximum=40) or "auto",
        branch=_optional_text(data, "branch", path, maximum=255),
        port=_optional_int(data, "port", path, 1, 65535),
        ssl=ssl,
        team=_optional_slug(data, "team", path),
        project=_optional_slug(data, "project", path),
        environment=_optional_slug(data, "environment", path),
        env=env,
        domains=aliases,
        limits=limits,
        health=health,
        databases=tuple(databases),
        backups=backups,
    )


def _optional_text(
    data: Mapping[str, Any], key: str, path: str, *, maximum: int = 253
) -> str | None:
    """
    Args:
        data: A mapping.
        key: The key.
        path: Where the mapping is.
        maximum: Longest accepted.

    Returns:
        The text, or None when the key is absent or null.
    """
    value = data.get(key)
    return None if value is None else _text(value, f"{path}.{key}", maximum=maximum)


def _optional_slug(data: Mapping[str, Any], key: str, path: str) -> str | None:
    """
    Args:
        data: A mapping.
        key: The key.
        path: Where the mapping is.

    Returns:
        The slug, or None when the key is absent or null.
    """
    value = data.get(key)
    return None if value is None else _slug(value, f"{path}.{key}")


def _optional_int(data: Mapping[str, Any], key: str, path: str, low: int, high: int) -> int | None:
    """
    Args:
        data: A mapping.
        key: The key.
        path: Where the mapping is.
        low: The least accepted.
        high: The most.

    Returns:
        The number, or None when the key is absent or null.
    """
    value = data.get(key)
    return None if value is None else _integer(value, f"{path}.{key}", low, high)


def parse(text: str) -> Manifest:
    """
    Read a manifest.

    Args:
        text: The YAML (or JSON) document.

    Returns:
        The manifest.

    Raises:
        ManifestError: When it is too large, is not YAML, or is not a valid manifest.
    """
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise _fail("manifest", f"is larger than {MAX_BYTES // 1024} KiB")
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _fail("manifest", f"is not valid YAML: {exc}") from exc
    data = _mapping(document, "", {"version", "name", "projects", "applications"}, {"version"})
    if data["version"] != VERSION:
        raise _fail(
            "version",
            f"{data['version']!r} is not a version this release reads",
            f"Write version: {VERSION}.",
        )
    projects = []
    for index, raw in enumerate(data.get("projects") or []):
        path = f"projects[{index}]"
        spec = _mapping(raw, path, {"team", "project", "name", "environments"}, {"team", "project"})
        environments = []
        for position, item in enumerate(spec.get("environments") or []):
            slug = _slug(item, f"{path}.environments[{position}]")
            if slug not in environments:
                environments.append(slug)
        projects.append(
            ProjectSpec(
                team=_slug(spec["team"], f"{path}.team"),
                project=_slug(spec["project"], f"{path}.project"),
                name=_optional_text(spec, "name", path, maximum=80),
                environments=tuple(environments),
            )
        )
    raw_apps = data.get("applications") or []
    if not isinstance(raw_apps, list):
        raise _fail("applications", "must be a list")
    if len(raw_apps) > MAX_APPLICATIONS:
        raise _fail("applications", f"has more than {MAX_APPLICATIONS} applications")
    applications = [
        _application(raw, f"applications[{index}]") for index, raw in enumerate(raw_apps)
    ]
    seen: set[str] = set()
    for index, app in enumerate(applications):
        if app.domain in seen:
            raise _fail(f"applications[{index}].domain", f"{app.domain} appears twice")
        seen.add(app.domain)
    return Manifest(
        name=_optional_text(data, "name", "", maximum=120) or "",
        projects=tuple(projects),
        applications=tuple(applications),
    )
