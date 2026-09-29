# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central's proxy to its nodes: API calls, the event stream and WebSockets.

A central re-implements nothing a node does (rule 3). Every page of the
console works for a node because every call it makes is forwarded, through
the node's SSH tunnel, to the node's own API:

- ``/api/nodes/{node}/api/{path}`` - any method - is the node's
  ``/api/{path}``, with the query, the body and the response as they are:
  the node's error contract reaches the console intact, so a node's nginx
  error reads exactly as a local one.
- ``/api/nodes/{node}/events`` is the node's ``/events`` stream.
- ``/ws/nodes/{node}/{path}`` is the node's ``/ws/{path}`` (logs, jobs).

What the proxy adds is authority, never content. The node sees its fleet token
and three headers: who is acting on the central (``X-Noust-Actor``), with
what scope (``X-Noust-Actor-Scope``, so a read-only credential here reads
the node as a read-only credential there), and whether the central confirmed
that operator's sudo mode (``X-Noust-Elevated``). The node's schema marks
what needs sudo mode (``x-noust-requires-elevation``); the central asks for
its own operator's confirmation before forwarding such a call, and the node
refuses it anyway if the central did not vouch - one source of truth, checked
on both sides.

What the proxy removes: the central's own cookies and ``Authorization`` on
the way in, anything a node sets as a cookie on the way out, and a ``401``
from the node - which is about the central's token, not the operator's
session, and would otherwise sign the operator out of the central.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Protocol, cast
from urllib.parse import parse_qsl, quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from noust.core.exceptions import NodeError, NodeRefusedError
from noust.core.sealing import SealError
from noust.core.store import NodeRecord
from noust.fleet.client import load_httpx
from noust.web.api import nodes as nodes_api
from noust.web.api.deps import ELEVATION_EXEMPT_TYPES, NoustErrorRoute, ensure_elevated
from noust.web.api.openapi import ELEVATION_EXTENSION
from noust.web.auth import (
    SAFE_METHODS,
    SCOPE_RANK,
    WS_CLOSE_FORBIDDEN,
    WS_CLOSE_UNAUTHORIZED,
    WS_SUBPROTOCOL,
    actor_label,
    credential_is_current,
    fleet_refusal,
    get_audit_logger,
    get_client_ip,
    is_elevated,
    require_auth,
)
from noust.web.events import CREDENTIAL_RECHECK_SECONDS, HEARTBEAT_SECONDS, shutting_down

if TYPE_CHECKING:
    import httpx
else:
    # FleetUnavailableError when httpx is missing: api/router.py then mounts
    # a stand-in that says so, and the rest of the console still starts.
    httpx = load_httpx()

logger = logging.getLogger(__name__)

router = APIRouter(route_class=NoustErrorRoute)
# The class only shapes HTTP errors; set for the one convention every router follows.
ws_router = APIRouter(route_class=NoustErrorRoute)

#: Methods the API proxy forwards. ``OPTIONS`` is the central's own business:
#: a preflight is answered by the central's CORS policy, never a node's.
PROXY_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]

#: Request headers a node is given, and nothing else: content negotiation and
#: conditional requests. Cookies, ``Authorization``, the CSRF header and any
#: forwarding header of the central's stay here; the node gets its fleet
#: token instead.
FORWARDED_REQUEST_HEADERS = (
    "accept",
    "accept-language",
    "content-type",
    "if-match",
    "if-modified-since",
    "if-none-match",
    "if-unmodified-since",
    "last-event-id",
    "range",
)

#: Response headers the console is given back. ``set-cookie`` is never one: a
#: node's cookie would be set on the central's origin. Hop-by-hop and length
#: headers are the central's own to write, and the security headers are
#: replaced by the central's middleware anyway.
RETURNED_RESPONSE_HEADERS = (
    "accept-ranges",
    "content-disposition",
    "content-language",
    "content-range",
    "content-type",
    "etag",
    "last-modified",
    "retry-after",
    "vary",
)

#: How long the proxy waits for a node. Reading allows for the node's slow
#: synchronous endpoints (a certificate issued inline); connecting does not,
#: because the tunnel ends on loopback and either answers at once or is down.
HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)

#: The node's event stream sends a keepalive every HEARTBEAT_SECONDS; three
#: missed in a row means the tunnel is gone, and the stream ends so the
#: console's EventSource reconnects.
EVENTS_READ_TIMEOUT = 3 * HEARTBEAT_SECONDS

#: WebSocket limits towards a node: the size of one message, and how many
#: messages may wait before the proxy stops reading from the node. The node's
#: own sends then wait on TCP, so a slow browser slows its stream instead of
#: growing the central's memory.
WS_MAX_MESSAGE_BYTES = 1024 * 1024
WS_MAX_QUEUE = 16
WS_OPEN_TIMEOUT = 15.0

