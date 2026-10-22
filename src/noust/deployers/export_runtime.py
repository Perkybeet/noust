# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application runs with besides its code, in an export document (checklist B-80).

Two parts of :mod:`noust.deployers.app_export` live here so that the module of the
document stays about the document:

- **The mounts** (:mod:`noust.managers.app_mounts`): the configuration files Noust keeps
  for an application and the paths of the server bound into a container. A file's text
  may hold a token, so it travels exactly like a secret variable: only with
  ``with_secrets``, ``null`` and ``"secret": true`` otherwise. A path is a name, and the
  server it names has to have it.
- **The container settings** (:mod:`noust.core.container_store`): the image or the
  Dockerfile, the command, the port, the volumes. The registry password and the values of
  the build secrets are Noust's own credentials and never go in; the account and the ids
  are named so the import can say what to give back.

Everything here is a client of the owners of those things: the files are read and written
by :class:`~noust.managers.app_mounts.MountManager`, which refuses a link, a bad target or
a source that is the server's own state, and the settings are checked by
:class:`~noust.core.container_store.ContainerSettings`. A document is untrusted input, so
it is checked here and again by the manager that applies it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from noust.core import app_mount_store
from noust.core.app_mount_store import DEFAULT_MODE, KIND_FILE, KIND_PATH, MountRecord
from noust.core.container_store import ContainerSettings, ContainerStore
from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import App, get_store
from noust.core.utils import domain_to_app_name
from noust.deployers.export_checks import (
    check_flag,
    check_names,
    check_number,
    check_object,
    check_text,
    fail,
)
from noust.deployers.helpers.container_settings import SETTINGS_FIELDS
from noust.managers.app_mounts import (
    MAX_FILE_BYTES,
    MountManager,
    clean_source,
    clean_target,
    unsupported_reason,
)

#: The most mounts a document names: far more than an application has, and a bound so a
#: document cannot ask for thousands of files.
MAX_MOUNTS = 64

#: The text of mounted files one document carries, in bytes once written as JSON. A
#: document is read whole and is bounded (``app_export.MAX_DOCUMENT_SIZE``); a file that does
#: not fit is named and left to be copied by hand, never cut.
CONTENT_BUDGET = 600 * 1024

#: What a mode of a document looks like: octal digits, as ``ls`` and ``chmod`` write it.
_MODE = re.compile(r"[0-7]{3,4}")

#: The types whose first deploy cannot be given files beforehand: a container is bound to
#: its mounts when its compose file is written, and a monorepo has no shared directory to
#: seed. Every other type writes the files before its first start (``seed_files``).
_NOT_SEEDED = frozenset({"container", "monorepo"})

#: The settings of a container that go into a document and come out of it: the ones a
#: change may name, minus the registry account, which is told apart (its password is not here).
_REGISTRY = ("registry_server", "registry_username")
_CONTAINER_FIELDS = tuple(sorted(SETTINGS_FIELDS))


# Export ---------------------------------------------------------------------


def export_mounts(
    app: App, *, with_secrets: bool, actor: str = "export", manager: MountManager | None = None
) -> list[dict[str, Any]]:
    """
    Describe an application's mounts for the document.

    Args:
        app: The application.
        with_secrets: Put the text of the files in. Without it each is ``null`` with
            ``"secret": true``, because a mounted file is where a token lives.
        actor: Who asked, for the audit record of reading a file.
        manager: The mount manager; tests pass one.

    Returns:
        One entry per mount, by target. A file that cannot travel (not on this server, not
        text, too large for the document) has ``content`` null and a ``note`` that says why.
    """
    if unsupported_reason(app) is not None:
        return []
    mounts = manager or MountManager()
    budget = CONTENT_BUDGET
    entries: list[dict[str, Any]] = []
    for record in app_mount_store.list_mounts(app.domain):
        if record.kind == KIND_PATH:
            entries.append(
                {
                    "kind": KIND_PATH,
                    "target": record.target,
                    "source": record.source,
                    "read_only": record.read_only,
                }
            )
            continue
        entry: dict[str, Any] = {
            "kind": KIND_FILE,
            "target": record.target,
            "mode": f"{record.mode:o}",
            "secret": True,
            "content": None,
        }
        if with_secrets:
            content, note = _read_file(mounts, app, record, budget, actor)
            entry["content"] = content
            if content is not None:
                budget -= len(json.dumps(content))
            if note is not None:
                entry["note"] = note
        entries.append(entry)
    return entries


