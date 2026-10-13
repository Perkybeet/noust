# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/ws/terminal``: the socket a terminal is typed into.

Opening one is two steps (:mod:`noust.web.api.terminal` is the first): an
authenticated call, inside sudo mode, mints a ticket; this handler redeems it.
The middleware has already judged the handshake as it does every one (address,
host, origin, credential, the second-factor and usage-notice gates, the
``apps.terminal`` permission of the route), and this handler judges what only it
can: that the ticket is the one this very credential was given, that it names a
terminal this principal may still reach (its role may have changed in the few
seconds since), and that a session can be opened and recorded. Nothing starts a
shell before all of that, and only :class:`~noust.terminal.sessions.SessionRegistry`
starts one.

**Frames** are JSON text, both ways.

From the console: ``{"type": "input", "data": "..."}`` (what was typed or
pasted), ``{"type": "resize", "rows": 40, "cols": 120}``, ``{"type": "ping"}``
and ``{"type": "close"}`` (end the session). From the server:
``{"type": "ready", ...}`` first (who, where, for how long, the size, whether
this is a session that came back), then ``output``, ``notice`` (a limit is a
minute away), ``error`` and finally ``ended`` with why.

**Flow control** is the terminal's own: a console that cannot keep up makes the
reader of the program wait, the program's output buffer fills, and the program
waits too. Memory does not grow with a slow browser.

