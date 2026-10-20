# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application has been told that it is not yet running with (checklist B-09).

A variable edited, a memory limit raised, a build command changed: each is
stored the moment it is saved and reaches the process only at the next restart,
or the next build. Until then the application runs one configuration and the
console shows another. This is the difference between them, with what each
change needs to take effect and a persistent warning in the console and the
CLI, before anything is applied (Railway queues changes as New, Edited and
Removed; Coolify added a version of this in 2026).

**The baseline** is the configuration the application was last put to work
with: taken when a deployment succeeds (the recorder is the one place every
deploy, update, rollback and activation passes), and refreshed, for the areas a
restart applies, when its unit restarts (:func:`mark_restarted`, called by
``ServiceManager.restart``). **The difference** is computed against the
configuration now, section by section; each section knows what applying it takes:

- ``restart``: the process picks it up when it starts (variables, limits, the
  sandbox, mounted files, processes);
- ``rebuild``: it is an input of the build (build commands, container settings,
  a stack's compose document, the persistent paths, and a variable the framework
  bakes into the bundle);
- ``next_deploy``: it is read at the next activation, which nothing needs to be
  restarted for (the health check, the deploy hooks).

**Values are never shown, and never kept**: a variable, a hook or a build block is
compared by a salted hash, so the answer is "this changed", never "to what". The
settings that are not secret (limits, the health check, the sandbox) say what
they were and what they are.

An application with no baseline (it predates this) is given one the first time it
is asked about, as it is now: there is nothing to compare it with, and saying that
everything changed would be noise.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from noust.core import applied_config_store
from noust.core.exceptions import NoustError
from noust.core.store import App, NoustStore, get_store

logger = logging.getLogger(__name__)

#: What applying a change takes, from the lightest to the heaviest.
NEEDS_NEXT_DEPLOY = "next_deploy"
NEEDS_RESTART = "restart"
NEEDS_REBUILD = "rebuild"

#: How the heaviest need ranks.
_RANK = {NEEDS_NEXT_DEPLOY: 0, NEEDS_RESTART: 1, NEEDS_REBUILD: 2}

#: How a setting changed.
ADDED = "added"
CHANGED = "changed"
REMOVED = "removed"

#: Name prefixes a framework bakes into the client bundle at build time.
BUILD_TIME_PREFIXES: tuple[str, ...] = (
    "NEXT_PUBLIC_",
    "VITE_",
    "PUBLIC_",
    "NUXT_PUBLIC_",
    "REACT_APP_",
    "GATSBY_",
    "ASTRO_",
)

#: Application types whose process, if any, reads no variable the build did not see.
BUILD_ONLY_TYPES = frozenset({"static", "vite"})


