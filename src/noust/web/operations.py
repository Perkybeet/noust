# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The long operations that answer on the request, told to everyone who is not waiting for them
(item 114).

About twenty things an operator does take long enough to be noticed and are not jobs: starting,
stopping and restarting an application, a service or an engine, running a cron entry, verifying a
backup, rebooting the server. They answer 200 with their result when they are done, which is a
promise of ``docs/api.md`` that no route may break, so they cannot become a 202 and a job. Only the
browser that asked sees them run.

One dependency of the API (:func:`track_operation`) announces each one on the closed list
:data:`OPERATIONS`, as the ``operation`` event of the stream (a ``start`` and an ``end``), and
:class:`ActiveOperations` keeps the ones that run for ``GET /api/operations/active``. The list is
closed on purpose: a route that is not on it is not announced, and adding one is a decision (a
sentence in :mod:`noust.core.server_text` and a line here). A central asks each server's list
through its tunnel, so nothing runs twice.

What a team member sees of them is what concerns its own applications, as for a job: the stream
and the list hold an operation to the application it names (``domain``), and one that is about the
machine (a reboot, a service) has none and is the instance's.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Request

from noust.core.server_text import ServerText, say
from noust.core.timeutil import utc_now_iso
from noust.web.auth import actor_label, require_auth
from noust.web.events import hub
from noust.web.permissions.enforce import route_template

__all__ = [
    "MAX_TARGET",
    "OPERATIONS",
    "ActiveOperations",
    "Operation",
    "Running",
    "active_operations",
    "track_operation",
]

#: The longest subject an announcement carries, so a long path segment is not echoed to every
#: open console.
MAX_TARGET = 253

#: An operation that has not ended after this long is forgotten by the list of what runs: its
#: request was cut off before the dependency could end it. No operation of the list takes minutes,
#: let alone hours.
_FORGET_AFTER_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class Operation:
    """
    One operation of the closed list.

    Attributes:
        code: Its sentence, in :mod:`noust.core.server_text`.
        target: The path parameter that names its subject (``domain``, ``name``), or None for an
            operation about the whole server. The sentence is said with it as ``target``.
    """

    code: str
    target: str | None = None


#: ``(method, route template)`` to what is said of it. The closed list: ``tests/test_operations.py``
#: holds it to what the plan names, to the routes of the application, and each of them to a
#: response of 200.
OPERATIONS: dict[tuple[str, str], Operation] = {
    ("POST", "/api/apps/{domain}/start"): Operation("operations.app_start", "domain"),
    ("POST", "/api/apps/{domain}/stop"): Operation("operations.app_stop", "domain"),
    ("POST", "/api/apps/{domain}/restart"): Operation("operations.app_restart", "domain"),
    ("POST", "/api/services/{name}/start"): Operation("operations.service_start", "name"),
    ("POST", "/api/services/{name}/stop"): Operation("operations.service_stop", "name"),
    ("POST", "/api/services/{name}/restart"): Operation("operations.service_restart", "name"),
    ("POST", "/api/databases/engines/{engine}/start"): Operation(
        "operations.engine_start", "engine"
    ),
    ("POST", "/api/databases/engines/{engine}/stop"): Operation("operations.engine_stop", "engine"),
    ("POST", "/api/databases/engines/{engine}/restart"): Operation(
        "operations.engine_restart", "engine"
    ),
    ("POST", "/api/cron/{name}/run"): Operation("operations.cron_run", "name"),
    ("POST", "/api/backups/{backup_id}/verify"): Operation("operations.backup_verify", "backup_id"),
    ("POST", "/api/monitor/install"): Operation("operations.monitor_install"),
    ("POST", "/api/monitor/scan"): Operation("operations.monitor_scan"),
    ("POST", "/api/probes/{name}/check"): Operation("operations.probe_check", "name"),
    ("POST", "/api/nodes/{node}/test"): Operation("operations.node_test", "node"),
    ("POST", "/api/waf/install"): Operation("operations.waf_install"),
    ("POST", "/api/compose/import"): Operation("operations.compose_import"),
    ("POST", "/api/apps/{domain}/env/sync"): Operation("operations.env_sync", "domain"),
    ("POST", "/api/apps/{domain}/move"): Operation("operations.app_move", "domain"),
    ("DELETE", "/api/apps/{domain}/build-cache"): Operation(
        "operations.build_cache_clear", "domain"
    ),
    ("PUT", "/api/apps/{domain}/build"): Operation("operations.build_apply", "domain"),
    ("POST", "/api/server/power/reboot"): Operation("operations.power_reboot"),
    ("POST", "/api/server/power/shutdown"): Operation("operations.power_shutdown"),
    ("POST", "/api/sites/reload"): Operation("operations.sites_reload"),
    ("POST", "/api/apps/{domain}/apply"): Operation("operations.app_apply", "domain"),
    ("POST", "/api/fleet/settings/apply"): Operation("operations.fleet_settings_apply"),
    ("POST", "/api/backups/encryption/enable"): Operation("operations.encryption_enable"),
    ("POST", "/api/backups/encryption/rotate"): Operation("operations.encryption_rotate"),
}


