# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What every endpoint of ``/api/server`` shares.

An endpoint here does three things and no more: it turns HTTP into a call on a
manager, it records that somebody asked, and it answers. The managers
(:mod:`noust.managers.server`) are the only implementation of what the server
does; the command line is their other client. Everything below is plumbing for
that translation.

**Errors.** The managers refuse in ways the console shows differently: a host
that is busy is a wait, a confirmation is a question, an unsupported host is an
explanation. :class:`ServerRoute` gives each its own status and error code, and
leaves every other :class:`~noust.core.exceptions.NoustError` to the API's one
error contract.

**Audit.** A write is recorded when it is accepted and again when its job ends,
by :func:`audit_write` and :func:`audit_job`, with the actor named the way the
rest of the API names them. Reads are not recorded.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from noust.core import audit
from noust.core.exceptions import NoustError
from noust.managers.server.context import ServerContext
from noust.managers.server.errors import (
    ConfirmationRequiredError,
    HostBusyError,
    PreflightError,
    UnsupportedHostError,
)
from noust.web.api.deps import ErrorResponse, JobAcceptedResponse, NoustErrorRoute, error_response
from noust.web.jobs import Job, JobContext, JobStatus, JobType, get_job_manager
from noust.web.pydantic_compat import dump_model

#: Job types that belong to this area. They do not count as "something running
#: that a reboot would interrupt" when the check is made by one of them, and an
#: update or a refresh already queued refuses a second one.
SERVER_JOB_TYPES = frozenset(
    {
        JobType.OS_REFRESH,
        JobType.OS_UPDATE,
        JobType.CLEANUP,
        JobType.SWAP,
        JobType.DISK_SCAN,
        JobType.SERVER_ACTION,
    }
)

#: Package manager jobs: only one of them at a time.
PACKAGE_JOB_TYPES = frozenset({JobType.OS_REFRESH, JobType.OS_UPDATE, JobType.SERVER_ACTION})

#: The status and error code of each refusal the managers make on purpose.
_REFUSALS: tuple[tuple[type[NoustError], int, str], ...] = (
    (ConfirmationRequiredError, 409, "confirmation_required"),
    (HostBusyError, 409, "host_busy"),
    (PreflightError, 409, "preflight_failed"),
    (UnsupportedHostError, 501, "unsupported_platform"),
)


class ServerRoute(NoustErrorRoute):
    """
    A route that answers the managers' deliberate refusals with their own code.

    :class:`~noust.web.api.deps.NoustErrorRoute` answers every
    :class:`NoustError` with a status by class and an error code by class name.
    The refusals here carry data the console needs (the removal list, the
    blockers) and a status that is not the default, so they are answered first
    and everything else falls through to the shared translation.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """
        Wrap the handler so a deliberate refusal keeps its own status and code.

        Returns:
            The wrapped handler.
        """
        # The generated handler itself, not NoustErrorRoute's wrapper of it: that
        # wrapper answers every NoustError first, and would never let these through.
        raw = APIRoute.get_route_handler(self)

        async def wrapped(request: Request) -> Response:
            try:
                return await raw(request)
            except NoustError as exc:
                for kind, status, code in _REFUSALS:
                    if isinstance(exc, kind):
                        return refusal_response(exc, status, code)
                return error_response(exc)

        return wrapped


def refusal_response(exc: NoustError, status: int, code: str) -> Response:
    """
    Render a deliberate refusal in the API's error body.

    Args:
        exc: The refusal.
        status: The HTTP status.
        code: The machine-readable error code.

    Returns:
        The response. The data the console needs travels in ``fields`` only as
        text; a structured payload (``required``, ``blockers``) goes in the
        body next to the standard members.
    """
    body = dump_model(
        ErrorResponse(
            detail=exc.message,
            hint=exc.details or None,
            error=code,
            output=exc.output,
        )
    )
    for attribute in ("required", "blockers", "holders"):
        value = getattr(exc, attribute, None)
        if value:
            body[attribute] = value
    return JSONResponse(status_code=status, content=body)


_context: ServerContext | None = None
_context_lock = threading.Lock()


def running_job_names() -> list[str]:
    """
    Name what the console is running that an update or a reboot would break.

    Returns:
        The names of queued and running jobs other than this area's own.
    """
    return [
        job.name for job in get_job_manager().get_active_jobs() if job.type not in SERVER_JOB_TYPES
    ]


def get_server_context() -> ServerContext:
    """
    Return the managers of this server, built on first use.

    Returns:
        The process-wide context. The console's job queue is what tells it what
        is running.
    """
    global _context
    with _context_lock:
        if _context is None:
            _context = ServerContext(blockers=running_job_names)
        return _context


def set_server_context(context: ServerContext | None) -> None:
    """
    Replace the process-wide context.

    Args:
        context: The context to install, or None to build a new one on next use.
    """
    global _context
    with _context_lock:
        _context = context


def ensure_no_package_job() -> None:
    """
    Refuse to queue a package manager job while another is queued or running.

    Raises:
        HostBusyError: One is, naming it.
    """
    busy = [
        job.name for job in get_job_manager().get_active_jobs() if job.type in PACKAGE_JOB_TYPES
    ]
    if busy:
        raise HostBusyError(
            f"Another operation on the packages is in progress: {', '.join(busy)}",
            "Wait for it to finish; two package manager runs at once fail on its lock.",
            holders=busy,
        )


def audit_event(event: str, *, target: str, outcome: str = "ok", **details: Any) -> None:
    """
    Record that somebody changed the server, or what came of it.

    The actor is the one the console bound for the request, or the job manager for
    the job (:mod:`noust.core.audit.context`), so nothing here names them: an
    event recorded deep inside a job is still the operator's.

    Args:
        event: A name from the audit catalog (``server.update``, ``server.reboot``,
            ``server.storage``, ``server.time``, ``server.hostname``,
            ``server.refresh``).
        target: What it was done to (``packages``, ``power``, ``swap``).
        outcome: ``ok`` or ``failure``.
        **details: Context, such as ``action``, ``stage`` (``queued`` when a
            request only queued a job, ``finished`` when the job ended) and the
            job's id. Never a credential; the audit log replaces secret-looking
            names anyway.
    """
    audit.record(event, target=target, outcome=outcome, details=details or None)


def accepted(job: Job, message: str) -> JobAcceptedResponse:
    """
    Describe a queued job in the API's 202 body.

    Args:
        job: The job.
        message: One sentence for the operator.

    Returns:
        The response body.
    """
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value if isinstance(job.status, JobStatus) else str(job.status),
        message=message,
        job=job.to_dict(),
    )


def require_job_context(job_context: JobContext | None) -> JobContext:
    """
    Assert that the job manager supplied a context.

    Args:
        job_context: The context the manager injects.

    Returns:
        The context.

    Raises:
        ValueError: The function was called outside the job manager.
    """
    if job_context is None:
        raise ValueError("job_context is required; job functions run under the job manager")
    return job_context
