# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application declared in a file, and a file written from an application (checklist A-51).

``noust.yaml`` has always held what a repository may say about how it is built (``build:``,
``hooks:``). This module is the other half: how an application is *run*, written once and
applied to a server, or read back from one. The same file, the same keys::

    app:
      health: {path: /up, expect: 200-399, timeout: 30}
      limits: {memory_max_mb: 512, cpu_quota_percent: 100, tasks_max: 512}
      keep_releases: 5
    source: {submodules: true, lfs: auto, shallow: true, watch_paths: ["apps/web/**"]}
    compose: {file: deploy/compose.yml, profiles: [web]}
    build: {install: pnpm install, build: pnpm build, start: node dist/server.js}
    hooks: {pre_deploy: [{run: pnpm migrate, migrates: true}]}
    env: {LOG_LEVEL: info}
    domains: {aliases: [www.example.com], redirects: [old.example.com]}
    tasks:
      - {name: nightly, schedule: daily, command: ./bin/cleanup}

:func:`export_declaration` writes it from an application; :func:`parse_declaration` reads it
with a closed schema; :func:`plan_declaration` says what applying it changes, against what the
application is now; :func:`apply_declaration` does it, through the managers that own each part
(rule 3): the build block and the hooks through their one setter, the health check, the limits and
the release retention through :mod:`noust.deployers.lifecycle`, and so on.

Three properties matter more than the rest:

- **A repository never applies this.** A deployment reads ``noust.yaml`` for ``build``, ``hooks``
  and ``processes`` only; these sections are accepted by its schema and ignored, because a
  repository that could set its own domains, limits or cron jobs would be setting the server's.
  Only an operator applies them, with ``noust app apply`` or ``POST /api/apps/{domain}/apply``,
  which are root-equivalent: the file may carry commands that run as the application.
- **A section is declarative, a file is not a replacement.** Only the sections the file has are
  touched. Inside ``env`` only the variables named are set, and ``domains`` and ``tasks`` only
  gain what is missing unless the caller asks to prune, so applying a file never removes a
  secret or an application's certificate by omission.
- **Secrets are not declared.** A variable whose name or value looks like a secret
  (:func:`noust.core.secret_detection.classify`) is refused naming the variable: the file is
  meant to be committed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

from noust.core import app_build_store, app_compose_store, app_source_store, audit
from noust.core.applock import app_lock
from noust.core.config import Config
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.secret_detection import classify
from noust.core.store import DomainKind, get_store
from noust.deployers import domains as domain_changes
from noust.deployers.app_export import ImportStep, export_app
from noust.deployers.helpers.app_env import read_app_env, write_app_env
from noust.deployers.helpers.build_config import (
    BuildConfig,
    refuse_unsupported,
    set_operator_build,
)
from noust.deployers.helpers.compose_settings import change_compose
from noust.deployers.helpers.hooks import ALL_PHASES, HookSet, set_operator_hooks
from noust.deployers.helpers.project_file import parse_build_document, parse_hooks_document
from noust.deployers.lifecycle import set_health_check, set_release_retention, set_resource_limits
from noust.managers.cron_manager import CronJob, CronManager
from noust.managers.service_manager import ResourceLimits
from noust.validators.domain import validate_domain
from noust.validators.environment import is_valid_env_name, validate_environment
from noust.validators.health import check_health_expect, check_health_path, check_health_timeout

#: The largest declaration read; a real one is a few kilobytes.
MAX_DECLARATION_BYTES = 64 * 1024

#: Most variables, names and tasks one declaration carries.
MAX_ITEMS = 200

#: The top-level keys, and what each is for. ``backup`` and ``processes`` are read elsewhere.
TOP_KEYS = (
    "app",
    "source",
    "compose",
    "build",
    "hooks",
    "env",
    "domains",
    "tasks",
    "backup",
    "processes",
)

#: Keys the file may carry that apply never touches: the repository's own.
READ_ELSEWHERE = ("backup", "processes")

_APP_KEYS = ("health", "limits", "keep_releases", "port")
_HEALTH_KEYS = ("path", "expect", "timeout")
_LIMIT_KEYS = ("memory_max_mb", "cpu_quota_percent", "tasks_max")
_COMPOSE_KEYS = ("file", "profiles", "build_args", "up_args", "services")
_SOURCE_KEYS = ("submodules", "lfs", "shallow", "watch_paths", "skip_markers")
_DOMAIN_KEYS = ("aliases", "redirects")
_TASK_KEYS = ("name", "schedule", "command", "user", "working_directory", "enabled")