def _read_file(
    mounts: MountManager, app: App, record: MountRecord, budget: int, actor: str
) -> tuple[str | None, str | None]:
    """
    Read the text of a file mount for the document.

    Args:
        mounts: The mount manager.
        app: The application.
        record: The mount.
        budget: How many bytes of text the document still has room for.
        actor: Who asked, for the audit record.

    Returns:
        The text and None, or None and why it does not go in.
    """
    try:
        text = mounts.read_file(app.domain, record.target, actor=actor)
    except (ValidationError, OSError) as exc:
        reason = exc.message if isinstance(exc, ValidationError) else str(exc)
        return None, f"Its text could not be read here: {reason}"
    size = len(json.dumps(text))
    if size > min(budget, MAX_FILE_BYTES):
        return None, (
            f"Its {len(text.encode('utf-8'))} bytes do not fit in the document. Copy it with "
            f"'noust app mount file {app.domain} {record.target} --from FILE'."
        )
    return text, None


def export_container(app: App) -> dict[str, Any] | None:
    """
    Describe how a container application is built and run, for the document.

    Args:
        app: The application.

    Returns:
        The settings that can travel, and the ids of the build secrets (their values cannot);
        None for an application that is not a container.
    """
    if app.app_type != "container":
        return None
    settings = ContainerStore(get_store()).get(app.domain)
    if settings is None:
        return None
    public = _portable(settings, registry=True)
    public["build_secrets"] = list(settings.build_secrets)
    return public


def _portable(settings: ContainerSettings, *, registry: bool) -> dict[str, Any]:
    """
    Read the settings that can travel in a document.

    Args:
        settings: The container's settings.
        registry: Whether the registry account's server and username go too (a document
            names them; creating the application does not take them, as it has no password).

    Returns:
        Setting name to JSON-ready value.
    """
    portable: dict[str, Any] = {}
    for name in _CONTAINER_FIELDS:
        if name in _REGISTRY and not registry:
            continue
        value = getattr(settings, name)
        portable[name] = list(value) if isinstance(value, tuple) else value
    portable["build_args"] = dict(sorted(settings.build_args.items()))
    return portable


# Validation -----------------------------------------------------------------


def check_mounts(data: Any) -> list[dict[str, Any]]:
    """
    Check the mounts of a document and fill in what an entry leaves out.

    Args:
        data: The ``mounts`` field, or None for a document from before they were exported.

    Returns:
        A copy, each entry complete: a file has ``mode`` and ``content``, a path has
        ``read_only``.

    Raises:
        ValidationError: It is not a list of mounts; names the field.
    """
    if data is None:
        return []
    if not isinstance(data, list):
        raise fail("mounts", "must be a list")
    if len(data) > MAX_MOUNTS:
        raise fail("mounts", f"has more than {MAX_MOUNTS} entries")
    seen: set[str] = set()
    checked: list[dict[str, Any]] = []
    for index, raw in enumerate(data):
        where = f"mounts[{index}]"
        entry = dict(check_object(raw, where))
        kind = check_text(entry, "kind", where, required=True)
        if kind not in (KIND_FILE, KIND_PATH):
            raise fail(f"{where}.kind", f"must be {KIND_FILE} or {KIND_PATH}")
        target = check_text(entry, "target", where, required=True)
        if target in seen:
            raise fail(f"{where}.target", "is mounted twice")
        seen.add(str(target))
        if kind == KIND_FILE:
            mode = check_text(entry, "mode", where)
            if mode is None:
                entry["mode"] = f"{DEFAULT_MODE:o}"
            elif _MODE.fullmatch(mode) is None or not 0o400 <= int(mode, 8) <= 0o777:
                raise fail(f"{where}.mode", "must be an octal mode between 400 and 777")
            content = check_text(entry, "content", where)
            if content is not None and "\x00" in content:
                raise fail(f"{where}.content", "must be text")
            entry["content"] = content
            check_text(entry, "note", where)
        else:
            check_text(entry, "source", where, required=True)
            entry["read_only"] = check_flag(entry, "read_only", where, default=True)
        checked.append(entry)
    return checked