@dataclass(frozen=True)
class PendingItem:
    """
    One setting that differs from what the application runs with.

    Attributes:
        area: The part of the configuration (``environment``, ``limits``...).
        key: The setting inside it: a variable's name, a limit's field. Empty
            for an area compared as a whole (the hooks, the build block).
        change: ``added``, ``changed`` or ``removed``.
        needs: What applying it takes: ``restart``, ``rebuild`` or ``next_deploy``.
        before: What it was, for a setting that is not secret.
        after: What it is now, for a setting that is not secret.
    """

    area: str
    key: str
    change: str
    needs: str
    before: str | None = None
    after: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The item as JSON-ready data.
        """
        return {
            "area": self.area,
            "key": self.key,
            "change": self.change,
            "needs": self.needs,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class PendingChanges:
    """
    The difference between what an application runs with and what it is set to.

    Attributes:
        domain: The application.
        items: Every difference, by area.
        needs: The heaviest thing applying them takes, or None when there are none.
        since: When the configuration it runs with was taken.
        deployment_id: The deployment that took it, when one did.
        baseline_created: The application had no baseline, so this is its first,
            taken just now: there is nothing to compare yet.
    """

    domain: str
    items: tuple[PendingItem, ...] = ()
    needs: str | None = None
    since: str | None = None
    deployment_id: int | None = None
    baseline_created: bool = False

    @property
    def pending(self) -> bool:
        """Whether anything differs."""
        return bool(self.items)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The changes as JSON-ready data.
        """
        return {
            "domain": self.domain,
            "pending": self.pending,
            "needs": self.needs,
            "since": self.since,
            "deployment_id": self.deployment_id,
            "baseline_created": self.baseline_created,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class Section:
    """
    One area of configuration and how it is compared.

    Attributes:
        name: The area's name in the document and in an item.
        needs: What applying a change to it takes.
        collect: Reads it from the application as it is set now: setting name to
            value. A hashed section's values are hashed here, so they never reach
            the document.
        hashed: Whether the values are secret and so reported without them.
        on_restart: Whether a restart applies it, refreshing the baseline.
        needs_for: Overrides ``needs`` for one setting (a variable that is baked
            into the bundle needs a rebuild).
    """

    name: str
    needs: str
    collect: Callable[[App], dict[str, Any]]
    hashed: bool = False
    on_restart: bool = False
    needs_for: Callable[[App, str], str] | None = None


def fingerprint(domain: str, area: str, key: str, value: object) -> str:
    """
    Hash a setting so it can be compared and not read.

    Args:
        domain: The application, as the salt.
        area: The area.
        key: The setting.
        value: Its value.

    Returns:
        A short hash.
    """
    text = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(f"{domain}\0{area}\0{key}\0{text}".encode()).hexdigest()[:20]


def _hashed(domain: str, area: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """
    Hash every value of an area.

    Args:
        domain: The application.
        area: The area.
        values: Setting to value.

    Returns:
        Setting to hash.
    """
    return {key: fingerprint(domain, area, key, value) for key, value in values.items()}


def _environment(app: App) -> dict[str, Any]:
    from noust.deployers.helpers.app_env import read_app_env

    try:
        values = read_app_env(app)
    except (OSError, NoustError) as exc:
        logger.debug("Could not read the environment of %s: %s", app.domain, exc)
        return {}
    return _hashed(app.domain, "environment", values)


def _environment_needs(app: App, key: str) -> str:
    if (app.app_type or "") in BUILD_ONLY_TYPES or key.startswith(BUILD_TIME_PREFIXES):
        return NEEDS_REBUILD
    return NEEDS_RESTART


def _limits(app: App) -> dict[str, Any]:
    return {
        "memory_max_mb": app.memory_max_mb,
        "cpu_quota_percent": app.cpu_quota_percent,
        "tasks_max": app.tasks_max,
    }


def _health(app: App) -> dict[str, Any]:
    from noust.core.app_health_store import get_extras

    return {
        "path": app.health_path,
        "expect": app.health_expect,
        "timeout": app.health_timeout,
        **get_extras(app.domain).to_dict(),
    }


def _hooks(app: App) -> dict[str, Any]:
    document = get_store().get_app_hooks(app.domain)
    return _hashed(app.domain, "hooks", {"document": document}) if document else {}


def _build(app: App) -> dict[str, Any]:
    from noust.core import app_build_store

    document = app_build_store.get_document(app.domain)
    return _hashed(app.domain, "build", {"document": document}) if document else {}


def _compose_document(app: App) -> dict[str, Any]:
    from noust.deployers.compose_document import document_text

    text = document_text(app)
    return _hashed(app.domain, "compose", {"document": text}) if text else {}


def _container(app: App) -> dict[str, Any]:
    if app.app_type != "container":
        return {}
    from dataclasses import asdict

    from noust.core.container_store import ContainerStore

    settings = ContainerStore().get(app.domain)
    if settings is None:
        return {}
    values = asdict(settings)
    # The digest is what the last deployment resolved, not a setting.
    values.pop("digest", None)
    return _hashed(app.domain, "container", values)


def _sandbox(app: App) -> dict[str, Any]:
    from noust.core.unit_hardening_store import get_record

    record = get_record(app.domain)
    if record is None:
        return {}
    return {"profile": record.profile, "writable_paths": ",".join(record.writable_paths)}


def _tuning(app: App) -> dict[str, Any]:
    from noust.core.unit_tuning_store import get_record

    record = get_record(app.domain)
    if record is None:
        return {}
    return {
        name: ",".join(
            entry["path"] + ":" + entry["mode"] if isinstance(entry, dict) else str(entry)
            for entry in value
        )
        if isinstance(value, list)
        else value
        for name, value in record.options.items()
    }


def _mounts(app: App) -> dict[str, Any]:
    from noust.core import app_mount_store
    from noust.managers.app_mounts import MountManager, unsupported_reason

    if unsupported_reason(app) is not None:
        return {}
    manager = MountManager()
    found: dict[str, Any] = {}
    for record in app_mount_store.list_mounts(app.domain):
        if record.kind == "file":
            path = manager.file_path(app, record.target)
            # The file as it is on disk: an edit made by hand shows up too.
            try:
                found[record.target] = fingerprint(
                    app.domain, "mounts", record.target, path.read_bytes().hex()
                )
            except OSError:
                found[record.target] = "missing"
        else:
            found[record.target] = fingerprint(
                app.domain, "mounts", record.target, [record.source, record.read_only]
            )
    return found


def _persistent_paths(app: App) -> dict[str, Any]:
    return dict.fromkeys(app.persistent_paths, True)


#: Every area, in the order the console lists them.
SECTIONS: tuple[Section, ...] = (
    Section(
        "environment",
        NEEDS_RESTART,
        _environment,
        hashed=True,
        on_restart=True,
        needs_for=_environment_needs,
    ),
    Section("limits", NEEDS_RESTART, _limits, on_restart=True),
    Section("mounts", NEEDS_RESTART, _mounts, hashed=True, on_restart=True),
    Section("sandbox", NEEDS_RESTART, _sandbox, on_restart=True),
    Section("tuning", NEEDS_RESTART, _tuning, on_restart=True),
    Section("build", NEEDS_REBUILD, _build, hashed=True),
    Section("container", NEEDS_REBUILD, _container, hashed=True),
    Section("compose", NEEDS_REBUILD, _compose_document, hashed=True),
    Section("persistent_paths", NEEDS_REBUILD, _persistent_paths),
    Section("health", NEEDS_NEXT_DEPLOY, _health),
    Section("hooks", NEEDS_NEXT_DEPLOY, _hooks, hashed=True),
)

#: Sections other areas of this module's lane add (processes, mounted files), each
#: registered by the module that owns them so this one need not import them.
_EXTRA: list[Section] = []


def register_section(section: Section) -> None:
    """
    Add an area of configuration to the comparison.

    Args:
        section: The area. Registering a name twice replaces the first.
    """
    for index, known in enumerate(_EXTRA):
        if known.name == section.name:
            _EXTRA[index] = section
            return
    _EXTRA.append(section)


def sections() -> tuple[Section, ...]:
    """
    Returns:
        Every area compared, the built-in ones and those registered.
    """
    return (*SECTIONS, *_EXTRA)


def snapshot(app: App, *, only: set[str] | None = None) -> dict[str, Any]:
    """
    Read the configuration an application is set to now.

    Args:
        app: The application.
        only: Read just these areas; all of them when None.

    Returns:
        Area name to what it holds.
    """
    taken: dict[str, Any] = {}
    for section in sections():
        if only is not None and section.name not in only:
            continue
        try:
            taken[section.name] = section.collect(app)
        except (NoustError, OSError, ValueError) as exc:
            # An area that cannot be read is left out, so it is not compared
            # to something it is not: the rest of the answer stands.
            logger.debug("Could not read the %s of %s: %s", section.name, app.domain, exc)
    return taken


def record_applied(
    domain: str, deployment_id: int | None, *, store: NoustStore | None = None
) -> bool:
    """
    Take the configuration a deployment just put to work.

    Args:
        domain: The application.
        deployment_id: The deployment that succeeded.
        store: The store.

    Returns:
        True when the application exists.
    """
    target = store or get_store()
    app = target.get_app(domain)
    if app is None:
        return False
    return applied_config_store.put(
        domain, snapshot(app), deployment_id=deployment_id, store=target
    )


def mark_restarted(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Take, for the areas a restart applies, the configuration the application restarted with.

    The health check and the hooks, which a restart does not apply, and the build
    inputs, which it does not either, keep the baseline they had.

    Args:
        domain: The application.
        store: The store.

    Returns:
        True when there was a baseline to refresh.
    """
    target = store or get_store()
    app = target.get_app(domain)
    known = applied_config_store.get(domain, store=target)
    if app is None or known is None:
        return False
    names = {section.name for section in sections() if section.on_restart}
    merged = {**known.sections, **snapshot(app, only=names)}
    return applied_config_store.put(
        domain, merged, deployment_id=None, keep_deployment=True, store=target
    )


def _compare(
    app: App, section: Section, before: Mapping[str, Any], now: Mapping[str, Any]
) -> list[PendingItem]:
    """
    Compare one area with what the application ran with.

    Args:
        app: The application.
        section: The area.
        before: What it held.
        now: What it holds.

    Returns:
        The settings that differ.
    """
    items: list[PendingItem] = []
    for key in sorted(set(before) | set(now)):
        old, new = before.get(key), now.get(key)
        if old == new:
            continue
        # A setting that is None is not set: absent and None are the same thing.
        if old in (None, "") and new in (None, ""):
            continue
        if key not in before or old in (None, ""):
            change = ADDED
        elif key not in now or new in (None, ""):
            change = REMOVED
        else:
            change = CHANGED
        needs = section.needs_for(app, key) if section.needs_for else section.needs
        shown = not section.hashed
        items.append(
            PendingItem(
                area=section.name,
                key="" if key == "document" else key,
                change=change,
                needs=needs,
                before=None if not shown or old in (None, "") else str(old),
                after=None if not shown or new in (None, "") else str(new),
            )
        )
    return items


def pending(domain: str, *, store: NoustStore | None = None) -> PendingChanges:
    """
    Say what an application is set to that it is not running with.

    Args:
        domain: The application.
        store: The store.

    Returns:
        The differences. For an application with no baseline, none: it is given
        one, as it is now.

    Raises:
        NoustError: The application is unknown.
    """
    target = store or get_store()
    app = target.get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    known = applied_config_store.get(domain, store=target)
    if known is None:
        record_applied(domain, None, store=target)
        return PendingChanges(domain=domain, baseline_created=True)
    items: list[PendingItem] = []
    for section in sections():
        before = known.sections.get(section.name)
        if before is None:
            # A baseline taken before this area existed: nothing to compare.
            continue
        try:
            now = section.collect(app)
        except (NoustError, OSError, ValueError) as exc:
            logger.debug("Could not read the %s of %s: %s", section.name, domain, exc)
            continue
        items.extend(_compare(app, section, before, now))
    needs = max((item.needs for item in items), key=_RANK.__getitem__, default=None)
    return PendingChanges(
        domain=domain,
        items=tuple(items),
        needs=needs,
        since=known.taken_at,
        deployment_id=known.deployment_id,
    )


def summary(changes: PendingChanges) -> dict[str, int]:
    """
    Count the differences by what applying them takes.

    Args:
        changes: What :func:`pending` returned.

    Returns:
        Need to count, for the needs that occur.
    """
    counts: dict[str, int] = {}
    for item in changes.items:
        counts[item.needs] = counts.get(item.needs, 0) + 1
    return counts