**When the socket closes**, the session lives on for a minute for the console to
come back to (a dropped network, a central's tunnel restarting), unless the
person said ``close``, or the credential it was opened with stopped being valid,
which ends it at once.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from noust.core import audit
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.runner import CommandError, get_runner
from noust.core.teams import ScopedStore
from noust.terminal.sessions import (
    END_REVOKED,
    TerminalDisabledError,
    TerminalGone,
    TerminalLimitError,
    TerminalSession,
    get_registry,
)
from noust.terminal.targets import SessionPlan, plan_app_terminal, plan_server_terminal
from noust.terminal.tickets import KIND_APP, KIND_SERVER, TerminalGrant, TicketRefused, get_tickets
from noust.web.auth import (
    WS_CLOSE_FORBIDDEN,
    WS_CLOSE_UNAUTHORIZED,
    actor_label,
    get_client_ip,
    get_security_config,
)
from noust.web.permissions import Permission
from noust.web.permissions.principal import audit_actor, permissions_of, scope_of
from noust.web.permissions.team_roles import permissions_at
from noust.web.websockets.router import (
    _accept,
    _close,
    _reject,
    _serve,
    authenticate_websocket,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: Messages a connection may be behind before the reader of the program waits.
QUEUE_MESSAGES = 64

#: How long the reader waits for a connection to take one message before it
#: treats the connection as gone.
SINK_TIMEOUT_SECONDS = 15.0

#: The largest frame the console may send: a paste, with room for its JSON.
MAX_FRAME_CHARS = 256 * 1024

#: Close code when the console sent something that is not a frame.
WS_CLOSE_PROTOCOL = 1003


class ConnectionSink:
    """
    What a session delivers to: this connection, through a bounded queue.

    Called from the session's reader thread. Waiting here is the flow control:
    it holds the reader, and with it the program.
    """

    def __init__(
        self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        """
        Args:
            loop: The event loop the connection runs on.
            queue: Where messages wait for the connection.
        """
        self._loop = loop
        self._queue = queue
        self._closed = False

    def close(self) -> None:
        """Refuse every message from now on: the connection is over."""
        self._closed = True

    def __call__(self, message: dict[str, Any]) -> None:
        """
        Hand a message to the connection.

        Args:
            message: What to say.

        Raises:
            RuntimeError: The connection is over.
            TimeoutError: The connection did not take it in time.
        """
        if self._closed:
            raise RuntimeError("the connection is closed")
        future = asyncio.run_coroutine_threadsafe(self._queue.put(message), self._loop)
        try:
            future.result(timeout=SINK_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError("the connection did not take the output in time") from exc
        except concurrent.futures.CancelledError as exc:
            raise RuntimeError("the connection is closed") from exc
        if self._closed:
            raise RuntimeError("the connection is closed")


def _refusal(session: dict[str, Any], grant: TerminalGrant) -> str | None:
    """
    Judge again, at the socket, what the ticket was minted for.

    A role changes, a team loses an application, a switch is turned off: none of
    that waits for a ticket to expire.

    Args:
        session: The handshake's payload.
        grant: What the ticket authorised.

    Returns:
        The sentence that refuses it, or None when the principal may still open it.
    """
    if grant.kind == KIND_SERVER:
        if scope_of(session).restricted:
            return "The server's terminal is for administrators of the whole server"
        if Permission.SERVER_TERMINAL not in permissions_of(session):
            return f"This needs the '{Permission.SERVER_TERMINAL}' permission"
        return None
    if grant.domain is None:
        return "The ticket names no application"
    scope = scope_of(session)
    if not scope.restricted:
        if Permission.APPS_TERMINAL not in permissions_of(session):
            return f"This needs the '{Permission.APPS_TERMINAL}' permission"
        return None
    scoped = ScopedStore(scope)
    app = scoped.get_app(grant.domain)
    if app is None:
        return f"Application not found: {grant.domain}"
    placed = scoped.placement_of_project(app.project_id, app.environment_id)
    team = placed.team_id if placed is not None else None
    held = permissions_at(
        session,
        team,
        placed.project_id if placed is not None else None,
        (placed.environment_id or None) if placed is not None else None,
    )
    if Permission.APPS_TERMINAL not in held:
        return f"This needs the '{Permission.APPS_TERMINAL}' permission where the application lives"
    return None


def _plan(grant: TerminalGrant) -> SessionPlan:
    """
    Work out what a ticket opens. Blocking: it may ask Docker.

    Args:
        grant: What the ticket authorised.

    Returns:
        The plan.
    """
    if grant.kind == KIND_SERVER:
        return plan_server_terminal(shell_name=grant.shell)
    return plan_app_terminal(
        grant.domain or "", service=grant.service, shell_name=grant.shell, runner=get_runner()
    )


def _ready(term: TerminalSession, *, resumed: bool, lost: bool) -> dict[str, Any]:
    """
    Describe a session to the connection that has just been put on it.

    Args:
        term: The session.
        resumed: It was already open: this connection came back to it.
        lost: Output was lost while nobody was attached.

    Returns:
        The ``ready`` frame.
    """
    info = term.info()
    return {
        "type": "ready",
        "session": info.id,
        "resumed": resumed,
        "lost": lost,
        "target": term.plan.target.to_dict(),
        "actor": info.actor,
        "rows": info.rows,
        "cols": info.cols,
        "started_at": info.started_at,
        "ends_at": info.ends_at,
        # Seconds, so the console counts down on its own clock: the server's and the
        # browser's are not the same.
        "ends_in": round(max(0.0, info.ends_at - time.time()), 1),
        "idle_seconds": info.idle_seconds,
        "idle_remaining": round(info.idle_remaining, 1),
        "recorded": True,
        "recording": info.recording,
    }


async def _refuse(
    websocket: WebSocket,
    session: dict[str, Any],
    resource: str,
    *,
    code: str,
    message: str,
    details: str = "",
) -> None:
    """
    Tell the console why no terminal opened, record it, and close.

    Args:
        websocket: The accepted connection.
        session: The handshake's payload.
        resource: What it asked for, for the trail.
        code: What the console branches on.
        message: The sentence for the person.
        details: What to do.
    """
    audit.record(
        "terminal.denied",
        actor=audit_actor(session),
        target=resource,
        outcome="denied",
        details={"reason": message, "code": code, "client_ip": get_client_ip(websocket)},
    )
    try:
        await websocket.send_json(
            {"type": "error", "code": code, "message": message, "details": details}
        )
    except (RuntimeError, WebSocketDisconnect):
        pass  # The console left in the same instant; there is nobody to tell.
    await _close(websocket, WS_CLOSE_FORBIDDEN)


async def _writer(websocket: WebSocket, queue: asyncio.Queue[dict[str, Any]]) -> None:
    """
    Send the session's messages to the console until the session ends.

    Args:
        websocket: The connection.
        queue: What the session delivers to.
    """
    while True:
        message = await queue.get()
        try:
            await websocket.send_json(message)
        except (RuntimeError, WebSocketDisconnect):
            return
        if message["type"] == "ended":
            return


async def _reader(websocket: WebSocket, term: TerminalSession) -> None:
    """
    Take what the console sends: typing, a resize, a goodbye.

    Args:
        websocket: The connection.
        term: The session it is on.
    """
    while True:
        try:
            raw = await websocket.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            return
        if len(raw) > MAX_FRAME_CHARS:
            await _close(websocket, 1009)
            return
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(frame, dict):
            continue
        kind = frame.get("type")
        try:
            if kind == "input" and isinstance(frame.get("data"), str):
                await run_in_threadpool(term.write, frame["data"])
            elif kind == "resize":
                rows, cols = frame.get("rows"), frame.get("cols")
                if isinstance(rows, int) and isinstance(cols, int) and rows > 0 and cols > 0:
                    await run_in_threadpool(term.resize, rows, cols)
            elif kind == "ping":
                await websocket.send_json({"type": "pong"})
            elif kind == "close":
                await run_in_threadpool(term.close, "closed")
        except (ValidationError, CommandError) as exc:
            try:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "input",
                        "message": exc.message,
                        "details": exc.details,
                    }
                )
            except (RuntimeError, WebSocketDisconnect):
                return
        except (RuntimeError, WebSocketDisconnect):
            return


@router.websocket("/terminal")
async def websocket_terminal(
    websocket: WebSocket, terminal_ticket: str | None = Query(default=None)
) -> None:
    """
    Open the terminal a ticket authorises, and type into it.

    Connect with the console's own credential (its session cookie, or a bearer
    token in the subprotocol) and ``?terminal_ticket=<ticket>`` from
    ``POST /api/apps/{domain}/terminal`` or ``POST /api/server/terminal``.

    Args:
        websocket: The client connection.
        terminal_ticket: The single-use ticket.
    """
    session = await authenticate_websocket(websocket, None)
    if session is None:
        await _reject(websocket, "/ws/terminal")
        return
    await _accept(websocket, session, "/ws/terminal")

    registry = get_registry()
    actor = actor_label(session)
    try:
        grant = get_tickets().redeem(
            terminal_ticket or "",
            sid=str(session.get("sid")),
            actor=actor,
            client_ip=get_client_ip(websocket),
            bind_address=bool(get_security_config().bind_session_to_ip),
        )
    except TicketRefused as exc:
        await _refuse(
            websocket,
            session,
            "terminal",
            code="ticket",
            message=exc.message,
            details=exc.details,
        )
        return

    resource = f"app:{grant.domain}" if grant.kind == KIND_APP else "server"
    refusal = _refusal(session, grant)
    if refusal is not None:
        await _refuse(websocket, session, resource, code="forbidden", message=refusal)
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MESSAGES)
    sink = ConnectionSink(loop, queue)
    lost = False
    try:
        if grant.resume:
            term = registry.resumable(grant.resume, sid=grant.sid, actor=grant.actor)
            lost = await run_in_threadpool(term.attach, sink)
        else:
            plan = await run_in_threadpool(_plan, grant)
            term = await run_in_threadpool(registry.open, grant, plan, actor=audit_actor(session))
            term.start(sink)
    except (TerminalDisabledError, TerminalLimitError, TerminalGone) as exc:
        code = {
            TerminalDisabledError: "disabled",
            TerminalLimitError: "limit",
            TerminalGone: "gone",
        }[type(exc)]
        await _refuse(
            websocket, session, resource, code=code, message=exc.message, details=exc.details
        )
        return
    except (ValidationError, DeploymentError, CommandError) as exc:
        # Nothing to open it in (a stack that stopped, a shell this server lacks), or
        # the terminal could not be recorded: said in the words of what failed.
        await _refuse(
            websocket,
            session,
            resource,
            code="unavailable",
            message=exc.message,
            details=exc.details,
        )
        return
    except NoustError as exc:
        logger.warning("A terminal could not be opened for %s: %s", actor, exc)
        await _refuse(
            websocket,
            session,
            resource,
            code="unavailable",
            message=exc.message,
            details=exc.details,
        )
        return

    close_code = 1000
    try:
        await websocket.send_json(_ready(term, resumed=bool(grant.resume), lost=lost))
        close_code = await _serve(
            websocket, session, _writer(websocket, queue), _reader(websocket, term)
        )
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:
        logger.debug("The terminal socket of %s closed under it: %s", actor, exc)
    finally:
        sink.close()
        # Whatever is waiting for the connection is let go, so the session's reader
        # is not held by a connection that is no longer there.
        while not queue.empty():
            queue.get_nowait()
        if not term.ended:
            if close_code == WS_CLOSE_UNAUTHORIZED:
                # The credential this was opened with is gone: so is the shell.
                await run_in_threadpool(term.close, END_REVOKED)
            else:
                term.detach(sink)
        await _close(websocket, close_code)
