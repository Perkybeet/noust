# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ambient actor and correlation id of whatever Noust is doing right now.

An event recorded deep inside a manager does not know which request or which
command caused it, and threading that through every call would touch every
signature in the codebase. The console binds a correlation id per request,
the CLI per command and the job manager per job; :func:`noust.core.audit.record`
and the host action ledger read them from here, so ``systemctl restart`` run
by a deploy is linked to the request that asked for the deploy.

Context variables follow an ``asyncio`` task and ``asyncio.to_thread``; a
plain :class:`threading.Thread` starts empty, which is why a background job
binds its own (see :func:`run_with`).
"""

from __future__ import annotations

import contextvars
import re
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, TypeVar

from noust.core.audit.actor import Actor

_actor: contextvars.ContextVar[Actor | None] = contextvars.ContextVar(
    "noust_audit_actor", default=None
)
_correlation: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "noust_audit_correlation", default=None
)

_T = TypeVar("_T")


def new_correlation_id() -> str:
    """
    Mint a correlation id.

    Returns:
        Sixteen hex characters: unique enough to join events of one request,
        short enough to read in a log line.
    """
    return uuid.uuid4().hex[:16]


#: Header a caller (a central forwarding to a node) may name its request by,
#: so the same id appears in both audit logs.
REQUEST_ID_HEADER = b"x-noust-request-id"

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def request_correlation_id(scope: Mapping[str, Any]) -> str:
    """
    The correlation id of an HTTP or WebSocket request.

    Args:
        scope: The ASGI scope.

    Returns:
        The ``X-Noust-Request-Id`` it carries when that is a plain token of
        at most 64 characters, else a fresh id: a header is never trusted
        to put arbitrary text in the audit log.
    """
    for name, value in scope.get("headers") or ():
        if name.lower() == REQUEST_ID_HEADER:
            text = str(value.decode("latin-1"))
            if _REQUEST_ID.fullmatch(text):
                return text
    return new_correlation_id()


def current_actor() -> Actor | None:
    """
    The actor bound to the current context.

    Returns:
        The actor, or None when nothing bound one.
    """
    return _actor.get()


def current_correlation_id() -> str | None:
    """
    The correlation id bound to the current context.

    Returns:
        The id, or None when nothing bound one.
    """
    return _correlation.get()


@contextmanager
def bind(*, actor: Actor | None = None, correlation_id: str | None = None) -> Iterator[str]:
    """
    Bind an actor and a correlation id for the duration of a block.

    Args:
        actor: Who is acting; the current one is kept when None.
        correlation_id: The id to link events by; a fresh one when None.

    Yields:
        The correlation id in force inside the block.
    """
    correlation = correlation_id or new_correlation_id()
    actor_token = _actor.set(actor if actor is not None else _actor.get())
    correlation_token = _correlation.set(correlation)
    try:
        yield correlation
    finally:
        _correlation.reset(correlation_token)
        _actor.reset(actor_token)


def run_with(function: Callable[[], _T], *, actor: Actor | None, correlation_id: str | None) -> _T:
    """
    Call a function with an actor and a correlation id bound.

    For work handed to another thread: capture :func:`current_actor` and
    :func:`current_correlation_id` where the work is queued, then run it
    through this where it executes.

    Args:
        function: What to run.
        actor: The actor to bind.
        correlation_id: The correlation id to bind; a fresh one when None.

    Returns:
        What the function returned.
    """
    with bind(actor=actor, correlation_id=correlation_id):
        return function()
