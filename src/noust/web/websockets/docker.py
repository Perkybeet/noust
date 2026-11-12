# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/ws/docker/logs/{ref}``: the log of any container, followed live.

The twin of ``/ws/logs/{domain}`` for what the Docker zone shows: a container of an application, of a
database engine, of Noust or of nobody. It is a client of the gate (rule 3): the container is resolved
to its owner by :class:`~noust.core.docker.gate.DockerGate` before anything is started, a principal that
may not see it is answered as if it did not exist, and the lines are read through the runner with the
credentials in them masked. Nothing here runs ``docker``.

The handshake is held to ``docker.read`` by the middleware like every stream, and the handler holds it to
the machine: a person of a team is told the container is not there, whatever it asks for.

**Frames** are JSON text. From the server: ``{"type": "connected", "container": name, "owner": {...}}``
first, then ``{"type": "log", "data": line}`` for each line (the time Docker prints is the line's first
word), ``{"type": "ended"}`` when the container's log ends, and ``{"type": "error", "message": ...}``. From
the console: ``{"type": "ping"}``, answered with ``pong``.

**Flow control** is the connection's: the thread that reads Docker waits while a slow browser takes
its lines, so memory does not grow, and when the socket closes Docker's process is stopped with everything
it started.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from noust.core.docker.errors import DockerNotFoundError
from noust.core.docker.gate import DockerGate
from noust.core.exceptions import DockerError, NoustError
from noust.core.runner import CommandCancelled, cancellable
from noust.web.auth import WS_CLOSE_FORBIDDEN
from noust.web.permissions.principal import scope_of
from noust.web.websockets.router import (
    WS_MAX_LIFETIME_SECONDS,
    _accept,
    _close,
    _reject,
    _serve,
    authenticate_websocket,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: Lines a connection may be behind before the thread that reads Docker waits.
QUEUE_LINES = 256

#: How long that thread waits for the connection to take a line before it treats it as gone.
SINK_TIMEOUT_SECONDS = 15.0


async def _say(websocket: WebSocket, message: dict[str, Any]) -> bool:
    """
    Args:
        websocket: The connection.
        message: What to tell the console.

    Returns:
        Whether it was delivered; False when the console left in the same instant.
    """
    try:
        await websocket.send_json(message)
    except (RuntimeError, WebSocketDisconnect):
        return False
    return True


@router.websocket("/docker/logs/{ref}")
async def websocket_docker_logs(
    websocket: WebSocket,
    ref: str,
    ticket: str | None = Query(default=None),
    lines: int = Query(default=100, ge=1, le=5000),
) -> None:
    """
    Follow the log of a container in real time.

    Connect with the session cookie, with ``Sec-WebSocket-Protocol: noust.auth, noust.token.<token>``, or
    with ``?ticket=<single-use ticket>``. The container is the one named by its name or by the id Docker
    shows; what a principal may not see is told as what is not there.

    Args:
        websocket: The client connection.
        ref: The container's name or id.
        ticket: Optional single-use handshake ticket.
        lines: The backlog of lines to send first (1 to 5000).
    """
    path = f"/ws/docker/logs/{ref}"
    session = await authenticate_websocket(websocket, ticket)
    if session is None:
        await _reject(websocket, path)
        return
    await _accept(websocket, session, path)

    scope = scope_of(session)
    gate = DockerGate(scope=scope)
    try:
        if scope.restricted:
            # The Docker zone is the machine's: a person of a team sees its containers from its
            # application, and is told here that this one is not there, as for one that is not.
            raise DockerNotFoundError(f"No container {ref!r} on this server")
        container = await run_in_threadpool(_resolve, gate, ref)
    except NoustError as exc:
        await _say(websocket, {"type": "error", "message": exc.message})
        await _close(websocket, WS_CLOSE_FORBIDDEN)
        return

    if not await _say(
        websocket,
        {"type": "connected", "container": container.name, "owner": container.owner.to_dict()},
    ):
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=QUEUE_LINES)
    stop = threading.Event()
    failure: list[NoustError] = []

    def push(line: str) -> None:
        """
        Hand a line to the connection, waiting while it is slow.

        Args:
            line: What the container wrote.

        Raises:
            CommandCancelled: The connection is gone: stop reading Docker.
        """
        future = asyncio.run_coroutine_threadsafe(queue.put(line), loop)
        try:
            future.result(timeout=SINK_TIMEOUT_SECONDS)
        except (concurrent.futures.TimeoutError, concurrent.futures.CancelledError):
            future.cancel()
            stop.set()
        if stop.is_set():
            raise CommandCancelled("The connection that followed this log is gone")

    def follow() -> None:
        """Read Docker's follow until the container ends, the time is up or the connection is gone."""
        try:
            with cancellable(stop):
                gate.follow_container_logs(
                    container.id, push, tail=lines, seconds=int(WS_MAX_LIFETIME_SECONDS)
                )
        except CommandCancelled:
            pass
        except DockerError as exc:
            failure.append(exc)
        except NoustError as exc:
            failure.append(exc)
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    reader = threading.Thread(target=follow, name=f"docker-logs-{container.name}", daemon=True)

    async def read_logs() -> None:
        """Send each line to the console until the container's log ends."""
        while True:
            line = await queue.get()
            if line is None:
                break
            if not await _say(websocket, {"type": "log", "data": line}):
                return
        if failure:
            await _say(websocket, {"type": "error", "message": failure[0].message})
        else:
            await _say(websocket, {"type": "ended"})

    async def handle_messages() -> None:
        """Answer the console's pings; anything else is ignored."""
        while True:
            try:
                raw = await websocket.receive_text()
                message = json.loads(raw)
            except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
                break
            if isinstance(message, dict) and message.get("type") == "ping":
                await _say(websocket, {"type": "pong"})

    close_code = 1000
    reader.start()
    try:
        close_code = await _serve(websocket, session, read_logs(), handle_messages())
    except WebSocketDisconnect:
        pass
    except (RuntimeError, NoustError) as exc:
        # The socket closed under a worker, or Docker failed after the stream began: said, and logged.
        logger.warning("The log of %s could not be followed: %s", container.name, exc)
        await _say(websocket, {"type": "error", "message": str(exc)})
    finally:
        # Docker's process and everything it started stop with the connection.
        stop.set()
        await _close(websocket, close_code)


def _resolve(gate: DockerGate, ref: str) -> Any:
    """
    Resolve the container to follow. Blocking: it asks Docker.

    Args:
        gate: The gate, as the principal.
        ref: The container's name or id.

    Returns:
        The container, with its owner.

    Raises:
        DockerNotFoundError: There is none, or the principal may not see it.
        DockerUnavailableError: Docker is not there or is below what Noust speaks.
    """
    gate.require_usable()
    return gate.container(ref)