def check_container(data: Any) -> dict[str, Any] | None:
    """
    Check the container settings of a document.

    Args:
        data: The ``container`` field, or None.

    Returns:
        The settings, only the fields a container has; None for none.

    Raises:
        ValidationError: A value is not one a container can have; names the field.
    """
    if data is None:
        return None
    given = check_object(data, "container")
    kept = {name: given[name] for name in (*_CONTAINER_FIELDS, "build_secrets") if name in given}
    for name in ("volumes", "build_secrets"):
        if name in kept:
            check_names(kept, name, "container")
    for name in ("entrypoint", "command"):
        if kept.get(name) is not None:
            check_names(kept, name, "container")
    check_number(kept, "container_port", "container")
    try:
        _settings_of(kept)
    except ValidationError as exc:
        exc.field = f"container.{exc.field}" if exc.field else "container"
        raise
    return kept


def _settings_of(container: Mapping[str, Any]) -> ContainerSettings:
    """
    Build and check the settings a document describes, without the registry account.

    Args:
        container: The ``container`` field, already shaped.

    Returns:
        The validated settings.

    Raises:
        ValidationError: A value is not valid.
    """
    values = {
        name: container[name]
        for name in _CONTAINER_FIELDS
        if name in container and name not in _REGISTRY
    }
    return ContainerSettings.from_mapping(values).checked()


# Plan -----------------------------------------------------------------------


@dataclass
class RuntimePlan:
    """
    What importing the mounts and the container of a document does.

    Attributes:
        steps: One line per thing applied after the deploy.
        skipped: ``(part, why)`` for what cannot be applied here, known before starting.
        confirm: Why the operator must say yes: what the document asks of this server.
        seed_files: Text of the files to put in place before the first start, by target.
        seed_modes: Their modes.
        container: The settings the application is created with, or None.
        mounts: The entries this server can make, to apply after the deploy.
    """

    steps: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    confirm: list[str] = field(default_factory=list)
    seed_files: dict[str, str] = field(default_factory=dict)
    seed_modes: dict[str, int] = field(default_factory=dict)
    container: dict[str, Any] | None = None
    mounts: list[dict[str, Any]] = field(default_factory=list)


def reaches_first_start(app_type: str) -> bool:
    """
    Tell whether the files of an import are in place when the application first starts.

    Args:
        app_type: The application's type.

    Returns:
        False for the types whose mounts can only be made once the application exists.
    """
    return app_type not in _NOT_SEEDED


def late_mounts_note(app_type: str, domain: str) -> str:
    """
    Say what to do so an application uses the mounts made after it was created.

    Args:
        app_type: The application's type.
        domain: The application.

    Returns:
        The sentence, with the command.
    """
    if app_type == "container":
        return (
            f"{domain} was created before its mounts. A container is bound to them when it is "
            f"created: run 'noust update {domain}'."
        )
    return (
        f"{domain} was created before its mounts. It reads them when it restarts: run "
        f"'noust service restart {domain_to_app_name(domain)}'."
    )


def plan_runtime(doc: Mapping[str, Any], domain: str) -> RuntimePlan:
    """
    Decide what an import does with a document's mounts and container settings.

    Args:
        doc: The checked document.
        domain: The domain the application is created on.

    Returns:
        The plan. Nothing here changes anything.
    """
    app = doc["app"]
    app_type = str(app["app_type"])
    stand_in = App(
        domain=domain,
        app_type=app_type,
        app_path="",
        layout=app.get("layout") or "",
        is_static=app_type == "static",
    )
    plan = RuntimePlan()
    _plan_container(doc.get("container"), domain, plan)
    reason = unsupported_reason(stand_in)
    binds = 0
    for mount in doc.get("mounts") or []:
        kind, target = mount["kind"], mount["target"]
        part = f"{kind} mount {target}"
        try:
            if reason is not None:
                raise ValidationError(f"{app_type} applications take no mounts: {reason}")
            if kind == KIND_PATH and app_type != "container":
                raise ValidationError(
                    f"{app_type} applications are not given paths of the server",
                    details="Only a container application is; the others already run on it.",
                )
            cleaned = clean_target(stand_in, target, kind)
            if kind == KIND_PATH:
                _plan_bind(mount, cleaned, plan)
                binds += 1
            else:
                _plan_file(domain, app_type, mount, cleaned, plan)
            plan.mounts.append({**mount, "target": cleaned})
        except NoustError as exc:
            plan.skipped.append((part, exc.message + (f". {exc.details}" if exc.details else "")))
    if binds:
        plan.confirm.append(
            f"binds {binds} path(s) of this server into the container, which the document chose: "
            "a container that writes there changes the server"
        )
    return plan


