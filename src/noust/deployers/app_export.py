# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's definition as a document, and an application made from one.

:func:`export_app` writes everything that defines an application - type,
source, branch, layout, domains, environment, health check, limits, release
retention, persistent paths, cron jobs, backup schedule, previews, blue/green
- as a versioned JSON document (``"format": "wasm-app"``). Every part is read
through the reader that already owns it: the store's getters, the domain
helpers, :func:`~noust.deployers.helpers.app_env.read_app_env`,
:meth:`~noust.managers.cron_manager.CronManager.list_jobs`. Secret values are
left out (``null`` with ``"secret": true``) unless asked for, and Noust's own
credentials never go in at all: not the webhook secret, not a backup
destination's keys, not the GitHub App's; a destination is named, and the
GitHub link is a yes or no.

:func:`plan_import` checks a document and decides what an import will do;
:func:`apply_import` does it. The application is created by the normal
deploy path, handed in by the caller as ``deploy`` (``noust create``'s
``_create_app`` on the terminal, the deploy job in the console), so an
imported application is built, gated and recorded like any other. The rest is
applied afterwards through the managers that own each part, and whatever
cannot be applied here - a backup destination this server does not have, a
database, a GitHub installation - is reported, never guessed.

:func:`proposal_document` turns another platform's configuration
(:mod:`noust.deployers.importers`) into a document, so ``noust import --deploy``
creates through this same path instead of a second one.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust import __version__
from noust.central import require_server_role
from noust.core.config import REDACTED, Config
from noust.core.exceptions import DomainConflictError, NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.secret_detection import classify
from noust.core.store import App, DomainKind, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers import domains as domain_changes
from noust.deployers.bluegreen import set_zero_downtime
from noust.deployers.helpers.app_env import read_app_env
from noust.deployers.helpers.layout import LAYOUTS, RELEASES, code_path_for
from noust.deployers.importers import Proposal
from noust.deployers.importers.base import MAX_NESTING, nesting_depth
from noust.deployers.lifecycle import set_health_check, set_release_retention
from noust.managers.backup_scheduler import BackupSchedule, BackupScheduler
from noust.managers.cron_manager import CronJob, CronManager
from noust.managers.previews import enable_previews
from noust.managers.source_manager import redact_git_text
from noust.validators.domain import validate_domain
from noust.validators.environment import is_valid_env_name
from noust.validators.health import check_health_expect, check_health_path, check_health_timeout

#: What the document says it is, and the version of its shape.
FORMAT = "wasm-app"
VERSION = 1

#: Variables the unit sets itself (see ``helpers/app_env.py``): never exported,
#: because a deploy refuses them in the environment it is given.
MANAGED_ENV = frozenset({"PORT", "NODE_ENV"})

#: Largest document an import reads; a real one is a few kilobytes.
MAX_DOCUMENT_SIZE = 1024 * 1024

#: Where every cron job an import creates is recorded, as ``POST /api/cron``
#: records the ones created there.
audit_log = logging.getLogger("noust.audit")


# Export ---------------------------------------------------------------------


def _known(domain: str) -> App:
    """
    Read an application's row, or say it is not deployed.

    Args:
        domain: The application's domain.

    Returns:
        The row.

    Raises:
        NoustError: Nothing is deployed there.
    """
    app = get_store().get_app(validate_domain(domain))
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    return app


def export_app(
    domain: str, *, with_secrets: bool = False, cron: CronManager | None = None
) -> dict[str, Any]:
    """
    Describe an application as an export document.

    Args:
        domain: The application's domain.
        with_secrets: Include the values of secret variables. Without it they
            are ``null`` with ``"secret": true``, decided by the one
            classifier (:func:`~noust.core.secret_detection.classify`) with the
            application's own marks.
        cron: Cron manager to list jobs with; tests pass one.

    Returns:
        The document, keys in a fixed order and every collection sorted, so
        two exports of the same application are identical but for
        ``exported_at``.

    Raises:
        NoustError: Nothing is deployed at ``domain``.
    """
    app = _known(domain)
    store = get_store()
    records = store.list_domains(app.domain)
    www = f"www.{app.domain}"
    aliases = sorted(r.domain for r in records if r.kind == DomainKind.ALIAS.value)
    redirects = sorted(r.domain for r in records if r.kind == DomainKind.REDIRECT.value)

    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "wasm_version": __version__,
        "secrets_included": with_secrets,
        "app": {
            "domain": app.domain,
            "app_type": app.app_type,
            "source": _export_source(app.source, with_secrets=with_secrets),
            "branch": app.branch,
            "layout": app.layout,
            "port": app.port,
            "webserver": app.webserver,
            "ssl": bool(app.ssl_enabled),
            "include_www": www in redirects,
            "persistent_paths": sorted(app.persistent_paths),
            "keep_releases": app.keep_releases,
            "limits": {
                "memory_max_mb": app.memory_max_mb,
                "cpu_quota_percent": app.cpu_quota_percent,
                "tasks_max": app.tasks_max,
            },
            "health": {
                "path": app.health_path,
                "expect": app.health_expect,
                "timeout": app.health_timeout,
            },
            "zero_downtime": {
                "enabled": bool(app.zero_downtime),
                "drain_seconds": app.drain_seconds,
            },
        },
        "domains": {"aliases": aliases, "redirects": [d for d in redirects if d != www]},
        "env": _export_env(app, with_secrets=with_secrets),
        "env_secret_marks": dict(sorted(app.env_secret_marks.items())),
        "cron": _export_cron(app, cron or CronManager()),
        "backup": _export_backup(app),
        "previews": _export_previews(app),
        "github": {"installation_linked": app.github_installation_id is not None},
        "databases": sorted(
            (
                {"engine": database.engine, "name": database.name}
                for database in store.list_databases(app_id=app.id)
            ),
            key=lambda entry: (entry["engine"], entry["name"]),
        ),
    }


