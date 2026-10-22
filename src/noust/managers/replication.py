# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether an application could run on more than one server, and what stops it (checklist B-24, B-69).

Running one application on several servers is the central's: a placement records where it runs, a
rollout puts one commit on each, a copy brings it to a server that lacks it (``noust fleet
placement``, ``docs/CENTRAL.md``). None of that looks inside the application, and it cannot: the
central holds no copy of what a server runs. What decides whether a second server would serve the
same thing is on the server the application is on - the files it keeps, the database it uses, the
tasks it schedules - and that is what this reads: the records, the environment file, the compose
file and the list of scheduled tasks. It changes nothing.

It answers only "can it": the findings it gives are what a copy would leave behind or do twice.
It places nothing, copies nothing and touches no server, which is the central's, and the verdict
is advice: the operator may spread an application whose findings are warnings, and a finding that
``blocks`` is one a second server would get wrong (an uploads folder that is empty there, a database
that does not exist there).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from noust.core.exceptions import SourceError
from noust.core.store import AppType, NoustStore, get_store, is_adopted

Severity = Literal["blocks", "warns"]

#: Hosts that are the machine the application is on.
_LOOPBACK = re.compile(r"(?:^|[/@=:\s])(?:127\.\d+\.\d+\.\d+|localhost|\[?::1\]?)(?::\d+|/|$)")

#: The hosts a database record may have that mean this machine; any other is a server of its own.
_THIS_MACHINE = frozenset({"", "localhost", "127.0.0.1", "::1"})

#: Environment variables whose value is a connection to a service, and so can name this machine.
_SERVICE_VARIABLE = re.compile(
    r"(?:_URL|_URI|_HOST|_DSN|_ADDR|_ADDRESS)$|^(?:DATABASE|REDIS|MONGO)", re.I
)


@dataclass(frozen=True)
class Finding:
    """
    One thing a second server would get wrong or do twice.

    Attributes:
        code: A stable name for it (``persistent_paths``, ``local_database``...).
        severity: ``blocks`` (a copy would be wrong) or ``warns`` (it would work, with a cost).
        message: What it is, in a sentence.
        fix: What the operator does about it.
        items: The names it is about (paths, databases, variables, tasks); never a value.
    """

    code: str
    severity: Severity
    message: str
    fix: str
    items: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The finding as JSON-ready values.
        """
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "fix": self.fix,
            "items": list(self.items),
        }


@dataclass(frozen=True)
class Readiness:
    """
    Whether an application could run on several servers.

    Attributes:
        domain: The application.
        ready: True when nothing blocks it. Warnings do not.
        findings: What a second server would get wrong or do twice, blocking ones first.
        by_image: Whether it runs a registry image, which a rollout can then name by tag so no
            server builds it.
    """

    domain: str
    ready: bool
    findings: tuple[Finding, ...]
    by_image: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The readiness as JSON-ready values.
        """
        return {
            "domain": self.domain,
            "ready": self.ready,
            "findings": [finding.to_dict() for finding in self.findings],
            "by_image": self.by_image,
        }


def _compose_findings(document: Mapping[str, Any] | None) -> list[Finding]:
    """
    Read a compose file for the state its stack keeps on the server.

    Args:
        document: The parsed compose file, or None when it could not be read.

    Returns:
        The findings: a database run by the stack and named volumes.
    """
    from noust.managers.stack_databases import image_engine

    if not isinstance(document, Mapping):
        return []
    services = document.get("services")
    services = services if isinstance(services, Mapping) else {}
    found: list[Finding] = []
    databases = sorted(
        str(name)
        for name, service in services.items()
        if isinstance(service, Mapping) and image_engine(service.get("image")) is not None
    )
    if databases:
        found.append(
            Finding(
                "stack_database",
                "blocks",
                "The stack runs its own database, whose data would not be on a second server.",
                "Point every server at one database server of yours and take the service out of "
                "the stack.",
                tuple(databases),
            )
        )
    volumes = document.get("volumes")
    named = sorted(str(key) for key in volumes) if isinstance(volumes, Mapping) else []
    if named:
        found.append(
            Finding(
                "named_volumes",
                "warns",
                "The stack keeps data in Docker volumes, which belong to the server they were "
                "created on.",
                "If that data is a cache, nothing is lost; if it is the application's, keep it in "
                "a service all the servers share.",
                tuple(named),
            )
        )
    return found