def _plan_container(container: Mapping[str, Any] | None, domain: str, plan: RuntimePlan) -> None:
    """
    Plan the container settings of a document.

    Args:
        container: The ``container`` field.
        domain: The domain the application is created on.
        plan: The plan, which gets the settings, the steps and what is left to give back.
    """
    if container is None:
        return
    settings = _settings_of(container)
    plan.container = _portable(settings, registry=False)
    built_from = settings.image or settings.dockerfile or "the repository's Dockerfile"
    plan.steps.append(
        f"container settings: {settings.method} {built_from}, port {settings.effective_port}"
    )
    user, server = container.get("registry_username"), container.get("registry_server")
    if user:
        plan.skipped.append(
            (
                f"registry account {user}" + (f" at {server}" if server else ""),
                "Its password is a credential and is not exported. Give it back with "
                f"'noust app container set {domain} --registry-user {user} "
                "--registry-password-stdin'.",
            )
        )
    secrets = container.get("build_secrets") or []
    if secrets:
        plan.skipped.append(
            (
                f"build secrets {', '.join(secrets)}",
                "Their values are credentials and are not exported. Give them back with "
                f"'noust app container set {domain} --build-secret ID=FILE'.",
            )
        )


def _plan_file(
    domain: str, app_type: str, mount: Mapping[str, Any], cleaned: str, plan: RuntimePlan
) -> None:
    """
    Plan one file mount.

    Args:
        domain: The application's domain.
        app_type: Its type, which decides whether the file is there at the first start.
        mount: The checked entry.
        cleaned: Its target, as the application sees it.
        plan: The plan.

    Raises:
        ValidationError: The text is not in the document.
    """
    mode = int(mount["mode"], 8)
    content = mount.get("content")
    if content is None:
        raise ValidationError(
            f"The text of {cleaned} is not in the export",
            details=mount.get("note")
            or "A mounted file may hold a secret, so it is exported only with --with-secrets. "
            f"Give it after the import: noust app mount file {domain} {cleaned} --from FILE "
            f"--mode {mount['mode']}.",
        )
    size = len(content.encode("utf-8"))
    seeded = reaches_first_start(app_type)
    plan.steps.append(
        f"file mount {cleaned} ({size} bytes, mode {mount['mode']}"
        + ("" if seeded else ", after the first start")
        + ")"
    )
    if seeded:
        plan.seed_files[cleaned] = content
        plan.seed_modes[cleaned] = mode


def _plan_bind(mount: Mapping[str, Any], cleaned: str, plan: RuntimePlan) -> None:
    """
    Plan one path of the server bound into a container.

    Args:
        mount: The checked entry.
        cleaned: Its target in the container.
        plan: The plan.

    Raises:
        ValidationError: This server cannot bind the path (it is not there, or it is the
            server's own state).
    """
    source = clean_source(mount["source"])
    plan.steps.append(
        f"bind {source} at {cleaned} " + ("read-only" if mount["read_only"] else "writable")
    )


# Apply ----------------------------------------------------------------------


def apply_mount(
    manager: MountManager, domain: str, mount: Mapping[str, Any], *, actor: str
) -> str | None:
    """
    Make one mount of a document on the application just created.

    Args:
        manager: The mount manager, which refuses what it must.
        domain: The application.
        mount: The checked entry.
        actor: Who asked, for the audit trail.

    Returns:
        Why it was not made (a file whose text is not in the document), or None.

    Raises:
        NoustError: The manager refused it (a target it does not take, a source that is not
            there, a link in the way).
    """
    if mount["kind"] == KIND_PATH:
        manager.put_path(
            domain,
            mount["source"],
            mount["target"],
            read_only=bool(mount["read_only"]),
            actor=actor,
        )
        return None
    if mount.get("content") is None:
        return mount.get("note") or "The text of the file is not in the export."
    manager.put_file(
        domain, mount["target"], mount["content"], mode=int(mount["mode"], 8), actor=actor
    )
    return None