def _export_source(source: str | None, *, with_secrets: bool) -> str | None:
    """
    Describe where an application deploys from, without its credentials.

    The userinfo of a URL always goes (a repository token is Noust's to keep,
    not the document's). Without ``with_secrets`` the query and the fragment
    of an http(s) URL go too: an archive link carries its token there
    (``?token=``, a presigned ``X-Amz-Signature``). They are replaced by
    ``***``, so :func:`plan_import` asks for the source again, as it does
    for ``***@``, instead of deploying from a broken URL.

    Args:
        source: The application's source.
        with_secrets: Keep the query and the fragment.

    Returns:
        The source to write, or None when it has none.
    """
    if not source:
        return None
    return redact_git_text(source if with_secrets else strip_url_secrets(source))


def strip_url_secrets(source: str) -> str:
    """
    Replace the query and the fragment of an http(s) URL with ``***``.

    Args:
        source: A source: URL, archive URL or path.

    Returns:
        The source, its query and fragment replaced when it is an http(s)
        URL that has them; anything else unchanged.
    """
    if not source.lower().startswith(("http://", "https://")):
        return source
    cut = min((i for i in (source.find("?"), source.find("#")) if i != -1), default=-1)
    if cut == -1:
        return source
    rest = source[cut:]
    query = f"?{REDACTED}" if rest.startswith("?") else ""
    fragment = f"#{REDACTED}" if "#" in rest else ""
    return source[:cut] + query + fragment


def display_source(source: str) -> str:
    """
    Show a source in a plan, a log or a job, without a credential in it.

    Args:
        source: The source.

    Returns:
        The source, its userinfo, query and fragment replaced.
    """
    return redact_git_text(strip_url_secrets(source))


def _source_was_stripped(source: str) -> bool:
    """
    Tell whether an exported source had its credentials taken out.

    Args:
        source: The source the document names.

    Returns:
        True when its userinfo, query or fragment is the ``***`` an export
        leaves.
    """
    return f"{REDACTED}@" in source or source.endswith((f"?{REDACTED}", f"#{REDACTED}"))


def _export_env(app: App, *, with_secrets: bool) -> dict[str, dict[str, Any]]:
    """
    Describe an application's ``.env``.

    Args:
        app: The application.
        with_secrets: Keep secret values.

    Returns:
        Name to ``{"secret", "value"}``, sorted by name.
    """
    values = read_app_env(app)
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(values):
        if name in MANAGED_ENV:
            continue
        secret = classify(name, values[name], app.env_secret_marks).secret
        out[name] = {
            "secret": secret,
            "value": values[name] if with_secrets or not secret else None,
        }
    return out


def _export_cron(app: App, cron: CronManager) -> list[dict[str, Any]]:
    """
    Describe the cron jobs that belong to an application.

    A working directory inside the application, and the user every job runs
    as by default, are left out: they are what a job gets anyway, and they
    name paths and accounts of this server.

    Args:
        app: The application.
        cron: The cron manager.

    Returns:
        One entry per job, sorted by name.
    """
    own_paths = (str(code_path_for(app)), app.app_path)
    default_user = Config().service_user
    jobs = []
    for job in cron.list_jobs():
        if job.get("app_domain") != app.domain:
            continue
        directory = job.get("working_directory") or None
        if directory and any(directory == p or directory.startswith(f"{p}/") for p in own_paths):
            directory = None
        user = job.get("user") or None
        jobs.append(
            {
                "name": job["name"],
                "schedule": job.get("on_calendar") or "",
                "command": job.get("command") or "",
                "enabled": bool(job.get("enabled")),
                "user": None if user == default_user else user,
                "working_directory": directory,
            }
        )
    return sorted(jobs, key=lambda entry: entry["name"])


def _export_backup(app: App) -> dict[str, Any] | None:
    """
    Describe an application's backup schedule.

    Destinations are named with their retention and nothing else: their
    credentials are secret files of this server.

    Args:
        app: The application.

    Returns:
        The schedule, or None when it has none.
    """
    record = get_store().get_backup_schedule(app.domain)
    if record is None:
        return None
    return {
        "schedule": record.schedule,
        "include_databases": record.include_databases,
        "retention_count": record.retention_count,
        "retention_days": record.retention_days,
        "destinations": [
            {
                "name": str(destination.get("name")),
                "retention_count": destination.get("retention_count"),
                "retention_days": destination.get("retention_days"),
            }
            for destination in record.destinations
            if destination.get("name")
        ],
    }


def _export_previews(app: App) -> dict[str, Any] | None:
    """
    Describe an application's pull request previews.

    Args:
        app: The application.

    Returns:
        The settings, or None when previews are off.
    """
    settings = get_store().get_preview_settings(app.domain)
    if settings is None:
        return None
    return {
        "base_domain": settings.base_domain,
        "max_previews": settings.max_previews,
        "ttl_hours": settings.ttl_hours,
        "allow_bots": settings.allow_bots,
        "exclude_env": sorted(settings.exclude_env),
    }


def dumps(document: Mapping[str, Any]) -> str:
    """
    Write a document the way every export is written.

    Args:
        document: The document.

    Returns:
        Indented JSON with a trailing newline, keys in the document's order.
    """
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


# Validation -----------------------------------------------------------------