#: What a plan may name, in the order it is applied.
SECTIONS = (
    "health",
    "limits",
    "keep_releases",
    "source",
    "compose",
    "build",
    "hooks",
    "env",
    "domains",
    "tasks",
)

#: Sections whose change runs commands as the application or on the machine.
ROOT_EQUIVALENT = frozenset({"build", "hooks", "tasks", "compose"})

_STEP_ERRORS = (NoustError, OSError)

audit_log = logging.getLogger("noust.audit")


@dataclass(frozen=True)
class Declaration:
    """
    What a declaration file says, checked.

    Attributes:
        health: ``path``, ``expect`` and ``timeout`` as the store keeps them; None when the
            file has no ``app.health``.
        limits: The limits; None when the file has no ``app.limits``.
        keep_releases: How many releases to keep, or None.
        port: The port the file names; applying it only checks it matches.
        source: Source options as :func:`noust.core.app_source_store.check_options` returns them.
        compose: Compose settings as :func:`noust.core.app_compose_store.check_options` does.
        build: The operator's build block as a document, or None.
        hooks: The operator's hooks as a document, or None.
        env: Variable name to value, none of them a secret.
        aliases: Names to answer on; None when the file does not say.
        redirects: Names that redirect to the primary; None when the file does not say.
        tasks: Cron jobs, each ``name``, ``schedule``, ``command``, ``user``,
            ``working_directory`` and ``enabled``.
        ignored: The keys of the file that apply does not read (``backup``, ``processes``).
    """

    health: dict[str, Any] | None = None
    limits: ResourceLimits | None = None
    keep_releases: int | None = None
    port: int | None = None
    source: dict[str, Any] | None = None
    compose: dict[str, Any] | None = None
    build: str | None = None
    hooks: str | None = None
    env: dict[str, str] | None = None
    aliases: tuple[str, ...] | None = None
    redirects: tuple[str, ...] | None = None
    tasks: tuple[dict[str, Any], ...] | None = None
    ignored: tuple[str, ...] = ()

    @property
    def sections(self) -> tuple[str, ...]:
        """The sections the file has, in the order they are applied."""
        has = {
            "health": self.health is not None,
            "limits": self.limits is not None,
            "keep_releases": self.keep_releases is not None,
            "source": self.source is not None,
            "compose": self.compose is not None,
            "build": self.build is not None,
            "hooks": self.hooks is not None,
            "env": self.env is not None,
            "domains": self.aliases is not None or self.redirects is not None,
            "tasks": self.tasks is not None,
        }
        return tuple(name for name in SECTIONS if has[name])


@dataclass(frozen=True)
class Change:
    """
    One thing applying a declaration would change.

    Attributes:
        section: One of :data:`SECTIONS`.
        description: What it does, in a line.
        root_equivalent: Whether it runs commands as the application or on the machine.
    """

    section: str
    description: str
    root_equivalent: bool = False


@dataclass
class ApplyPlan:
    """
    What applying a declaration to an application would do.

    Attributes:
        domain: The application.
        changes: What differs from what it is now.
        unchanged: The sections that already are as declared.
        notes: What apply does not do, and why.
        declaration: The checked declaration.
        prune: Whether domains and tasks the file does not name are removed.
    """

    domain: str
    changes: list[Change]
    unchanged: list[str]
    notes: list[str]
    declaration: Declaration = field(repr=False)
    prune: bool = False

    @property
    def confirm(self) -> list[str]:
        """What needs the operator's yes: every change that runs commands."""
        return [change.description for change in self.changes if change.root_equivalent]

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the plan for the API and ``--json``.

        Returns:
            The changes, the sections already as declared, the notes and what needs a yes.
        """
        return {
            "domain": self.domain,
            "changes": [
                {
                    "section": change.section,
                    "description": change.description,
                    "root_equivalent": change.root_equivalent,
                }
                for change in self.changes
            ],
            "unchanged": list(self.unchanged),
            "notes": list(self.notes),
            "confirm": self.confirm,
            "prune": self.prune,
        }


@dataclass
class ApplyReport:
    """
    What applying a declaration did.

    Attributes:
        domain: The application.
        steps: Every change, in the order it was attempted.
    """

    domain: str
    steps: list[ImportStep] = field(default_factory=list)

    @property
    def not_applied(self) -> list[ImportStep]:
        """Every change that was not applied."""
        return [step for step in self.steps if not step.applied]

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the report for the API and ``--json``.

        Returns:
            The steps, each with whether it was applied and why not.
        """
        return {
            "domain": self.domain,
            "steps": [
                {"part": step.part, "applied": step.applied, "detail": step.detail}
                for step in self.steps
            ],
        }