def assess(
    app: Any,
    *,
    store: NoustStore | None = None,
    compose_document: Callable[[], Mapping[str, Any] | None] | None = None,
    cron_jobs: Callable[[], list[dict[str, Any]]] | None = None,
) -> Readiness:
    """
    Say whether an application could run on more than one server.

    Reads records, the environment file and, for a Compose stack, its compose file, and lists the
    scheduled tasks; changes nothing.

    Args:
        app: The application's row.
        store: The store the records are read from; the process-wide one when None.
        compose_document: Returns the parsed compose file of a stack; read from the stack's
            directory when None.
        cron_jobs: Returns the scheduled tasks of the server, as :meth:`CronManager.list_jobs`
            does; read from the server when None.

    Returns:
        The verdict, with every finding. An application nothing in the records speaks against is
        ready.
    """
    from noust.core.store import is_compose_backed
    from noust.validators.source import validate_source

    store = store if store is not None else get_store()
    findings: list[Finding] = []
    by_image = app.app_type == AppType.CONTAINER.value

    if is_adopted(app.app_type):
        findings.append(
            Finding(
                "adopted",
                "blocks",
                "It is a site Noust adopted, not one it built: a copy cannot be made from its record.",
                "Adopt it on the other server too, or create it from its source with noust create.",
            )
        )
    elif not by_image:
        try:
            kind = validate_source(app.source)[0] if app.source else "local"
        except SourceError:
            # A source that is not one is an answer to the question, not a failure of it.
            kind = "local"
        if kind == "local":
            findings.append(
                Finding(
                    "no_repository",
                    "blocks",
                    "Its source is a folder on this server, so there is nothing for a second "
                    "server to build from.",
                    "Put it in a git repository and deploy from that, or run a registry image.",
                )
            )

    paths = tuple(app.persistent_paths or ())
    if paths:
        findings.append(
            Finding(
                "persistent_paths",
                "blocks",
                "It keeps files on this server (the folders it writes to between releases); a "
                "second server would start with them empty.",
                "Keep them in object storage or a mount all the servers share.",
                paths,
            )
        )

    databases = sorted(
        f"{database.engine}:{database.name}"
        for database in store.list_databases(app_id=app.id)
        if database.host.strip().lower() in _THIS_MACHINE
    )
    if databases:
        findings.append(
            Finding(
                "local_database",
                "blocks",
                "It uses a database that Noust runs on this server, which a second server would not have.",
                "Move the database to a server of its own and point DATABASE_URL at it on every server.",
                tuple(databases),
            )
        )

    if is_compose_backed(app.app_type):
        document = _read_document(app, compose_document)
        findings.extend(_compose_findings(document))

    if app.layout == "inplace" and not is_adopted(app.app_type):
        findings.append(
            Finding(
                "in_place",
                "warns",
                "It is built in place, so a rollout that stops cannot put a server back by "
                "activating an earlier release.",
                "Move it onto releases: noust app migrate.",
            )
        )

    local = _local_services(app)
    if local:
        findings.append(
            Finding(
                "local_service",
                "warns",
                "Some of its variables point at a service on this machine; on another server they "
                "would point at that server's own, or at nothing.",
                "Point them at a host both servers reach.",
                local,
            )
        )

    tasks = _scheduled_tasks(app.domain, cron_jobs)
    if tasks:
        findings.append(
            Finding(
                "scheduled_tasks",
                "warns",
                "It has scheduled tasks, which would then run once on every server.",
                "Keep them on one server, or make them safe to run twice.",
                tasks,
            )
        )

    if store.get_preview_settings(app.domain) is not None:
        findings.append(
            Finding(
                "previews",
                "warns",
                "Its previews are made on this server and are not copied.",
                "Turn them off on the servers that should only serve.",
            )
        )

    findings.sort(key=lambda finding: finding.severity != "blocks")
    return Readiness(
        domain=app.domain,
        ready=not any(finding.severity == "blocks" for finding in findings),
        findings=tuple(findings),
        by_image=by_image,
    )


def _read_document(
    app: Any, compose_document: Callable[[], Mapping[str, Any] | None] | None
) -> Mapping[str, Any] | None:
    """
    Read the compose file of a stack.

    Args:
        app: The application's row.
        compose_document: A seam that returns it.

    Returns:
        The parsed file, or None when it cannot be read.
    """
    if compose_document is not None:
        return compose_document()
    from noust.core.exceptions import NoustError
    from noust.deployers.docker_compose import stack_deployer

    try:
        document = stack_deployer(app)._load_compose_document()
    except (NoustError, OSError):
        return None
    return document if isinstance(document, Mapping) else None


def _local_services(app: Any) -> tuple[str, ...]:
    """
    Name the variables of an application whose value points at this machine.

    Args:
        app: The application's row.

    Returns:
        Their names, sorted. A value is never returned or logged.
    """
    from noust.deployers.helpers.app_env import read_environment_of

    try:
        values = read_environment_of(app).values
    except (OSError, ValueError):
        return ()
    return tuple(
        sorted(
            name
            for name, value in values.items()
            if _SERVICE_VARIABLE.search(name) and _LOOPBACK.search(str(value))
        )
    )


def _scheduled_tasks(
    domain: str, cron_jobs: Callable[[], list[dict[str, Any]]] | None
) -> tuple[str, ...]:
    """
    Name the scheduled tasks that belong to an application.

    Args:
        domain: The application's domain.
        cron_jobs: A seam that returns the server's jobs.

    Returns:
        Their names, sorted.
    """
    if cron_jobs is None:
        from noust.core.exceptions import NoustError
        from noust.managers.cron_manager import CronManager

        try:
            jobs = CronManager().list_jobs()
        except NoustError:
            return ()
    else:
        jobs = cron_jobs()
    return tuple(
        sorted(
            str(job.get("name"))
            for job in jobs
            if job.get("app_domain") == domain and job.get("name")
        )
    )