def _fail(where: str, what: str) -> ValidationError:
    """
    Build the refusal for one field of a document.

    Args:
        where: The field, as a path (``app.port``).
        what: What is wrong with it.

    Returns:
        The error.
    """
    return ValidationError(
        f"The export document is not valid: {where} {what}",
        details="Export it again with 'noust app export', or fix the field.",
        field=where,
    )


def _object(data: Any, where: str, *, optional: bool = False) -> dict[str, Any]:
    """Check an object field; an absent optional one reads as empty."""
    if data is None and optional:
        return {}
    if not isinstance(data, dict):
        raise _fail(where, "must be an object")
    return data


def _text(data: Mapping[str, Any], key: str, where: str, *, required: bool = False) -> str | None:
    """Check a string field."""
    value = data.get(key)
    if value is None:
        if required:
            raise _fail(f"{where}.{key}", "is missing")
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise _fail(f"{where}.{key}", "must be a string")
    return value


def _number(data: Mapping[str, Any], key: str, where: str) -> int | None:
    """Check a whole-number field."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(f"{where}.{key}", "must be a whole number")
    return int(value)


def _flag(data: Mapping[str, Any], key: str, where: str, default: bool = False) -> bool:
    """Check a boolean field."""
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _fail(f"{where}.{key}", "must be true or false")
    return value


def _names(data: Mapping[str, Any], key: str, where: str) -> list[str]:
    """Check a list-of-strings field."""
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _fail(f"{where}.{key}", "must be a list of strings")
    return list(value)


def validate_document(data: Any) -> dict[str, Any]:
    """
    Check a document's shape and fill in what an older export left out.

    Unknown keys are ignored, so a document from a later release of the same
    version still imports. Every optional section an older or a hand-written
    document leaves out is filled in with its empty value, on a copy, so the
    code after this never meets a missing key.

    Args:
        data: The parsed JSON.

    Returns:
        A copy of the document, every section present.

    Raises:
        ValidationError: It is not a ``wasm-app`` document of a version this
            release reads, or a field has the wrong type.
    """
    doc = dict(_object(data, "document"))
    if doc.get("format") != FORMAT:
        raise ValidationError(
            "This is not a Noust application export",
            details=f'An export says "format": "{FORMAT}"; make one with \'noust app export\'.',
            field="format",
        )
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise _fail("version", "must be a whole number")
    if version > VERSION:
        raise ValidationError(
            f"The export is version {version}; this Noust reads version {VERSION}",
            details=f"It was made by Noust {doc.get('wasm_version') or 'a newer release'}. "
            "Upgrade Noust on this server to import it.",
            field="version",
        )
    if version < 1:
        raise _fail("version", "must be 1 or more")

    app = dict(_object(doc.get("app"), "app"))
    doc["app"] = app
    _text(app, "domain", "app", required=True)
    _text(app, "app_type", "app", required=True)
    for key in ("source", "branch", "layout", "webserver"):
        _text(app, key, "app")
    layout = app.get("layout")
    if layout is not None and layout not in LAYOUTS:
        raise _fail("app.layout", f"must be one of {', '.join(LAYOUTS)}")
    for key in ("port", "keep_releases"):
        _number(app, key, "app")
    for key in ("ssl", "include_www"):
        _flag(app, key, "app", default=key == "ssl")
    app["persistent_paths"] = _names(app, "persistent_paths", "app")
    limits = app["limits"] = _object(app.get("limits"), "app.limits", optional=True)
    for key in ("memory_max_mb", "cpu_quota_percent", "tasks_max"):
        _number(limits, key, "app.limits")
    health = app["health"] = _object(app.get("health"), "app.health", optional=True)
    _text(health, "path", "app.health")
    _text(health, "expect", "app.health")
    _number(health, "timeout", "app.health")
    zero = app["zero_downtime"] = _object(
        app.get("zero_downtime"), "app.zero_downtime", optional=True
    )
    _flag(zero, "enabled", "app.zero_downtime")
    _number(zero, "drain_seconds", "app.zero_downtime")

    domains = _object(doc.get("domains"), "domains", optional=True)
    doc["domains"] = {
        "aliases": _names(domains, "aliases", "domains"),
        "redirects": _names(domains, "redirects", "domains"),
    }

    env = doc["env"] = _object(doc.get("env"), "env", optional=True)
    for name, entry in env.items():
        if not is_valid_env_name(name):
            raise _fail(f"env.{name}", "is not an environment variable name")
        entry = _object(entry, f"env.{name}")
        _flag(entry, "secret", f"env.{name}")
        _text(entry, "value", f"env.{name}")
    marks = _object(doc.get("env_secret_marks"), "env_secret_marks", optional=True)
    if not all(isinstance(mark, bool) for mark in marks.values()):
        raise _fail("env_secret_marks", "must map names to true or false")
    doc["env_secret_marks"] = marks

    cron = doc["cron"] = doc.get("cron") or []
    if not isinstance(cron, list):
        raise _fail("cron", "must be a list")
    for index, job in enumerate(cron):
        where = f"cron[{index}]"
        job = _object(job, where)
        for key in ("name", "schedule", "command"):
            _text(job, key, where, required=True)
        _flag(job, "enabled", where, default=True)
        _text(job, "user", where)
        _text(job, "working_directory", where)

    if doc.get("backup") is not None:
        backup = _object(doc["backup"], "backup")
        _text(backup, "schedule", "backup", required=True)
        _flag(backup, "include_databases", "backup", default=True)
        _number(backup, "retention_count", "backup")
        _number(backup, "retention_days", "backup")
        destinations = backup.get("destinations") or []
        if not isinstance(destinations, list):
            raise _fail("backup.destinations", "must be a list")
        for index, destination in enumerate(destinations):
            where = f"backup.destinations[{index}]"
            destination = _object(destination, where)
            _text(destination, "name", where, required=True)
            _number(destination, "retention_count", where)
            _number(destination, "retention_days", where)

    if doc.get("previews") is not None:
        previews = _object(doc["previews"], "previews")
        _text(previews, "base_domain", "previews", required=True)
        _number(previews, "max_previews", "previews")
        _number(previews, "ttl_hours", "previews")
        _flag(previews, "allow_bots", "previews")
        _names(previews, "exclude_env", "previews")

    doc.setdefault("backup", None)
    doc.setdefault("previews", None)
    github = doc["github"] = _object(doc.get("github"), "github", optional=True)
    _flag(github, "installation_linked", "github")
    databases = doc["databases"] = doc.get("databases") or []
    if not isinstance(databases, list):
        raise _fail("databases", "must be a list")
    for index, database in enumerate(databases):
        database = _object(database, f"databases[{index}]")
        _text(database, "engine", f"databases[{index}]", required=True)
        _text(database, "name", f"databases[{index}]")
    return doc


def load_document(text: str) -> dict[str, Any]:
    """
    Parse and check an export document.

    Args:
        text: The file's content.

    Returns:
        The document, as :func:`validate_document` returns it.

    Raises:
        ValidationError: It is too large, not JSON, or not a valid document.
    """
    if len(text) > MAX_DOCUMENT_SIZE:
        raise ValidationError(
            f"The export document is larger than {MAX_DOCUMENT_SIZE} bytes",
            details="An export is a few kilobytes; check the file is the one 'noust app "
            "export' wrote.",
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError("The export document is not JSON", details=str(exc)) from exc
    except RecursionError as exc:
        raise ValidationError(
            "The export document nests too deeply to be read",
            details="An export nests three levels; check the file is the one 'noust app "
            "export' wrote.",
        ) from exc
    if nesting_depth(data) > MAX_NESTING:
        raise ValidationError(
            "The export document nests too deeply to be read",
            details="An export nests three levels; check the file is the one 'noust app "
            "export' wrote.",
        )
    return validate_document(data)


# Import ---------------------------------------------------------------------


@dataclass(frozen=True)
class CreateSpec:
    """
    What the normal deploy path is asked to create.

    Attributes:
        domain: The new application's domain.
        source: Git URL, archive URL or directory.
        app_type: The type, or ``auto``.
        branch: Git branch, or None for the default.
        layout: ``inplace``, ``releases``, or None for the server's.
        port: The port, or None to pick a free one.
        webserver: ``nginx`` or ``apache``.
        ssl: Obtain a certificate.
        include_www: Also answer on ``www.<domain>``, as a redirect.
        persistent_paths: Paths kept in ``shared/`` on releases.
        env_vars: The environment the application starts with.
        env_secret_marks: The secret marks its row starts with.
        memory_max_mb: ``MemoryMax``, or None for none.
        cpu_quota_percent: ``CPUQuota``, or None for none.
        tasks_max: ``TasksMax``, or None for none.
        health_path: What the health gate asks for from the first
            deployment, or None for the default.
        health_expect: The statuses it accepts, or None for the default.
        health_timeout: Seconds it waits, or None for the default.
    """

    domain: str
    source: str
    app_type: str = "auto"
    branch: str | None = None
    layout: str | None = None
    port: int | None = None
    webserver: str = "nginx"
    ssl: bool = True
    include_www: bool = False
    persistent_paths: tuple[str, ...] = ()
    env_vars: dict[str, str] = field(default_factory=dict, repr=False)
    env_secret_marks: dict[str, bool] = field(default_factory=dict)
    memory_max_mb: int | None = None
    cpu_quota_percent: int | None = None
    tasks_max: int | None = None
    health_path: str | None = None
    health_expect: str | None = None
    health_timeout: int | None = None

    @property
    def initial_health(self) -> tuple[str | None, str | None, int | None] | None:
        """``(path, expect, timeout)`` for the deployer, or None when all are defaults."""
        health = (self.health_path, self.health_expect, self.health_timeout)
        return health if any(value is not None for value in health) else None


@dataclass(frozen=True)
class ImportStep:
    """
    One part of an import, done or not.

    Attributes:
        part: What it is (``domain www.example.com``, ``cron nightly``).
        applied: Whether it was applied.
        detail: Why not, or what to do, when it was not.
    """

    part: str
    applied: bool
    detail: str = ""


@dataclass
class ImportPlan:
    """
    What an import will do, decided before anything changes.

    Attributes:
        domain: The domain the application is created on.
        exported_domain: The domain the document was exported from.
        create: What the deploy path is asked to create.
        steps: What is applied after it, in order, one line each.
        skipped: What will not be applied, known before starting.
        document: The checked document.
        confirm: Why the operator must say yes before it runs: the parts
            that run commands or copy variables the document chose (cron
            jobs, previews). Empty when nothing needs it.
    """

    domain: str
    exported_domain: str
    create: CreateSpec
    steps: list[str]
    skipped: list[ImportStep]
    document: dict[str, Any] = field(repr=False)
    confirm: list[str] = field(default_factory=list)


@dataclass
class ImportReport:
    """
    What an import did.

    Attributes:
        domain: The application created.
        steps: Every part, in the order it was attempted, the known skips
            last.
    """

    domain: str
    steps: list[ImportStep] = field(default_factory=list)

    @property
    def not_applied(self) -> list[ImportStep]:
        """Every part that was not applied."""
        return [step for step in self.steps if not step.applied]


def _rename(name: str, old: str, new: str) -> str:
    """
    Carry a name derived from the exported domain over to the new one.

    A domain is renamed when it is the old one or a subdomain of it. In any
    other name the old application name is replaced only where it stands as
    a whole word between dashes (``shop-example-com-nightly``), never inside
    another word: a job ``myshop-example-com-sync`` is not the application's.

    Args:
        name: A domain or a job name.
        old: The exported domain.
        new: The new domain.

    Returns:
        The name with the old domain, or its application name, replaced.
    """
    if old == new:
        return name
    if name == old or name.endswith(f".{old}"):
        return name[: len(name) - len(old)] + new
    token = re.compile(rf"(?<![A-Za-z0-9]){re.escape(domain_to_app_name(old))}(?![A-Za-z0-9])")
    return token.sub(domain_to_app_name(new), name)


def plan_import(
    document: Mapping[str, Any],
    *,
    domain: str | None = None,
    source: str | None = None,
    env: Mapping[str, str] | None = None,
) -> ImportPlan:
    """
    Check a document against this server and decide what importing it does.

    Args:
        document: The export document.
        domain: Create the application on this domain instead of the
            exported one. Names derived from the exported domain (``www.``,
            the subdomains of it among the aliases, cron job names) follow.
        source: Deploy from this source instead of the exported one; needed
            when the export's source had its credentials taken out.
        env: Values for variables, overriding the document's: how the secret
            values an export leaves out are given back.

    Returns:
        The plan.

    Raises:
        ValidationError: The document is not valid; its source cannot be
            deployed as it is; or a secret variable has no value, naming
            every one of them.
        DomainConflictError: An application is already deployed on the
            domain.
    """
    doc = validate_document(dict(document))
    app = doc["app"]
    exported = str(app["domain"])
    target = validate_domain(domain or exported)
    store = get_store()
    if store.get_app(target) is not None:
        raise DomainConflictError(
            f"An application is already deployed on {target}",
            details="Import it under another domain with --domain, or delete that one first.",
        )

    chosen_source = source or app.get("source")
    if not chosen_source:
        raise ValidationError(
            "The export names no source to deploy from", details="Give one with --source."
        )
    if _source_was_stripped(chosen_source):
        raise ValidationError(
            "The exported source had its credentials taken out",
            details="Give the repository URL again with --source, with a token if it is "
            "private, or deploy through the GitHub App.",
            field="source",
        )

    overrides = dict(env or {})
    bad = [name for name in overrides if not is_valid_env_name(name)]
    if bad:
        raise ValidationError(
            f"Not environment variable names: {', '.join(sorted(bad))}", field="env"
        )
    env_vars: dict[str, str] = {}
    missing: list[str] = []
    for name, entry in doc.get("env", {}).items():
        if name in overrides:
            env_vars[name] = overrides[name]
        elif entry.get("value") is not None:
            env_vars[name] = entry["value"]
        else:
            missing.append(name)
    for name, value in overrides.items():
        env_vars.setdefault(name, value)
    if missing:
        raise ValidationError(
            f"The export left out the value of {len(missing)} variable(s): "
            f"{', '.join(sorted(missing))}",
            details="Give them with --env-file FILE or --env NAME=VALUE (in the console, in "
            "the import form), or export again with --with-secrets.",
            field="env",
        )

    health = _checked_health(app.get("health") or {})
    limits = app.get("limits") or {}
    port = app.get("port")
    if port is not None and port in store.ports_owned_by_apps():
        port = None
    www = bool(app.get("include_www"))
    create = CreateSpec(
        domain=target,
        source=chosen_source,
        app_type=str(app["app_type"]),
        branch=app.get("branch"),
        layout=app.get("layout"),
        port=port,
        webserver=app.get("webserver") or "nginx",
        ssl=_flag(app, "ssl", "app", default=True),
        include_www=www,
        persistent_paths=tuple(app.get("persistent_paths") or ()),
        env_vars=env_vars,
        env_secret_marks=dict(doc.get("env_secret_marks") or {}),
        memory_max_mb=limits.get("memory_max_mb"),
        cpu_quota_percent=limits.get("cpu_quota_percent"),
        tasks_max=limits.get("tasks_max"),
        health_path=health["path"],
        health_expect=health["expect"],
        health_timeout=health["timeout"],
    )

    steps, skipped, confirm = _describe_steps(doc, exported, target, app.get("port"), port)
    return ImportPlan(
        domain=target,
        exported_domain=exported,
        create=create,
        steps=steps,
        skipped=skipped,
        document=doc,
        confirm=confirm,
    )


def _checked_health(health: Mapping[str, Any]) -> dict[str, Any]:
    """
    Check a document's health settings with the gate's own rules.

    Checked before anything is deployed, because the first deployment's gate
    already asks them.

    Args:
        health: ``app.health`` of the document.

    Returns:
        ``path``, ``expect`` and ``timeout``, each None or the value as the
        store keeps it.

    Raises:
        ValidationError: A value the gate could not use; ``field`` names it.
    """
    checks: tuple[tuple[str, Callable[[Any], Any]], ...] = (
        ("path", check_health_path),
        ("expect", check_health_expect),
        ("timeout", check_health_timeout),
    )
    out: dict[str, Any] = {}
    for key, check in checks:
        value = health.get(key)
        if value is None:
            out[key] = None
            continue
        try:
            out[key] = check(value)
        except ValidationError as exc:
            exc.field = f"app.health.{key}"
            raise
    return out


def _describe_steps(
    doc: dict[str, Any], exported: str, target: str, exported_port: Any, port: int | None
) -> tuple[list[str], list[ImportStep], list[str]]:
    """
    List what an import applies after the deploy, what it will not, and what needs a yes.

    Each line says what the part will do on this server, not only its name: a
    cron job is its schedule, its user, its directory and its command, and a
    preview says whether bots' pull requests deploy and which variables are
    copied. That is what the operator is agreeing to.

    Args:
        doc: The checked document.
        exported: The exported domain.
        target: The new domain.
        exported_port: The port the export named.
        port: The port the deploy will ask for.

    Returns:
        The steps, the parts known to be skipped, and the reasons the import
        needs the operator's confirmation.
    """
    store = get_store()
    app = doc["app"]
    steps = [f"deploy {target} ({app['app_type']}) from {display_source(str(app['source'] or ''))}"]
    skipped: list[ImportStep] = []
    confirm: list[str] = []
    if exported_port is not None and port is None:
        skipped.append(
            ImportStep(
                f"port {exported_port}",
                False,
                "Another application on this server has it; a free port is chosen.",
            )
        )
    for alias in doc["domains"]["aliases"]:
        steps.append(f"alias {_rename(alias, exported, target)}")
    for redirect in doc["domains"]["redirects"]:
        steps.append(f"redirect {_rename(redirect, exported, target)}")
    health = app["health"]
    if any(health.get(key) is not None for key in ("path", "expect", "timeout")):
        steps.append(
            f"health check {health.get('path') or '/'}, expecting "
            f"{health.get('expect') or 'the default statuses'}, waiting "
            f"{health.get('timeout') or 'the default'} s, from the first deployment"
        )
    if app.get("layout") == RELEASES and app.get("keep_releases") is not None:
        steps.append(f"keep {app['keep_releases']} releases")
    if doc["env_secret_marks"]:
        steps.append("secret marks")
    jobs = doc["cron"]
    if jobs:
        steps.extend(
            _cron_line(job, _rename(job["name"], exported, target), target) for job in jobs
        )
        as_root = [job["name"] for job in jobs if _runs_as_root(job)]
        confirm.append(
            f"creates {len(jobs)} cron job(s) that run commands the document chose"
            + (f", {len(as_root)} of them as root" if as_root else "")
        )
    backup = doc["backup"]
    if backup is not None:
        names = [d["name"] for d in backup.get("destinations", [])]
        steps.append(
            f"backup schedule {backup['schedule']}, "
            + ("to " + ", ".join(names) if names else "local only")
            + ("" if backup.get("include_databases", True) else ", without databases")
        )
        for destination in backup.get("destinations", []):
            if store.get_backup_destination(destination["name"]) is None:
                skipped.append(
                    ImportStep(
                        f"backup destination {destination['name']}",
                        False,
                        "This server has no destination by that name; the schedule keeps "
                        "local backups only. Add it with 'noust backup destination add', then "
                        "schedule again.",
                    )
                )
    previews = doc["previews"]
    if previews is not None:
        steps.append(_previews_line(previews))
        confirm.append(
            "enables pull request previews, which deploy code from pull requests with "
            + (
                "every variable of the application, secrets included"
                if not previews.get("exclude_env")
                else "the application's variables"
            )
            + (", bots' pull requests too" if previews.get("allow_bots") else "")
        )
    zero = app["zero_downtime"]
    if zero.get("enabled"):
        drain = zero.get("drain_seconds")
        steps.append(
            "zero-downtime: every activation starts a second instance and switches to it"
            + (f", draining the old one for {drain} s" if drain is not None else "")
        )
    for database in doc["databases"]:
        named = f"{database['name']} " if database.get("name") else ""
        skipped.append(
            ImportStep(
                f"database {named}({database['engine']})",
                False,
                "Databases are not exported, only named. Create it with 'noust db create', "
                "restore its data, and set the application's connection variables.",
            )
        )
    if doc["github"].get("installation_linked"):
        skipped.append(
            ImportStep(
                "GitHub App installation",
                False,
                "The exported application cloned through a GitHub App installation. Connect "
                "this server's GitHub App to the repository ('noust github setup') for updates to "
                "use it.",
            )
        )
    return steps, skipped, confirm


def _runs_as_root(job: Mapping[str, Any]) -> bool:
    """Whether a cron job of a document runs as root (by name or by uid)."""
    return (job.get("user") or "").strip() in ("root", "0")


def _cron_line(job: Mapping[str, Any], name: str, target: str) -> str:
    """
    Describe a cron job as it will run here.

    Args:
        job: The document's job.
        name: Its name here.
        target: The domain it is created for.

    Returns:
        ``cron NAME [SCHEDULE] as USER in DIR: COMMAND``, root and a
        directory outside the application called out.
    """
    config = Config()
    user = job.get("user") or config.service_user
    who = f"as {user}" + (" (root: the whole server)" if _runs_as_root(job) else "")
    directory = job.get("working_directory")
    app_dir = str(config.apps_directory / domain_to_app_name(target))
    if not directory:
        where = "in the application's directory"
    elif directory == app_dir or directory.startswith(f"{app_dir}/"):
        where = f"in {directory}"
    else:
        where = f"in {directory} (outside the application)"
    state = "" if job.get("enabled", True) else " (created disabled)"
    return f"cron {name} [{job['schedule']}] {who} {where}{state}: {job['command']}"


def _previews_line(previews: Mapping[str, Any]) -> str:
    """
    Describe the pull request previews an import turns on.

    Args:
        previews: The document's ``previews``.

    Returns:
        The base domain, the limits, whether bots' pull requests deploy and
        which variables a preview is given.
    """
    limits = []
    if previews.get("max_previews") is not None:
        limits.append(f"at most {previews['max_previews']}")
    if previews.get("ttl_hours") is not None:
        limits.append(f"removed after {previews['ttl_hours']} h")
    excluded = previews.get("exclude_env") or []
    variables = (
        f"every variable copied except {', '.join(excluded)}"
        if excluded
        else "every variable copied, secrets included"
    )
    bots = "bots' pull requests deploy too" if previews.get("allow_bots") else "no bots"
    shown = ", ".join([*limits, bots, variables])
    return f"previews under {previews['base_domain']}: {shown}"


#: What a step may raise and still let the import go on to the next one.
_STEP_ERRORS = (NoustError, OSError)


def apply_import(
    plan: ImportPlan,
    *,
    deploy: Callable[[CreateSpec], None],
    logger: Logger | None = None,
    cron: CronManager | None = None,
    actor: str = "cli",
) -> ImportReport:
    """
    Create the application a plan describes and apply the rest of it.

    The deploy runs first; if it fails, nothing else is attempted and its
    error is raised as it came. Each part after it is applied on its own: one
    that is refused (a domain another application has, a preview base domain
    this server cannot serve) is reported and the import goes on, so one
    part never costs the others. Zero-downtime goes last: it starts a second
    instance, which should run with everything else already in place.

    Args:
        plan: What :func:`plan_import` decided.
        deploy: Creates the application through the normal deploy path;
            raises when the deploy fails.
        logger: Where progress goes.
        cron: Cron manager; tests pass one.
        actor: Who asked, for the audit record of each cron job created
            (:func:`noust.web.auth.actor_label` in the console, ``cli`` on the
            terminal).

    Returns:
        What was applied and what was not.

    Raises:
        NoustError: The deploy failed.
    """
    require_server_role("Importing applications")
    log = logger or Logger()
    doc = plan.document
    app = doc["app"]
    target = plan.domain
    exported = plan.exported_domain
    report = ImportReport(domain=target)

    log.info(f"Deploying {target}")
    deploy(plan.create)
    report.steps.append(ImportStep(f"deploy {target}", True))
    log.info("Applying the rest of the export")

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

    added_names = False
    for kind, key in (
        (DomainKind.ALIAS.value, "aliases"),
        (DomainKind.REDIRECT.value, "redirects"),
    ):
        for name in doc["domains"].get(key, []):
            renamed = _rename(name, exported, target)

            def add(renamed: str = renamed, kind: str = kind) -> None:
                nonlocal added_names
                domain_changes.add_domain(target, renamed, kind, issue_cert=False, logger=log)
                added_names = True

            attempt(f"{kind} {renamed}", add)

    if added_names and plan.create.ssl:

        def certificate() -> str | None:
            change = domain_changes.issue_certificate(target, logger=log)
            if not change.certificate_issued:
                return (
                    f"The certificate does not cover every name yet: {change.certificate_error}. "
                    f"Point their DNS here and add each name again with 'noust domain add'."
                )
            return None

        attempt("certificate for every domain", certificate)

    created = get_store().get_app(target)
    wanted = plan.create.initial_health
    if wanted is not None:
        if created is not None and (
            created.health_path,
            created.health_expect,
            created.health_timeout,
        ) == tuple(wanted):
            # The deployment recorded it before its own gate asked it.
            log.substep("Applied health check")
            report.steps.append(ImportStep("health check", True))
        else:
            # A deployer without the initial health (monorepo, Compose).
            path, expect, timeout = wanted
            attempt(
                "health check",
                lambda: _none(set_health_check(target, path=path, expect=expect, timeout=timeout)),
            )

    keep = app.get("keep_releases")
    if (
        keep is not None
        and created is not None
        and created.layout == RELEASES
        and keep != created.keep_releases
    ):
        attempt(
            f"keep {keep} releases",
            lambda: _none(set_release_retention(target, int(keep), logger=log)),
        )

    marks = doc.get("env_secret_marks") or {}
    if marks:
        attempt(
            "secret marks", lambda: _none(get_store().set_env_secret_marks(target, dict(marks)))
        )

    jobs = cron or CronManager()
    for job in doc.get("cron", []):
        name = _rename(job["name"], exported, target)
        attempt(f"cron {name}", functools.partial(_cron_job, jobs, job, name, target, actor))

    backup = doc.get("backup")
    if backup is not None:
        store = get_store()
        destinations = [
            {
                "name": d["name"],
                "retention_count": d.get("retention_count"),
                "retention_days": d.get("retention_days"),
            }
            for d in backup.get("destinations", [])
            if store.get_backup_destination(d["name"]) is not None
        ]
        schedule = BackupSchedule(
            domain=target,
            app_name=domain_to_app_name(target),
            schedule=backup["schedule"],
            include_databases=bool(backup.get("include_databases", True)),
            retention_count=backup.get("retention_count"),
            retention_days=backup.get("retention_days"),
            destinations=destinations,
        )
        attempt(
            f"backup schedule {backup['schedule']}",
            lambda: _none(BackupScheduler().create_schedule(schedule)),
        )

    previews = doc.get("previews")
    if previews is not None:
        attempt(
            f"previews under {previews['base_domain']}",
            lambda: _none(
                enable_previews(
                    target,
                    previews["base_domain"],
                    max_previews=previews.get("max_previews"),
                    ttl_hours=previews.get("ttl_hours"),
                    allow_bots=previews.get("allow_bots"),
                    exclude_env=previews.get("exclude_env"),
                )
            ),
        )

    zero = app.get("zero_downtime") or {}
    if zero.get("enabled"):
        attempt(
            "zero-downtime",
            lambda: _none(
                set_zero_downtime(target, True, drain_seconds=zero.get("drain_seconds"), logger=log)
            ),
        )

    report.steps.extend(plan.skipped)
    return report


def _none(_result: Any) -> None:
    """Discard what a manager returned: the step only reports success."""
    return None


def _cron_job(
    jobs: CronManager, job: Mapping[str, Any], name: str, domain: str, actor: str
) -> str | None:
    """
    Create one exported cron job for the new application, audited.

    Args:
        jobs: The cron manager.
        job: The exported job.
        name: Its name here.
        domain: The application it belongs to.
        actor: Who asked for the import.

    Returns:
        Why it was not created, or None when it was.

    Raises:
        ServiceError: The cron manager refused it.
    """
    if jobs.get_job(name) is not None:
        return (
            f"A cron job named {name} already exists on this server; create this one with "
            "'noust cron create' under another name."
        )
    # The record POST /api/cron writes, plus what makes an imported job worth
    # reading twice: whom it runs as, and that a document chose it.
    audit_log.info(
        "create_cron_job name=%s schedule=%s session=%s user=%s via=import app=%s",
        name,
        job["schedule"],
        actor,
        job.get("user") or Config().service_user,
        domain,
    )
    jobs.create_job(
        CronJob(
            name=name,
            command=job["command"],
            schedule=job["schedule"],
            user=job.get("user"),
            working_directory=job.get("working_directory"),
            app_domain=domain,
        )
    )
    if not job.get("enabled", True):
        jobs.disable_job(name)
    return None


# Other platforms ------------------------------------------------------------


def proposal_document(
    proposal: Proposal,
    *,
    domain: str,
    source: str,
    branch: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """
    Turn another platform's configuration into an export document.

    So that ``noust import --deploy`` creates through :func:`plan_import` and
    :func:`apply_import` like any import. A generated secret gets a random
    value here, as the platform would have generated one; a variable the
    configuration needs but does not give stays ``null``, so the plan stops
    and names it unless ``env`` has it.

    Args:
        proposal: What the platform's configuration says.
        domain: The domain to create the application on.
        source: The repository to deploy.
        branch: The branch, or None for the default.
        env: Values given by the operator.

    Returns:
        The document.
    """
    given = dict(env or {})
    variables: dict[str, dict[str, Any]] = {}
    for variable in proposal.env:
        if variable.name in MANAGED_ENV:
            continue
        if variable.generated and variable.name not in given:
            value: str | None = secrets.token_urlsafe(32)
        else:
            value = variable.value
        if value is None and not variable.required and variable.name not in given:
            # Optional and without a default: the application does without.
            continue
        variables[variable.name] = {"secret": variable.secret, "value": value}
    health = {"path": proposal.health_path, "expect": None, "timeout": proposal.health_timeout}
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "wasm_version": __version__,
        "secrets_included": False,
        "app": {
            "domain": domain,
            "app_type": proposal.app_type or "auto",
            "source": source,
            "branch": branch,
            "layout": None,
            "port": proposal.port,
            "webserver": "nginx",
            "ssl": True,
            "include_www": False,
            "persistent_paths": list(proposal.persistent_paths),
            "keep_releases": None,
            "limits": {"memory_max_mb": None, "cpu_quota_percent": None, "tasks_max": None},
            "health": health,
            "zero_downtime": {"enabled": False, "drain_seconds": None},
        },
        "domains": {"aliases": [], "redirects": []},
        "env": variables,
        "env_secret_marks": {},
        "cron": [],
        "backup": None,
        "previews": None,
        "github": {"installation_linked": False},
        "databases": [{"engine": engine, "name": None} for engine in proposal.databases],
    }


# Summaries --------------------------------------------------------------------


def plan_summary(plan: ImportPlan) -> dict[str, Any]:
    """
    Describe a plan for JSON output, without a single variable's value.

    Args:
        plan: The plan.

    Returns:
        The domain, what is created (variables by name only), the steps, the
        parts known to be skipped, and why it needs a yes.
    """
    create = plan.create
    return {
        "domain": plan.domain,
        "exported_domain": plan.exported_domain,
        "create": {
            "app_type": create.app_type,
            "source": display_source(create.source),
            "branch": create.branch,
            "layout": create.layout,
            "port": create.port,
            "webserver": create.webserver,
            "ssl": create.ssl,
            "include_www": create.include_www,
            "persistent_paths": list(create.persistent_paths),
            "env": sorted(create.env_vars),
            "memory_max_mb": create.memory_max_mb,
            "cpu_quota_percent": create.cpu_quota_percent,
            "tasks_max": create.tasks_max,
            "health_path": create.health_path,
            "health_expect": create.health_expect,
            "health_timeout": create.health_timeout,
        },
        "steps": list(plan.steps),
        "skipped": [_step_summary(step) for step in plan.skipped],
        "confirm": list(plan.confirm),
    }


def report_summary(report: ImportReport) -> dict[str, Any]:
    """
    Describe what an import did, for JSON output and a job's result.

    Args:
        report: The report.

    Returns:
        The domain, every step, and the ones not applied.
    """
    return {
        "domain": report.domain,
        "steps": [_step_summary(step) for step in report.steps],
        "not_applied": [_step_summary(step) for step in report.not_applied],
    }


def _step_summary(step: ImportStep) -> dict[str, Any]:
    """
    Describe one step.

    Args:
        step: The step.

    Returns:
        ``part``, ``applied`` and ``detail``.
    """
    return {"part": step.part, "applied": step.applied, "detail": step.detail}