# Reading -----------------------------------------------------------------------------------


def _fail(where: str, what: str, details: str = "") -> ValidationError:
    """
    Args:
        where: The field path.
        what: What is wrong.
        details: How to fix it.

    Returns:
        The refusal, naming the field.
    """
    return ValidationError(f"{where}: {what}", details=details, field=where)


def _block(value: object, where: str, known: tuple[str, ...]) -> dict[str, Any]:
    """
    Args:
        value: What the key holds.
        where: Its field path.
        known: The keys it accepts.

    Returns:
        The mapping.

    Raises:
        ValidationError: It is not a mapping, or has a key outside ``known``.
    """
    if not isinstance(value, dict):
        raise _fail(where, "must be a block of keys", f"Write it as {where}: {{...}}.")
    for key in value:
        if key not in known:
            raise _fail(
                f"{where}.{key}",
                "is not a key of this block",
                f"Accepted here: {', '.join(known)}.",
            )
    return value


def _names(value: object, where: str) -> tuple[str, ...]:
    """
    Args:
        value: A list of domain names.
        where: Its field path.

    Returns:
        The names, validated, without repeats.

    Raises:
        ValidationError: Not a list of domains, or too many.
    """
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise _fail(where, "must be a list of domain names")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise _fail(where, f"{item!r} is not a domain name")
        try:
            names.append(validate_domain(item))
        except NoustError as exc:
            raise _fail(where, f"{item!r} is not a domain name", exc.message) from exc
    return tuple(dict.fromkeys(names))


def _document(key: str, value: object) -> str:
    """
    Args:
        key: ``build`` or ``hooks``.
        value: The block.

    Returns:
        The one-key YAML document the setters read.
    """
    text: str = yaml.safe_dump({key: value}, sort_keys=False, default_flow_style=False)
    return text


def _env(value: object) -> dict[str, str]:
    """
    Check the ``env`` block: names, values, and that none is a secret.

    Args:
        value: The block.

    Returns:
        Variable name to value, as text.

    Raises:
        ValidationError: A name or value is not valid, or looks like a secret.
    """
    if not isinstance(value, dict) or len(value) > MAX_ITEMS:
        raise _fail("env", "must be a block of NAME: value", "Write it as env: {LOG_LEVEL: info}.")
    out: dict[str, str] = {}
    for name, raw in value.items():
        where = f"env.{name}"
        if not isinstance(name, str) or not is_valid_env_name(name):
            raise _fail(where, "is not a variable name", "Use letters, digits and underscores.")
        if isinstance(raw, bool):
            text = "true" if raw else "false"
        elif isinstance(raw, int | float | str):
            text = str(raw)
        else:
            raise _fail(where, "must be text or a number")
        if classify(name, text).secret:
            raise _fail(
                where,
                "looks like a secret, and this file is meant to be committed",
                "Set it on the server: noust env configure DOMAIN (or the console's Environment tab).",
            )
        out[name] = text
    validate_environment(out)
    return out