@dataclass
class Running:
    """
    One operation that has started.

    Attributes:
        id: ``op-`` and eight hexadecimal digits: names the start and the end of one operation.
        title: What it is, as a sentence of the catalog.
        target: Its subject, or None when it is about the whole server.
        domain: The application it concerns, when its subject is one: what a team member's
            scope holds it to.
        actor: Who asked, as every audit record names them.
        started_at: When, ISO 8601 in UTC.
    """

    id: str
    title: ServerText
    target: str | None
    domain: str | None
    actor: str | None
    started_at: str
    began: float = field(default_factory=time.monotonic, repr=False)

    def event(self, phase: str, result: str | None = None) -> dict[str, Any]:
        """
        Args:
            phase: ``start`` or ``end``.
            result: ``ok`` or ``failed``, for the end.

        Returns:
            The body of the ``operation`` event.
        """
        body: dict[str, Any] = {
            "id": self.id,
            "phase": phase,
            "title": self.title.to_dict(),
            "target": self.target,
            "domain": self.domain,
            "actor": self.actor,
            "at": self.started_at if phase == "start" else utc_now_iso("seconds"),
        }
        if result is not None:
            body["result"] = result
        return body


def _target_of(operation: Operation, params: Mapping[str, Any]) -> str | None:
    """
    Args:
        operation: One of the closed list.
        params: The path parameters of the request.

    Returns:
        The subject, as one line of at most :data:`MAX_TARGET` characters (``?`` when nothing
        printable is left of it); None for an operation about the whole server.
    """
    if operation.target is None:
        return None
    raw = str(params.get(operation.target, ""))
    # A path segment is decoded text: it may carry a line break, and the sentence and the stream
    # show it as one line.
    return "".join(char for char in raw if char.isprintable())[:MAX_TARGET] or "?"


class ActiveOperations:
    """The operations that are running now, in this process."""

    def __init__(self) -> None:
        """Start with nothing running."""
        self._running: dict[str, Running] = {}
        self._lock = threading.Lock()

    def begin(self, operation: Operation, params: Mapping[str, Any], actor: str | None) -> Running:
        """
        Record that an operation started, and announce it.

        Args:
            operation: Which one, from :data:`OPERATIONS`.
            params: The path parameters of the request, one of which is its subject.
            actor: Who asked.

        Returns:
            The running operation.
        """
        target = _target_of(operation, params)
        running = Running(
            id=f"op-{uuid.uuid4().hex[:8]}",
            title=say(operation.code, **({"target": target} if target is not None else {})),
            target=target,
            domain=target if operation.target == "domain" else None,
            actor=actor,
            started_at=utc_now_iso("seconds"),
        )
        with self._lock:
            self._running[running.id] = running
        hub.publish("operation", running.event("start"))
        return running

    def finish(self, running: Running, result: str) -> None:
        """
        Record that an operation ended, and announce it.

        Args:
            running: What :meth:`begin` returned.
            result: ``ok`` when the route answered, ``failed`` when it raised.
        """
        with self._lock:
            self._running.pop(running.id, None)
        hub.publish("operation", running.event("end", result))

    def active(self) -> list[Running]:
        """
        Returns:
            What is running now, oldest first. One that has outlived any operation is dropped.
        """
        now = time.monotonic()
        with self._lock:
            for key in [
                k for k, r in self._running.items() if now - r.began > _FORGET_AFTER_SECONDS
            ]:
                del self._running[key]
            return sorted(self._running.values(), key=lambda running: running.began)

    def clear(self) -> None:
        """Forget everything. For tests."""
        with self._lock:
            self._running.clear()


#: The one list of what runs in this process.
active_operations = ActiveOperations()


async def track_operation(
    request: Request, session: Annotated[dict[str, Any], Depends(require_auth)]
) -> AsyncIterator[None]:
    """
    Announce a route of the closed list as it starts and as it ends.

    A dependency of the whole API, after the guards (authentication, scope, approval), so a
    request that is refused announces nothing. It changes nothing about the route: the response
    and its code are the handler's.

    Args:
        request: The request.
        session: The authenticated payload.

    Yields:
        Once, for the handler to run.
    """
    operation = OPERATIONS.get((request.method.upper(), route_template(request) or ""))
    if operation is None:
        yield
        return
    running = active_operations.begin(
        operation, request.path_params, actor_label(session) if session else None
    )
    result = "failed"
    try:
        yield
        result = "ok"
    finally:
        active_operations.finish(running, result)