#: Close code for a node that could not be reached, mirroring HTTP 502, for
#: a node that is not registered here, mirroring 404, and for a central whose
#: sealed secrets are locked, mirroring 423.
WS_CLOSE_NODE_UNREACHABLE = 4502
WS_CLOSE_NODE_NOT_FOUND = 4404
WS_CLOSE_CENTRAL_LOCKED = 4423

#: How long a node's schema is trusted without asking again: its elevation
#: map is refreshed at once when the node's version changes, and after this
#: long when the node never reported one.
SCHEMA_TTL_SECONDS = 3600.0
UNVERSIONED_SCHEMA_TTL_SECONDS = 300.0

#: The largest error body read from a node to carry in a translated error.
_MAX_ERROR_BODY = 64 * 1024

#: The largest OpenAPI document read from a node. Noust's own (committed at
#: panel/openapi.json) is a few hundred KiB; this gives it ample headroom
#: without letting a misbehaving or hostile node make the central buffer an
#: unbounded body in memory for every proxied call.
_MAX_SCHEMA_BYTES = 8 * 1024 * 1024

#: A path template parameter in an OpenAPI path.
_TEMPLATE_PARAMETER = re.compile(r"\{[^/{}]+\}")


# --------------------------------------------------------------- upstream


@dataclass(frozen=True)
class Upstream:
    """
    Where a node answers, and what every request to it carries.

    Attributes:
        base_url: The node's console through its tunnel, ``http://127.0.0.1:<port>``.
        headers: The fleet token, the actor, the actor's scope and, when the
            central confirmed it, the elevation.
        release: Returns the lease a stream holds on the tunnel (see
            :func:`open_upstream`); does nothing for a plain call.
    """

    base_url: str
    headers: dict[str, str]
    release: Callable[[], None] = field(default=lambda: None, compare=False, repr=False)


def central_elevated(session: dict[str, Any]) -> bool:
    """
    Report whether sudo mode on the central covers this credential right now.

    The same rule :func:`noust.web.api.deps.ensure_elevated` applies here: a
    master token or an API token is not asked, a session is asked once every
    ten minutes.

    Args:
        session: The central's authenticated payload.

    Returns:
        True when the central would let this credential run an elevated action.
    """
    return session.get("type") in ELEVATION_EXEMPT_TYPES or is_elevated(session)


def open_upstream(node: NodeRecord, session: dict[str, Any], *, hold: bool = False) -> Upstream:
    """
    Open (or reuse) the node's tunnel and say who is asking. Blocking.

    Args:
        node: The node.
        session: The central's authenticated payload.
        hold: Take a lease on the tunnel, for a stream: the idle reaper never
            closes a tunnel a live stream is using, however long it runs
            without another call. The caller returns it with
            ``upstream.release()`` when the stream ends.

    Returns:
        The node's address and the headers for it.

    Raises:
        NodeUnreachableError: When the tunnel cannot be opened.
        NodeRefusedError: When the node already refused the fleet token; it is
            not presented again until ``noust node test``.
        SecretsLockedError: When this central is sealed and locked.
    """
    from noust.fleet.client import actor_label as fleet_actor

    client = nodes_api.node_client(node)
    scope = str(session.get("scope") or "read")
    headers = client.auth_headers(
        actor=fleet_actor(actor_label(session)),
        actor_scope=scope if scope in SCOPE_RANK else "read",
        elevated=central_elevated(session),
    )
    if not hold:
        return Upstream(base_url=client.base_url().rstrip("/"), headers=dict(headers))
    (host, port), release = client.tunnels.hold(node.name)
    return Upstream(base_url=f"http://{host}:{port}", headers=dict(headers), release=release)


async def refused_by(node: NodeRecord, body: str) -> HTTPException:
    """
    Record that a node refused the fleet token, and build the error for it.

    The node is not presented the token again - by this proxy, the console's
    polling or ``noust fleet status`` - until ``noust node test``: each
    refusal is an audited failure on the node.

    Args:
        node: The node.
        body: The node's answer, verbatim.

    Returns:
        The 502 ``node_refused`` to raise.
    """
    await run_in_threadpool(lambda: nodes_api.node_client(node).mark_refused())
    return node_refused(node, body)