def _tasks(value: object) -> tuple[dict[str, Any], ...]:
    """
    Check the ``tasks`` list.

    Args:
        value: The list.

    Returns:
        One mapping per task, with every key present.

    Raises:
        ValidationError: Not a list of tasks, a key outside the schema, or a name repeated.
    """
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise _fail("tasks", "must be a list of tasks", "Write each as {name, schedule, command}.")
    tasks: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        where = f"tasks[{index}]"
        item = _block(raw, where, _TASK_KEYS)
        for key in ("name", "schedule", "command"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise _fail(f"{where}.{key}", "is required and must be text")
        for key in ("user", "working_directory"):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise _fail(f"{where}.{key}", "must be text")
        if not isinstance(item.get("enabled", True), bool):
            raise _fail(f"{where}.enabled", "must be true or false")
        tasks.append(
            {
                "name": item["name"].strip(),
                "schedule": item["schedule"].strip(),
                "command": item["command"].strip(),
                "user": item.get("user"),
                "working_directory": item.get("working_directory"),
                "enabled": item.get("enabled", True),
            }
        )
    names = [task["name"] for task in tasks]
    if len(set(names)) != len(names):
        raise _fail("tasks", "names a task twice", "Task names are unique.")
    return tuple(tasks)


def parse_declaration(text: str) -> Declaration:
    """
    Read and check a declaration file.

    Args:
        text: The YAML document.

    Returns:
        What it declares.

    Raises:
        ValidationError: It is too large, not YAML, or outside the schema; ``field`` names what.
    """
    if len(text) > MAX_DECLARATION_BYTES:
        raise ValidationError(
            f"The declaration is larger than {MAX_DECLARATION_BYTES // 1024} KiB",
            details="A declaration is a few lines.",
            field="declaration",
        )
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError(
            "The declaration is not valid YAML", details=str(exc), field="declaration"
        ) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise _fail("declaration", "must be a block of keys", f"Accepted: {', '.join(TOP_KEYS)}.")
    for key in data:
        if key not in TOP_KEYS:
            raise _fail(
                str(key), "is not a key of the declaration", f"Accepted: {', '.join(TOP_KEYS)}."
            )

    health = limits = keep = port = None
    if data.get("app") is not None:
        app = _block(data["app"], "app", _APP_KEYS)
        if app.get("health") is not None:
            raw = _block(app["health"], "app.health", _HEALTH_KEYS)
            checks: tuple[tuple[str, Callable[[Any], Any]], ...] = (
                ("path", check_health_path),
                ("expect", check_health_expect),
                ("timeout", check_health_timeout),
            )
            health = {}
            for key, check in checks:
                try:
                    health[key] = None if raw.get(key) is None else check(raw[key])
                except ValidationError as exc:
                    exc.field = f"app.health.{key}"
                    raise
        if app.get("limits") is not None:
            raw = _block(app["limits"], "app.limits", _LIMIT_KEYS)
            try:
                limits = ResourceLimits(**{key: raw.get(key) for key in _LIMIT_KEYS}).validated()
            except (TypeError, ValidationError) as exc:
                raise _fail(
                    "app.limits",
                    "has a limit a unit cannot run under",
                    getattr(exc, "message", str(exc)),
                ) from exc
        if app.get("keep_releases") is not None:
            keep = app["keep_releases"]
            if not isinstance(keep, int) or isinstance(keep, bool) or not 1 <= keep <= 50:
                raise _fail("app.keep_releases", "must be a number from 1 to 50")
        if app.get("port") is not None:
            port = app["port"]
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                raise _fail("app.port", "must be a port number")

    source = None
    if data.get("source") is not None:
        raw = _block(data["source"], "source", _SOURCE_KEYS)
        if isinstance(raw.get("lfs"), bool):
            # YAML 1.1 reads a bare on and off as booleans, and lfs: off is what people write.
            raw["lfs"] = "on" if raw["lfs"] else "off"
        source = app_source_store.check_options(**raw)

    compose = None
    if data.get("compose") is not None:
        raw = _block(data["compose"], "compose", _COMPOSE_KEYS)
        given = {("compose_file" if key == "file" else key): value for key, value in raw.items()}
        compose = app_compose_store.check_options(**given)

    build = hooks = None
    if data.get("build") is not None:
        build = _document("build", data["build"])
        parse_build_document(build, source="operator")
    if data.get("hooks") is not None:
        hooks = _document("hooks", data["hooks"])
        parse_hooks_document(hooks, source="operator")

    env = _env(data["env"]) if data.get("env") is not None else None

    aliases = redirects = None
    if data.get("domains") is not None:
        raw = _block(data["domains"], "domains", _DOMAIN_KEYS)
        aliases = _names(raw.get("aliases"), "domains.aliases")
        redirects = _names(raw.get("redirects"), "domains.redirects")
        both = set(aliases) & set(redirects)
        if both:
            raise _fail("domains", f"names {', '.join(sorted(both))} as an alias and a redirect")

    tasks = _tasks(data["tasks"]) if data.get("tasks") is not None else None
    ignored = tuple(key for key in READ_ELSEWHERE if key in data)
    return Declaration(
        health=health,
        limits=limits,
        keep_releases=keep,
        port=port,
        source=source,
        compose=compose,
        build=build,
        hooks=hooks,
        env=env,
        aliases=aliases,
        redirects=redirects,
        tasks=tasks,
        ignored=ignored,
    )


# Writing -----------------------------------------------------------------------------------


def export_declaration(domain: str, *, cron: CronManager | None = None) -> str:
    """
    Write a declaration from an application that is running.

    Everything a declaration can say, read through the reader that owns each part: the export
    document for the store's settings, the stores of the build block, the hooks, the source
    options and the Compose settings. Nothing the application's defaults already say is written,
    and secret variables are never written (their names are listed in a comment).

    Args:
        domain: The application's domain.
        cron: Cron manager to list tasks with; tests pass one.

    Returns:
        The YAML text, with a header that says how to apply it.

    Raises:
        NoustError: Nothing is deployed at ``domain``.
    """
    document = export_app(domain, cron=cron)
    app = document["app"]
    declared: dict[str, Any] = {}

    section: dict[str, Any] = {}
    health = {key: value for key, value in app["health"].items() if value is not None}
    if health:
        section["health"] = health
    limits = {key: value for key, value in app["limits"].items() if value is not None}
    if limits:
        section["limits"] = limits
    if app["layout"] == "releases" and app.get("keep_releases"):
        section["keep_releases"] = app["keep_releases"]
    if section:
        declared["app"] = section

    source = document.get("source_options") or {}
    if source:
        declared["source"] = {key: value for key, value in source.items() if value is not None}

    store = get_store()
    if app["app_type"] in ("docker-compose", "container"):
        options = app_compose_store.get_options(domain, store=store)
        if options.customised:
            compose = options.to_dict()
            compose.pop("customised", None)
            if "compose_file" in compose:
                compose["file"] = compose.pop("compose_file")
            declared["compose"] = {key: value for key, value in compose.items() if value}

    if app.get("build"):
        declared["build"] = yaml.safe_load(app["build"]).get("build", {})
    hooks = store.get_app_hooks(domain)
    if hooks:
        declared["hooks"] = yaml.safe_load(hooks).get("hooks", {})

    env = {
        name: entry["value"]
        for name, entry in (document.get("env") or {}).items()
        if not entry.get("secret") and entry.get("value") is not None
    }
    withheld = sorted(
        name for name, entry in (document.get("env") or {}).items() if entry.get("secret")
    )
    if env:
        declared["env"] = env

    names = document.get("domains") or {}
    if names.get("aliases") or names.get("redirects"):
        declared["domains"] = {key: names[key] for key in _DOMAIN_KEYS if names.get(key)}

    tasks = [
        {key: value for key, value in task.items() if value not in (None, True) or key == "name"}
        for task in document.get("cron") or []
    ]
    if tasks:
        declared["tasks"] = tasks

    header = [
        f"# Generated by: noust app export --as-yaml {domain}",
        f"# Apply it with: noust app apply {domain} noust.yaml",
        "# Only what differs from the defaults is written. build and hooks are the operator's",
        "# own, so applying this file makes them the operator's again.",
    ]
    if withheld:
        header.append(
            f"# Secret variables are not here (set them with noust env): {', '.join(withheld)}"
        )
    body = (
        yaml.safe_dump(declared, sort_keys=False, default_flow_style=False) if declared else "{}\n"
    )
    return "\n".join(header) + "\n" + body


# Planning ----------------------------------------------------------------------------------


def _current_domains(domain: str) -> tuple[list[str], list[str]]:
    """
    Args:
        domain: The application's domain.

    Returns:
        Its alias names and its redirect names, as the store has them.
    """
    records = get_store().list_domains(domain)
    aliases = sorted(r.domain for r in records if r.kind == DomainKind.ALIAS.value)
    redirects = sorted(r.domain for r in records if r.kind == DomainKind.REDIRECT.value)
    www = f"www.{domain}"
    return aliases, [name for name in redirects if name != www]


def _cron_jobs_of(domain: str, cron: CronManager) -> dict[str, dict[str, Any]]:
    """
    Args:
        domain: The application's domain.
        cron: The cron manager.

    Returns:
        Its jobs by name.
    """
    return {job["name"]: job for job in cron.list_jobs() if job.get("app_domain") == domain}


def plan_declaration(
    domain: str,
    declaration: Declaration,
    *,
    prune: bool = False,
    cron: CronManager | None = None,
) -> ApplyPlan:
    """
    Say what applying a declaration would change, against what the application is now.

    Args:
        domain: The application's domain.
        declaration: A checked declaration.
        prune: Remove the aliases, redirects and tasks the file does not name.
        cron: Cron manager; tests pass one.

    Returns:
        The plan.

    Raises:
        NoustError: The application is unknown.
        ValidationError: A section sets what the application's type cannot do.
    """
    store = get_store()
    app = store.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    changes: list[Change] = []
    unchanged: list[str] = []
    notes: list[str] = []

    def note(section: str, same: bool, description: str) -> None:
        if same:
            unchanged.append(section)
        else:
            changes.append(Change(section, description, section in ROOT_EQUIVALENT))

    d = declaration
    if d.health is not None:
        now = {"path": app.health_path, "expect": app.health_expect, "timeout": app.health_timeout}
        note("health", d.health == now, f"health check: {_compact(now)} -> {_compact(d.health)}")
    if d.limits is not None:
        now_limits = ResourceLimits.of(app)
        note(
            "limits",
            d.limits == now_limits,
            f"limits: {_limits_text(now_limits)} -> {_limits_text(d.limits)}",
        )
    if d.keep_releases is not None:
        if app.layout != "releases":
            notes.append("app.keep_releases is not applied: the application is not on releases.")
        else:
            note(
                "keep_releases",
                d.keep_releases == app.keep_releases,
                f"keep releases: {app.keep_releases} -> {d.keep_releases}",
            )
    if d.port is not None and d.port != app.port:
        notes.append(
            f"app.port {d.port} is not applied: the port is {app.port}, chosen when the "
            "application was created."
        )
    if d.source is not None:
        current = app_source_store.get_options(domain, store=store).to_dict()
        wanted = {
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in d.source.items()
        }
        differing = {key: value for key, value in wanted.items() if current.get(key) != value}
        note("source", not differing, f"source options: {_compact(differing)}")
    if d.compose is not None:
        if app.app_type not in ("docker-compose", "container"):
            notes.append("compose is not applied: the application is not a Compose stack.")
        else:
            current = app_compose_store.get_options(domain, store=store).to_dict()
            wanted = {
                key: (list(value) if isinstance(value, tuple) else value)
                for key, value in d.compose.items()
            }
            differing = {key: value for key, value in wanted.items() if current.get(key) != value}
            note("compose", not differing, f"compose settings: {_compact(differing)}")
    if d.build is not None:
        built = parse_build_document(d.build, source="operator")
        refuse_unsupported(built, app.app_type)
        stored = app_build_store.get_document(domain, store=store)
        same = stored is not None and _build_key(
            parse_build_document(stored, source="operator")
        ) == _build_key(built)
        note("build", same, f"build block: set the operator's ({', '.join(built.declared_keys)})")
    if d.hooks is not None:
        parsed = parse_hooks_document(d.hooks, source="operator")
        stored_hooks = store.get_app_hooks(domain)
        same = stored_hooks is not None and _hooks_key(
            parse_hooks_document(stored_hooks, source="operator")
        ) == _hooks_key(parsed)
        count = sum(len(parsed.phase(phase)) for phase in ALL_PHASES)
        note("hooks", same, f"deploy hooks: set the operator's ({count} hook(s))")
    if d.env is not None:
        current_env = read_app_env(app)
        differing_env = sorted(
            name for name, value in d.env.items() if current_env.get(name) != value
        )
        note(
            "env",
            not differing_env,
            f"environment: set {', '.join(differing_env)} (restart to use it)",
        )
    if d.aliases is not None or d.redirects is not None:
        aliases, redirects = _current_domains(domain)
        add_alias = [n for n in d.aliases or () if n not in aliases]
        add_redirect = [n for n in d.redirects or () if n not in redirects]
        drop = (
            [n for n in aliases if n not in (d.aliases or ())]
            if prune and d.aliases is not None
            else []
        ) + (
            [n for n in redirects if n not in (d.redirects or ())]
            if prune and d.redirects is not None
            else []
        )
        parts = (
            [f"add alias {n}" for n in add_alias]
            + [f"add redirect {n}" for n in add_redirect]
            + [f"remove {n}" for n in drop]
        )
        note("domains", not parts, f"domains: {', '.join(parts)}")
    if d.tasks is not None:
        jobs = _cron_jobs_of(domain, cron or CronManager())
        task_parts: list[str] = []
        for task in d.tasks:
            existing = jobs.get(task["name"])
            if existing is None:
                task_parts.append(f"create {task['name']} ({task['schedule']})")
            elif _task_differs(existing, task):
                task_parts.append(f"change {task['name']}")
        if prune:
            task_parts += [
                f"remove {name}"
                for name in sorted(jobs)
                if name not in {t["name"] for t in d.tasks}
            ]
        note("tasks", not task_parts, f"tasks: {', '.join(task_parts)}")
    for key in d.ignored:
        notes.append(f"{key} is read from the repository; apply leaves it alone.")
    return ApplyPlan(domain, changes, unchanged, notes, d, prune)


def _compact(values: Mapping[str, Any]) -> str:
    """
    Args:
        values: Settings.

    Returns:
        ``key=value`` pairs, ``default`` for a value that is not set.
    """
    return ", ".join(
        f"{key}={'default' if value is None else value}" for key, value in values.items()
    )


def _limits_text(limits: ResourceLimits) -> str:
    """
    Args:
        limits: Limits.

    Returns:
        Them as a line, ``none`` for a limit that is not set.
    """
    return (
        f"memory {limits.memory_max_mb or 'none'} MB, cpu {limits.cpu_quota_percent or 'none'} %, "
        f"tasks {limits.tasks_max or 'none'}"
    )


def _build_key(block: BuildConfig) -> dict[str, Any]:
    """
    Args:
        block: A build block.

    Returns:
        What makes two blocks the same, leaving the label out.
    """
    return block.to_mapping()


def _hooks_key(hooks: HookSet) -> Any:
    """
    Args:
        hooks: A set of hooks.

    Returns:
        What makes two sets the same, leaving the label out.
    """
    data = hooks.to_dict()
    data.pop("source", None)
    return data


def _task_differs(existing: Mapping[str, Any], task: Mapping[str, Any]) -> bool:
    """
    Args:
        existing: A job as the cron manager lists it.
        task: A declared task.

    Returns:
        Whether the declared task changes the job.
    """
    return (
        (existing.get("command") or "") != task["command"]
        or (existing.get("on_calendar") or "") != _calendar(task["schedule"])
        or bool(existing.get("enabled")) != bool(task["enabled"])
        or (task["user"] is not None and (existing.get("user") or None) != task["user"])
        or (
            task["working_directory"] is not None
            and (existing.get("working_directory") or None) != task["working_directory"]
        )
    )


def _calendar(schedule: str) -> str:
    """
    Args:
        schedule: An alias or a calendar expression.

    Returns:
        The expression the cron manager lists for it.
    """
    from noust.managers.cron_manager import SCHEDULE_ALIASES

    return SCHEDULE_ALIASES.get(schedule, schedule)


# Applying ----------------------------------------------------------------------------------


def apply_declaration(
    plan: ApplyPlan,
    *,
    actor: str = "cli",
    logger: Logger | None = None,
    cron: CronManager | None = None,
) -> ApplyReport:
    """
    Apply a plan, section by section, through the managers that own each part.

    One section that is refused (a domain another application has, a limit the unit cannot run
    under) is reported and the rest go on, so one part never costs the others. The whole is
    one operation on the application: nothing else changes it meanwhile.

    Args:
        plan: What :func:`plan_declaration` decided.
        actor: Who asked, for the audit trail and the records of each part.
        logger: Where progress goes.
        cron: Cron manager; tests pass one.

    Returns:
        What was applied and what was not.
    """
    log = logger or Logger()
    domain = plan.domain
    declaration = plan.declaration
    report = ApplyReport(domain=domain)
    wanted = {change.section for change in plan.changes}

    def attempt(part: str, action: Callable[[], str | None]) -> None:
        try:
            note = action()
        except _STEP_ERRORS as exc:
            detail = exc.message if isinstance(exc, NoustError) else str(exc)
            if isinstance(exc, NoustError) and exc.details:
                detail = f"{detail}. {exc.details}"
            log.warning(f"Not applied: {part}: {detail}")
            report.steps.append(ImportStep(part, False, detail))
            return
        if note:
            log.warning(f"{part}: {note}")
            report.steps.append(ImportStep(part, False, note))
        else:
            log.substep(f"Applied {part}")
            report.steps.append(ImportStep(part, True))

    with app_lock(domain, "apply declaration"):
        if "health" in wanted and declaration.health is not None:
            health = declaration.health
            attempt(
                "health check",
                lambda: _none(
                    set_health_check(
                        domain,
                        path=health["path"],
                        expect=health["expect"],
                        timeout=health["timeout"],
                    )
                ),
            )
        if "limits" in wanted and declaration.limits is not None:
            limits = declaration.limits
            attempt("limits", lambda: _none(set_resource_limits(domain, limits, logger=log)))
        if "keep_releases" in wanted and declaration.keep_releases is not None:
            keep = declaration.keep_releases
            attempt(
                f"keep {keep} releases",
                lambda: _none(set_release_retention(domain, keep, logger=log)),
            )
        if "source" in wanted and declaration.source is not None:
            source = declaration.source
            attempt(
                "source options",
                lambda: _none(app_source_store.set_options(domain, actor=actor, **source)),
            )
        if "compose" in wanted and declaration.compose is not None:
            compose = declaration.compose
            attempt("compose settings", lambda: _none(change_compose(domain, compose, actor=actor)))
        if "build" in wanted and declaration.build is not None:
            build = declaration.build
            attempt("build block", lambda: _none(set_operator_build(domain, build, actor=actor)))
        if "hooks" in wanted and declaration.hooks is not None:
            hooks = declaration.hooks
            attempt("deploy hooks", lambda: _none(set_operator_hooks(domain, hooks, actor=actor)))
        if "env" in wanted and declaration.env is not None:
            env = declaration.env
            attempt("environment", lambda: _write_env(domain, env, log))
        if "domains" in wanted:
            _apply_domains(plan, attempt, log)
        if "tasks" in wanted and declaration.tasks is not None:
            _apply_tasks(plan, attempt, cron or CronManager(), actor)

    audit.record(
        "apps.apply",
        target=f"app:{domain}",
        details={
            "sections": sorted(wanted),
            "applied": [step.part for step in report.steps if step.applied],
            "not_applied": [step.part for step in report.not_applied],
            "prune": plan.prune,
        },
    )
    return report


def _none(_result: Any) -> None:
    """Discard what a manager returned: the step only reports success."""
    return None


def _write_env(domain: str, values: Mapping[str, str], log: Logger) -> str | None:
    """
    Set the declared variables, keeping every other.

    Args:
        domain: The application.
        values: The declared variables.
        log: Where a failed hand-over is reported.

    Returns:
        A note: the application keeps running with the old values until it is restarted.
    """
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(f"Application not found: {domain}")
    current = read_app_env(app)
    write_app_env(app, {**current, **values}, logger=log)
    return None


def _apply_domains(
    plan: ApplyPlan, attempt: Callable[[str, Callable[[], str | None]], None], log: Logger
) -> None:
    """
    Add the names the file declares, and remove the others when pruning.

    Args:
        plan: The plan.
        attempt: Runs one part and records the outcome.
        log: Where the managers report.
    """
    domain = plan.domain
    declaration = plan.declaration
    aliases, redirects = _current_domains(domain)
    added = False
    for kind, wanted, have in (
        (DomainKind.ALIAS.value, declaration.aliases, aliases),
        (DomainKind.REDIRECT.value, declaration.redirects, redirects),
    ):
        if wanted is None:
            continue
        for name in wanted:
            if name in have:
                continue

            def add(name: str = name, kind: str = kind) -> None:
                nonlocal added
                domain_changes.add_domain(domain, name, kind, issue_cert=False, logger=log)
                added = True

            attempt(f"{kind} {name}", add)
        if plan.prune:
            for name in have:
                if name not in wanted:

                    def drop(name: str = name) -> None:
                        domain_changes.remove_domain(domain, name, logger=log)

                    attempt(f"remove {name}", drop)
    app = get_store().get_app(domain)
    if added and app is not None and app.ssl_enabled:

        def certificate() -> str | None:
            change = domain_changes.issue_certificate(domain, logger=log)
            if not change.certificate_issued:
                return (
                    f"The certificate does not cover every name yet: {change.certificate_error}. "
                    "Point their DNS here and add each name again with 'noust domain add'."
                )
            return None

        attempt("certificate for every domain", certificate)


def _apply_tasks(
    plan: ApplyPlan,
    attempt: Callable[[str, Callable[[], str | None]], None],
    cron: CronManager,
    actor: str,
) -> None:
    """
    Create or change the declared tasks, and remove the others when pruning.

    Args:
        plan: The plan.
        attempt: Runs one part and records the outcome.
        cron: The cron manager.
        actor: Who asked, for the audit record of each job.
    """
    domain = plan.domain
    declared = plan.declaration.tasks or ()
    jobs = _cron_jobs_of(domain, cron)
    for task in declared:
        existing = jobs.get(task["name"])
        if existing is not None and not _task_differs(existing, task):
            continue

        def write(task: Mapping[str, Any] = task, known: bool = existing is not None) -> str | None:
            if not known and cron.get_job(task["name"]) is not None:
                return (
                    f"A cron job named {task['name']} already exists on this server and is not "
                    f"this application's; declare this one under another name."
                )
            audit_log.info(
                "apply_cron_job name=%s schedule=%s session=%s user=%s via=apply app=%s",
                task["name"],
                task["schedule"],
                actor,
                task["user"] or Config().service_user,
                domain,
            )
            cron.create_job(
                CronJob(
                    name=task["name"],
                    command=task["command"],
                    schedule=task["schedule"],
                    user=task["user"],
                    working_directory=task["working_directory"],
                    app_domain=domain,
                )
            )
            if not task["enabled"]:
                cron.disable_job(task["name"])
            return None

        attempt(f"task {task['name']}", write)
    if plan.prune:
        names = {task["name"] for task in declared}
        for name in sorted(jobs):
            if name not in names:

                def drop(name: str = name) -> None:
                    cron.delete_job(name)

                attempt(f"remove task {name}", drop)


__all__ = [
    "MAX_DECLARATION_BYTES",
    "ROOT_EQUIVALENT",
    "SECTIONS",
    "ApplyPlan",
    "ApplyReport",
    "Change",
    "Declaration",
    "apply_declaration",
    "export_declaration",
    "parse_declaration",
    "plan_declaration",
]