def _new_client(upstream: Upstream, timeout: httpx.Timeout = HTTP_TIMEOUT) -> httpx.AsyncClient:
    """
    An HTTP client for one proxied call.

    Args:
        upstream: The node.
        timeout: The call's timeouts.

    Returns:
        A client that never follows redirects and ignores the environment's
        proxy settings: the tunnel is on loopback, and a proxy in between
        would see the fleet token.
    """
    return httpx.AsyncClient(
        base_url=upstream.base_url,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


# ----------------------------------------------------------- error shapes


def _error(
    status_code: int, error: str, detail: str, hint: str, output: str | None = None
) -> HTTPException:
    """
    Build an error in the API's contract.

    Args:
        status_code: HTTP status.
        error: Machine-readable code.
        detail: What went wrong.
        hint: What to do about it.
        output: The other side's own words, verbatim.

    Returns:
        The exception to raise.
    """
    return HTTPException(
        status_code=status_code,
        detail={"error": error, "detail": detail, "hint": hint, "fields": None, "output": output},
    )


def node_unreachable(node: NodeRecord, exc: Exception) -> HTTPException:
    """
    The error for a node whose tunnel failed mid-call.

    Args:
        node: The node.
        exc: What httpx or websockets raised.

    Returns:
        A 502 ``node_unreachable``.
    """
    return _error(
        502,
        "node_unreachable",
        f"Node {node.name} did not answer through its tunnel.",
        "Check the node from Settings, or run `noust node test " + node.name + "`.",
        output=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
    )


def node_refused(node: NodeRecord, body: str) -> HTTPException:
    """
    The error for a node that refused the central's fleet token.

    Args:
        node: The node.
        body: The node's answer, verbatim.

    Returns:
        A 502 ``node_refused``: never a 401, which the console would read as
        its own session having ended.
    """
    return _error(
        502,
        "node_refused",
        f"Node {node.name} refused this central's fleet token.",
        "The token was revoked or the node was re-authorized. Run `noust fleet "
        "authorize` on the node again and register it with the new join code.",
        output=body,
    )


# -------------------------------------------------------- elevation map


@dataclass(frozen=True)
class NodeSchema:
    """
    What a node's OpenAPI says about sudo mode, compiled for matching.

    Attributes:
        version: The node version it was fetched for.
        fetched_at: When, on the monotonic clock.
        operations: Path pattern and ``{METHOD: requires elevation}``, in the
            node's route order - the order the node itself matches in.
    """

    version: str | None
    fetched_at: float
    operations: tuple[tuple[re.Pattern[str], dict[str, bool]], ...]

    def requires_elevation(self, method: str, path: str) -> bool:
        """
        Report whether the node marks an operation as elevated.

        Args:
            method: The HTTP method.
            path: The path on the node, starting with ``/api/``.

        Returns:
            True when the first operation matching the path and the method, as
            the node would route it, carries the extension. An operation the
            schema does not name is not elevated here; the node still refuses
            it if it is and the central did not vouch. A trailing slash or a
            doubled one matches exactly as the plain path does (see
            :func:`_normalize_node_path`): ``node_path`` already refuses an
            empty path segment before a proxied call reaches this check, so
            this only matters to a caller that does not go through it, but it
            must never silently read as unelevated instead.
        """
        verb = method.upper()
        normalized = _normalize_node_path(path)
        for pattern, methods in self.operations:
            if verb in methods and pattern.fullmatch(normalized):
                return methods[verb]
        return False


def _normalize_node_path(path: str) -> str:
    """
    Normalise a node path before matching it against a schema.

    Duplicate slashes are collapsed and one trailing slash is stripped, so
    ``/api/apps/x/`` and ``/api//apps/x`` match the same operation as
    ``/api/apps/x``: the node treats them the same route, and the elevation
    check must agree with it rather than read a differently-spelled path as
    an operation the schema never named.

    Args:
        path: The path, starting with ``/``.

    Returns:
        The normalised path.
    """
    collapsed = re.sub(r"/{2,}", "/", path)
    if len(collapsed) > 1 and collapsed.endswith("/"):
        collapsed = collapsed[:-1]
    return collapsed


def compile_schema(document: dict[str, Any], version: str | None) -> NodeSchema:
    """
    Compile a node's OpenAPI document into its elevation map.

    Args:
        document: The node's ``/api/openapi.json``.
        version: The node's version, the cache key.

    Returns:
        The compiled map.
    """
    operations: list[tuple[re.Pattern[str], dict[str, bool]]] = []
    for template, item in (document.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        pieces = _TEMPLATE_PARAMETER.split(template)
        pattern = "[^/]+".join(re.escape(piece) for piece in pieces)
        methods = {
            method.upper(): bool(operation.get(ELEVATION_EXTENSION))
            for method, operation in item.items()
            if isinstance(operation, dict)
        }
        operations.append((re.compile(pattern), methods))
    return NodeSchema(version=version, fetched_at=time.monotonic(), operations=tuple(operations))


class NodeSchemas:
    """
    Each node's elevation map, cached per node and version.
    """

    def __init__(self) -> None:
        """Start with nothing cached."""
        self._schemas: dict[str, NodeSchema] = {}

    def cached(self, node: NodeRecord) -> NodeSchema | None:
        """
        The cached map for a node, if it is still the node's.

        Args:
            node: The node.

        Returns:
            The map, or None when there is none, the node's version changed,
            or it is older than its time to live.
        """
        schema = self._schemas.get(node.name)
        if schema is None or schema.version != node.version:
            return None
        ttl = SCHEMA_TTL_SECONDS if node.version else UNVERSIONED_SCHEMA_TTL_SECONDS
        if time.monotonic() - schema.fetched_at > ttl:
            return None
        return schema

    async def get(
        self, node: NodeRecord, client: httpx.AsyncClient, upstream: Upstream
    ) -> NodeSchema:
        """
        The node's elevation map, fetched through the tunnel when needed.

        Args:
            node: The node.
            client: A client for the node.
            upstream: The node's headers.

        Returns:
            The map.

        Raises:
            HTTPException: 502 when the node does not serve its schema, or
                serves one larger than :data:`_MAX_SCHEMA_BYTES`.
        """
        schema = self.cached(node)
        if schema is not None:
            return schema
        # Streamed and capped while reading, not after: a node's own claimed
        # Content-Length is not trusted, so the only way to bound how much of
        # a hostile or broken node's answer the central ever buffers is to
        # stop reading once the cap is passed.
        request = client.build_request("GET", "/api/openapi.json", headers=upstream.headers)
        response = await client.send(request, stream=True)
        try:
            if response.status_code == 401:
                body = (await response.aread()).decode("utf-8", "replace")[:_MAX_ERROR_BODY]
                raise await refused_by(node, body)
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", "replace")[:_MAX_ERROR_BODY]
                raise _error(
                    502,
                    "node_unreachable",
                    f"Node {node.name} did not serve its API schema (HTTP {response.status_code}).",
                    "The central reads it to know which calls need sudo mode. Check the "
                    "node's version with `noust node test " + node.name + "`.",
                    output=body,
                )
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw += chunk
                if len(raw) > _MAX_SCHEMA_BYTES:
                    raise _error(
                        502,
                        "node_unreachable",
                        f"Node {node.name} served an API schema larger than "
                        f"{_MAX_SCHEMA_BYTES // (1024 * 1024)} MiB.",
                        "This is larger than any schema Noust generates; check the node's "
                        "version with `noust node test " + node.name + "`.",
                    )
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise _error(
                    502,
                    "node_unreachable",
                    f"Node {node.name} served an API schema that is not valid JSON.",
                    "Check the node's version with `noust node test " + node.name + "`.",
                ) from exc
        finally:
            await response.aclose()
        schema = compile_schema(document, node.version)
        self._schemas[node.name] = schema
        return schema

    def forget(self, name: str | None = None) -> None:
        """
        Drop a node's map, or every map.

        Args:
            name: The node, or None for all of them.
        """
        if name is None:
            self._schemas.clear()
        else:
            self._schemas.pop(name, None)


#: One cache per process, like the tunnels it reads through.
node_schemas = NodeSchemas()


# ----------------------------------------------------------------- paths


def node_path(prefix: str, path: str) -> str:
    """
    Build the path on the node, refusing one that could climb out of it.

    Args:
        prefix: ``/api/`` or ``/ws/``.
        path: The rest, as the route captured it (percent-decoded).

    Returns:
        ``prefix + path``, decoded.

    Raises:
        HTTPException: 400 on an empty, ``.`` or ``..`` segment.
    """
    segments = path.split("/")
    if not path or any(segment in ("", ".", "..") for segment in segments):
        raise _error(
            400,
            "validation_error",
            "The node path has an empty, '.' or '..' segment.",
            "Send the node's API path exactly as the node names it.",
        )
    return prefix + path


def _quoted(path: str) -> str:
    """
    Percent-encode a decoded path for the request line again.

    Args:
        path: The decoded path.

    Returns:
        The path, safe to put on a request line.
    """
    return quote(path, safe="/:@!$&'()*+,;=-._~")


def _query_without_ticket(raw: bytes) -> str:
    """
    The query string, minus the central's own WebSocket ticket.

    Args:
        raw: The query string as received.

    Returns:
        The query string to forward; ``ticket`` authenticated the handshake
        to the central and means nothing to the node.
    """
    pairs = parse_qsl(raw.decode("latin-1"), keep_blank_values=True)
    return urlencode([(key, value) for key, value in pairs if key != "ticket"])


def _refuse_path(method: str, target: str) -> None:
    """
    Refuse, before any tunnel opens, what no node would accept from a central.

    Args:
        method: The HTTP method.
        target: The path on the node.

    Raises:
        HTTPException: 403 when :func:`noust.web.auth.fleet_refusal` names it.
    """
    reason = fleet_refusal(method, target)
    if reason is not None:
        raise _error(403, "forbidden", reason, "Sign in to the node's own console for this.")


def _forwarded_headers(request: Request, upstream: Upstream) -> dict[str, str]:
    """
    The headers a proxied request carries to the node.

    Args:
        request: The incoming request.
        upstream: The node.

    Returns:
        The allowed request headers, plus the fleet's; the body's own
        encoding is identity, so the raw bytes relayed back are what the
        ``content-type`` says they are.
    """
    headers = {
        name: value
        for name in FORWARDED_REQUEST_HEADERS
        if (value := request.headers.get(name)) is not None
    }
    headers["accept-encoding"] = "identity"
    headers.update(upstream.headers)
    return headers


def _returned_headers(
    response: httpx.Response, node: NodeRecord, upstream: Upstream
) -> dict[str, str]:
    """
    The node's response headers the console is given.

    Args:
        response: The node's response.
        node: The node, for rewriting a redirect.
        upstream: The node's address, for rewriting a redirect.

    Returns:
        The allowed headers; a ``location`` into the node's API is rewritten
        to the same place through the proxy, any other is dropped.
    """
    headers = {
        name: value
        for name in RETURNED_RESPONSE_HEADERS
        if (value := response.headers.get(name)) is not None
    }
    location = response.headers.get("location")
    if location is not None:
        relative = location.removeprefix(upstream.base_url)
        if relative.startswith("/api/"):
            headers["location"] = f"/api/nodes/{quote(node.name)}{relative}"
    return headers


# -------------------------------------------------------------- the proxy


async def _relay(response: httpx.Response, node: NodeRecord) -> AsyncIterator[bytes]:
    """
    Relay a node's response body as it arrives.

    Args:
        response: The node's streamed response.
        node: The node, for the log.

    Yields:
        The body's bytes, unchanged.
    """
    try:
        async for chunk in response.aiter_raw():
            yield chunk
    except httpx.TransportError as exc:
        # The status and headers are already on their way; all that is left
        # is to end the body where the node did.
        logger.warning("Node %s dropped a proxied response mid-body: %s", node.name, exc)


async def _close(response: httpx.Response, client: httpx.AsyncClient) -> None:
    """
    Release a proxied call's connection once the body has been relayed.

    Args:
        response: The node's response.
        client: The call's client.
    """
    await response.aclose()
    await client.aclose()


async def _prepare(
    node: str, session: dict[str, Any], *, hold: bool = False
) -> tuple[NodeRecord, Upstream]:
    """
    Find the node and open its tunnel, off the event loop.

    Args:
        node: The node's name.
        session: The central's authenticated payload.
        hold: Lease the tunnel for a stream (:func:`open_upstream`).

    Returns:
        The node and its upstream.
    """
    record = await run_in_threadpool(nodes_api.find_node, node)
    upstream = await run_in_threadpool(lambda: open_upstream(record, session, hold=hold))
    return record, upstream


@router.api_route("/{node}/api/{path:path}", methods=PROXY_METHODS, include_in_schema=False)
async def proxy_api(
    node: str,
    path: str,
    request: Request,
    session: Annotated[dict[str, Any], Depends(require_auth)],
) -> Response:
    """
    Forward one API call to a node and relay its answer.

    The central's own policy runs first, exactly as for a local call: its
    scope table (a mutation needs ``admin``), then sudo mode when the node
    marks the operation as elevated. The node then applies its own, with the
    operator's scope and the central's word on elevation.

    Args:
        node: The node's name.
        path: The node's API path after ``/api/``.
        request: The incoming request.
        session: The central's authenticated payload.

    Returns:
        The node's status, allowed headers and body, streamed.

    Raises:
        HTTPException: 403 for a path no central may reach or a missing sudo
            mode, 404 for an unknown node, 502 ``node_unreachable`` or
            ``node_refused`` when the node could not be used.
    """
    method = request.method.upper()
    target = node_path("/api/", path)
    _refuse_path(method, target)
    record, upstream = await _prepare(node, session)

    client = _new_client(upstream)
    sent = False
    try:
        schema = await node_schemas.get(record, client, upstream)
        if schema.requires_elevation(method, target):
            ensure_elevated(request, session)
        query = request.scope.get("query_string", b"").decode("latin-1")
        outgoing = client.build_request(
            method,
            _quoted(target) + (f"?{query}" if query else ""),
            headers=_forwarded_headers(request, upstream),
            content=None if method in SAFE_METHODS else request.stream(),
        )
        response = await client.send(outgoing, stream=True)
        sent = True
    except httpx.TransportError as exc:
        raise node_unreachable(record, exc) from exc
    finally:
        if not sent:
            await client.aclose()

    if response.status_code == 401:
        body = (await response.aread()).decode("utf-8", "replace")[:_MAX_ERROR_BODY]
        await _close(response, client)
        raise await refused_by(record, body)

    return StreamingResponse(
        _relay(response, record),
        status_code=response.status_code,
        headers=_returned_headers(response, record, upstream),
        background=BackgroundTask(_close, response, client),
    )


# ------------------------------------------------------------- the events


async def _relay_events(
    response: httpx.Response,
    client: httpx.AsyncClient,
    node: NodeRecord,
    session: dict[str, Any],
    release: Callable[[], None],
) -> AsyncIterator[bytes]:
    """
    Relay a node's event stream while the central's credential is good.

    Args:
        response: The node's streamed ``/events``.
        client: The stream's client.
        node: The node, for the log.
        session: The central's payload, re-checked as a local stream does.
        release: Returns the stream's lease on the tunnel, when it ends.

    Yields:
        The node's frames, unchanged.
    """
    loop = asyncio.get_running_loop()
    recheck_at = loop.time() + CREDENTIAL_RECHECK_SECONDS
    try:
        async for chunk in response.aiter_raw():
            yield chunk
            if shutting_down():
                return
            if loop.time() >= recheck_at:
                if not await run_in_threadpool(credential_is_current, session):
                    return
                recheck_at = loop.time() + CREDENTIAL_RECHECK_SECONDS
    except httpx.TransportError as exc:
        # A dead tunnel ends the stream; the console's EventSource reconnects,
        # and the reconnection opens a new tunnel or answers 502.
        logger.info("Event stream from node %s ended: %s", node.name, exc)
    finally:
        await _close(response, client)
        release()


@router.get("/{node}/events", include_in_schema=False)
async def proxy_events(
    node: str,
    request: Request,
    session: Annotated[dict[str, Any], Depends(require_auth)],
) -> Response:
    """
    Stream a node's ``/events`` to the console.

    Args:
        node: The node's name.
        request: The incoming request.
        session: The central's authenticated payload.

    Returns:
        The node's event stream, or the node's refusal in the API's contract.

    Raises:
        HTTPException: 404 for an unknown node, 502 when it cannot be used.
    """
    record, upstream = await _prepare(node, session, hold=True)
    client = _new_client(
        upstream, httpx.Timeout(connect=10.0, read=EVENTS_READ_TIMEOUT, write=10.0, pool=10.0)
    )
    headers = _forwarded_headers(request, upstream)
    headers["accept"] = "text/event-stream"
    try:
        response = await client.send(
            client.build_request("GET", "/events", headers=headers), stream=True
        )
    except httpx.TransportError as exc:
        await client.aclose()
        upstream.release()
        raise node_unreachable(record, exc) from exc

    if response.status_code != 200:
        body = (await response.aread()).decode("utf-8", "replace")[:_MAX_ERROR_BODY]
        await _close(response, client)
        upstream.release()
        if response.status_code == 401:
            raise await refused_by(record, body)
        return Response(
            content=body,
            status_code=response.status_code,
            headers=_returned_headers(response, record, upstream),
        )

    return StreamingResponse(
        _relay_events(response, client, record, session, upstream.release),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------- WebSockets


def relayable_close_code(code: int | None) -> int:
    """
    Map a close code received on one side to one that may be sent on the other.

    Args:
        code: The code received, or None when the connection ended without one.

    Returns:
        The same code when it may be sent; ``1000`` for "no status" (1005);
        ``1011`` for anything reserved for the transport (1006, 1015) or
        missing.
    """
    if code is None:
        return 1011
    if code == 1005:
        return 1000
    if 1000 <= code <= 1003 or 1007 <= code <= 1014 or 3000 <= code <= 4999:
        return code
    return 1011


def _close_code_for_status(status: int) -> int:
    """
    Map a node's refusal of a handshake to a close code for the browser.

    Args:
        status: The HTTP status the node answered the handshake with.

    Returns:
        The close code.
    """
    if status == 403:
        return WS_CLOSE_FORBIDDEN
    if status == 404:
        return WS_CLOSE_NODE_NOT_FOUND
    if status == 429:
        return 4429
    return WS_CLOSE_NODE_UNREACHABLE


def _websocket_url(base_url: str, target: str, query: str) -> str:
    """
    The node's WebSocket address for a path.

    Args:
        base_url: The node's HTTP base.
        target: The path on the node.
        query: The query string to forward.

    Returns:
        ``ws://`` (or ``wss://``) URL.
    """
    scheme, _, rest = base_url.partition("://")
    ws_scheme = "wss" if scheme == "https" else "ws"
    return f"{ws_scheme}://{rest}{_quoted(target)}" + (f"?{query}" if query else "")


class NodeSocket(Protocol):
    """
    The part of a websockets client connection the relay uses.

    Both the asyncio client (websockets 13 and later) and the legacy one
    (the 10.x Debian 12 and Ubuntu 24.04 ship) have it.
    """

    @property
    def close_code(self) -> int | None:
        """The code the node closed with, once closed."""

    @property
    def close_reason(self) -> str | None:
        """The reason the node closed with, once closed."""

    async def send(self, message: str | bytes) -> None:
        """Send one message to the node."""

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the connection to the node."""

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        """Iterate over the node's messages until it closes."""


async def connect_node_websocket(url: str, headers: dict[str, str]) -> NodeSocket:
    """
    Open a WebSocket to a node through its tunnel.

    Imported here rather than at the top of the module, so a server without
    ``websockets`` still serves its API and only this relay says what is
    missing.

    Args:
        url: The node's ``ws://`` address.
        headers: The fleet token and the actor headers.

    Returns:
        The open connection.

    Raises:
        OSError: When the tunnel refuses the connection.
        TimeoutError: When the handshake does not finish in time.
        websockets.exceptions.InvalidHandshake: When the node refuses the
            handshake - an HTTP status instead of an upgrade.
    """
    limits: dict[str, Any] = {
        "max_size": WS_MAX_MESSAGE_BYTES,
        "max_queue": WS_MAX_QUEUE,
        "open_timeout": WS_OPEN_TIMEOUT,
        "compression": None,
    }
    try:
        from websockets.asyncio.client import connect
    except ImportError:
        # websockets < 13: the legacy client, which reads no proxy settings.
        from websockets.legacy.client import connect as legacy_connect

        legacy = await legacy_connect(url, extra_headers=headers, **limits)
        return cast(NodeSocket, legacy)

    if "proxy" in inspect.signature(connect).parameters:
        # websockets >= 15 honours HTTPS_PROXY from the environment; a proxy
        # must never see a request, or a token, meant for a tunnel.
        limits["proxy"] = None
    connection = await connect(url, additional_headers=headers, **limits)
    return cast(NodeSocket, connection)


def refused_status(exc: Exception) -> int | None:
    """
    Read the HTTP status a node refused a handshake with.

    Args:
        exc: What the client raised: ``InvalidStatus`` (websockets 11 and
            later) carries a response, ``InvalidStatusCode`` (earlier) the
            status itself.

    Returns:
        The status, or None when the failure was not an HTTP answer.
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


async def _close_browser(websocket: WebSocket, code: int, reason: str = "") -> None:
    """
    Close the browser's side, accepting first so the code reaches it.

    Args:
        websocket: The browser's connection.
        code: The close code.
        reason: The reason, cut to what a close frame carries.
    """
    try:
        if websocket.client_state.name == "CONNECTING":
            await websocket.accept()
        await websocket.close(code=code, reason=reason.encode()[:120].decode("utf-8", "ignore"))
    except (RuntimeError, WebSocketDisconnect):
        pass  # The browser is already gone.


async def _pump(websocket: WebSocket, upstream: NodeSocket, session: dict[str, Any]) -> None:
    """
    Relay messages both ways until one side closes, then close the other.

    Args:
        websocket: The browser's connection, accepted.
        upstream: The node's connection, open.
        session: The central's payload, re-checked while the stream is open.
    """
    from websockets.exceptions import ConnectionClosed

    async def from_node() -> None:
        try:
            async for message in upstream:
                if isinstance(message, str):
                    await websocket.send_text(message)
                else:
                    await websocket.send_bytes(message)
        except ConnectionClosed:
            pass

    async def from_browser() -> int:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return int(message.get("code") or 1000)
            text = message.get("text")
            data = message.get("bytes")
            try:
                if text is not None:
                    await upstream.send(text)
                elif data is not None:
                    await upstream.send(data)
            except ConnectionClosed:
                return 1000

    async def watch_credential() -> None:
        while True:
            await asyncio.sleep(CREDENTIAL_RECHECK_SECONDS)
            if shutting_down() or not await run_in_threadpool(credential_is_current, session):
                return

    node_task = asyncio.create_task(from_node(), name="relay-from-node")
    browser_task = asyncio.create_task(from_browser(), name="relay-from-browser")
    watch_task = asyncio.create_task(watch_credential(), name="relay-watch-credential")
    tasks = (node_task, browser_task, watch_task)
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # return_exceptions=True is what lets cancelling the other two tasks
        # never raise past this block; it also means a real bug in one of
        # them - anything but the ConnectionClosed each already handles, or
        # the CancelledError cancelling its siblings causes - would vanish
        # with no trace at all unless logged here.
        for finished_task, result in zip(tasks, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                logger.warning(
                    "WebSocket relay task %s ended with an unexpected error",
                    finished_task.get_name(),
                    exc_info=result,
                )

    if browser_task.done() and not browser_task.cancelled() and browser_task.exception() is None:
        await upstream.close(code=relayable_close_code(browser_task.result()))
        return
    if watch_task.done() and not watch_task.cancelled():
        await upstream.close()
        await _close_browser(websocket, WS_CLOSE_UNAUTHORIZED, "Credential no longer valid")
        return
    await upstream.close()
    await _close_browser(
        websocket, relayable_close_code(upstream.close_code), upstream.close_reason or ""
    )


def _audit_stream(
    websocket: WebSocket, session: dict[str, Any], resource: str, result: str
) -> None:
    """
    Record a proxied WebSocket.

    Args:
        websocket: The browser's connection.
        session: The central's payload.
        resource: The central path.
        result: ``success`` or why not.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="ws.connect",
            result=result,
            client_ip=get_client_ip(websocket),
            actor=actor_label(session),
            resource=resource,
        )


@ws_router.websocket("/{node}/{path:path}")
async def proxy_websocket(websocket: WebSocket, node: str, path: str) -> None:
    """
    Relay a node's WebSocket (``/ws/{path}``) to the browser.

    The handshake was authenticated by the central's middleware, like every
    WebSocket here. The node's is authenticated with the fleet token in the
    ``Authorization`` header, which the node's handshake accepts alongside
    its cookie and its ``wasm.token.`` subprotocol; the browser's own
    subprotocol is answered here and never forwarded.

    Args:
        websocket: The browser's connection.
        node: The node's name.
        path: The node's WebSocket path after ``/ws/``.
    """
    resource = str(websocket.scope.get("path", ""))
    session = websocket.scope.get("state", {}).get("session")
    if not isinstance(session, dict):
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="Authentication required")
        return

    try:
        target = node_path("/ws/", path)
        _refuse_path("GET", target)
    except HTTPException as exc:
        detail: Any = exc.detail
        reason = detail.get("detail", "") if isinstance(detail, dict) else str(detail)
        await _close_browser(websocket, WS_CLOSE_FORBIDDEN, str(reason))
        return

    try:
        record, upstream = await _prepare(node, session, hold=True)
    except HTTPException as exc:
        await _close_browser(websocket, WS_CLOSE_NODE_NOT_FOUND, f"Node not found: {node}")
        logger.debug("WebSocket proxy refused: %s", exc.detail)
        return
    except NodeError as exc:
        code = (
            WS_CLOSE_FORBIDDEN if isinstance(exc, NodeRefusedError) else WS_CLOSE_NODE_UNREACHABLE
        )
        await _close_browser(websocket, code, exc.message)
        return
    except SealError as exc:
        # Locked (or a damaged seal): no node key can be read here.
        await _close_browser(websocket, WS_CLOSE_CENTRAL_LOCKED, exc.message)
        return
    try:
        await _relay_websocket(websocket, session, resource, record, upstream, target)
    finally:
        upstream.release()


async def _relay_websocket(
    websocket: WebSocket,
    session: dict[str, Any],
    resource: str,
    record: NodeRecord,
    upstream: Upstream,
    target: str,
) -> None:
    """
    Connect to the node's WebSocket and relay it, once the tunnel is leased.

    Args:
        websocket: The browser's connection, not yet accepted.
        session: The central's payload.
        resource: The central path, for the audit record.
        record: The node.
        upstream: Where the node answers, and its headers.
        target: The node's WebSocket path.
    """
    try:
        from websockets.exceptions import InvalidHandshake, InvalidURI
    except ImportError:
        await _close_browser(
            websocket, 1011, "This server cannot relay a node's streams: install websockets"
        )
        return

    url = _websocket_url(
        upstream.base_url, target, _query_without_ticket(websocket.scope.get("query_string", b""))
    )
    try:
        connection = await connect_node_websocket(url, upstream.headers)
    except InvalidHandshake as exc:
        status = refused_status(exc)
        if status is None:
            _audit_stream(websocket, session, resource, "error:unreachable")
            await _close_browser(
                websocket, WS_CLOSE_NODE_UNREACHABLE, f"Node {record.name} unreachable: {exc}"
            )
            return
        _audit_stream(websocket, session, resource, f"error:{status}")
        if status == 401:
            await refused_by(record, "")
        reason = f"Node {record.name} refused the stream (HTTP {status})"
        await _close_browser(websocket, _close_code_for_status(status), reason)
        return
    except (OSError, TimeoutError, InvalidURI) as exc:
        _audit_stream(websocket, session, resource, "error:unreachable")
        await _close_browser(
            websocket, WS_CLOSE_NODE_UNREACHABLE, f"Node {record.name} unreachable: {exc}"
        )
        return

    offered = websocket.headers.get("sec-websocket-protocol", "")
    await websocket.accept(subprotocol=WS_SUBPROTOCOL if WS_SUBPROTOCOL in offered else None)
    _audit_stream(websocket, session, resource, "success")
    try:
        await _pump(websocket, connection, session)
    finally:
        await connection.close()
