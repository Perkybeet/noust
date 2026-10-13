# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The MCP endpoint, ``POST /api/mcp`` (J-19, J-20, J-21).

One URL speaks the Model Context Protocol over Streamable HTTP, so an MCP client
(an editor, a command-line tool, a script) is given Noust's tools with the address of the
console and an API token. Everything about what it may do is the token's: this
module authenticates nothing of its own, reads the caller out of the payload
``require_auth`` already verified, and hands each tool call to
:class:`~noust.web.mcp.server.McpServer`, which calls the API back as that
caller.

Refused before any of that:

- **Off.** MCP is off until a security officer turns it on (``mcp.enabled``): an
  MCP endpoint is a new way into a server that runs systemd as root, and a
  server that never asked for one does not grow one by upgrading.
- **Not a token.** A browser session or the master token is not a client's
  credential: cookies would let a web page act as the signed-in person, and the
  master token is break-glass. A named API token, bound to a team if need be, is.
- **Not this site.** A request that carries an ``Origin`` different from the host
  it was sent to is a web page trying a DNS-rebinding attack on a server on a
  private network, which the protocol's own specification asks servers to refuse.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from noust.core.audit import record as audit_record
from noust.core.config import Config
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import bearer_token, get_client_ip, is_secure_request
from noust.web.mcp.prompts import PROMPTS, RESOURCES, TEMPLATES
from noust.web.mcp.server import (
    INVALID_REQUEST,
    PARSE_ERROR,
    SUPPORTED_VERSIONS,
    Caller,
    McpServer,
    load_settings,
)
from noust.web.mcp.tools import GROUPS, TOOLS
from noust.web.permissions.principal import actor_of
from noust.web.permissions.registry import permission_for_route

router = APIRouter(route_class=NoustErrorRoute)

#: Largest request accepted, and most messages in a batch.
MAX_BODY = 1024 * 1024
MAX_BATCH = 20

#: The headers every answer carries: a client's answers are never cached.
_NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _refuse(status: int, error: str, detail: str, hint: str) -> JSONResponse:
    """
    Args:
        status: The HTTP status.
        error: The machine-readable code.
        detail: What was refused.
        hint: What to do.

    Returns:
        The API's error contract, as a response.
    """
    return JSONResponse(
        status_code=status,
        content={"error": error, "detail": detail, "hint": hint, "fields": None},
        headers=_NO_STORE,
    )


def _same_site(request: Request) -> bool:
    """
    Args:
        request: The request.

    Returns:
        False when it carries an ``Origin`` that is not the host it was sent to.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = (request.headers.get("host") or "").lower()
    return urllib.parse.urlsplit(origin).netloc.lower() == host


@router.post("", response_model=None)
async def mcp_post(
    request: Request, session: Annotated[dict, Depends(get_current_session)]
) -> Response:
    """
    Answer one MCP message, or a batch of them.

    Args:
        request: The request, whose body is JSON-RPC.
        session: The authenticated payload of the caller's token.

    Returns:
        The JSON-RPC response (an array for a batch); 202 with no body when the
        message is a notification.
    """
    settings = load_settings()
    if not settings.enabled:
        return _refuse(
            404,
            "mcp_disabled",
            "MCP is turned off on this server",
            "A security officer turns it on: 'noust mcp enable', or Settings > MCP in the console.",
        )
    if session.get("type") != "api_token" or session.get("fleet"):
        return _refuse(
            403,
            "mcp_token_required",
            "MCP is for API tokens, not for a signed-in session or the master token",
            "Create a token for the client ('noust token create --scope deploy') and send it as "
            "'Authorization: Bearer ...'.",
        )
    if not _same_site(request):
        return _refuse(
            403,
            "mcp_origin_refused",
            "The request came from another site",
            "Call it from a client.",
        )
    version = request.headers.get("mcp-protocol-version")
    if version is not None and version not in SUPPORTED_VERSIONS:
        return _refuse(
            400,
            "mcp_version",
            f"Unsupported MCP protocol version: {version}",
            f"This server speaks {', '.join(SUPPORTED_VERSIONS)}.",
        )
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return _refuse(413, "payload_too_large", "The message is too large", "Send less.")
    try:
        decoded = json.loads(raw)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": PARSE_ERROR, "message": "Not JSON"},
            },
            headers=_NO_STORE,
        )
    token = bearer_token(request)
    if token is None:
        return _refuse(401, "unauthorized", "No bearer token", "Send 'Authorization: Bearer ...'.")
    caller = Caller(
        app=request.app,
        session=session,
        token=token,
        client_host=get_client_ip(request),
        host=request.headers.get("host") or "localhost",
        scheme="https" if is_secure_request(request) else "http",
        version=version or SUPPORTED_VERSIONS[0],
    )
    server = McpServer(settings)
    if isinstance(decoded, list):
        if not decoded or len(decoded) > MAX_BATCH:
            return JSONResponse(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": INVALID_REQUEST,
                        "message": f"A batch holds 1 to {MAX_BATCH} messages",
                    },
                },
                headers=_NO_STORE,
            )
        answers = [a for a in [await server.handle(message, caller) for message in decoded] if a]
        if not answers:
            return Response(status_code=202, headers=_NO_STORE)
        return JSONResponse(content=answers, headers=_NO_STORE)
    answer = await server.handle(decoded, caller)
    if answer is None:
        return Response(status_code=202, headers=_NO_STORE)
    return JSONResponse(content=answer, headers=_NO_STORE)


@router.get("", response_model=None)
async def mcp_get(session: Annotated[dict, Depends(get_current_session)]) -> Response:
    """
    Say that this server opens no stream of its own.

    Args:
        session: The authenticated payload.

    Returns:
        405: the protocol lets a server answer so when it never speaks first.
    """
    return JSONResponse(
        status_code=405,
        content={
            "error": "method_not_allowed",
            "detail": "This server does not open a stream; send messages with POST",
            "hint": None,
            "fields": None,
        },
        headers={**_NO_STORE, "Allow": "POST"},
    )


@router.delete("", response_model=None)
async def mcp_delete(session: Annotated[dict, Depends(get_current_session)]) -> Response:
    """
    Say that there is no session to end: the server remembers nothing between calls.

    Args:
        session: The authenticated payload.

    Returns:
        405.
    """
    return JSONResponse(
        status_code=405,
        content={
            "error": "method_not_allowed",
            "detail": "There is no session to end: this server keeps none",
            "hint": None,
            "fields": None,
        },
        headers={**_NO_STORE, "Allow": "POST"},
    )


class ToolInfo(BaseModel):
    """
    One tool, as the console describes it.

    Attributes:
        name: What the model calls it.
        title: A short title.
        group: ``inspect``, ``deploy`` or ``control``.
        permission: The permission its route needs.
        read_only: It changes nothing.
        destructive: It needs ``confirm: true``.
        description: What it does.
    """

    name: str
    title: str
    group: str
    permission: str
    read_only: bool
    destructive: bool
    description: str


class McpStatus(BaseModel):
    """
    Whether MCP is on, where, and what it offers.

    Attributes:
        enabled: Whether the endpoint answers.
        groups: The tool groups turned on.
        all_groups: Every group there is.
        endpoint: The address a client connects to.
        protocol_versions: The revisions this server speaks.
        tools: Every tool there is, whatever the groups (a token sees only the ones
            its permissions and the groups allow).
        prompts: The prompts offered.
        resources: The resources and templates offered.
    """

    enabled: bool
    groups: list[str]
    all_groups: list[str]
    endpoint: str
    protocol_versions: list[str]
    tools: list[ToolInfo]
    prompts: list[str]
    resources: list[str]


class McpSettings(BaseModel):
    """Request to turn MCP on or off, or change the groups on offer."""

    enabled: bool | None = Field(default=None, description="Whether the endpoint answers")
    groups: list[str] | None = Field(
        default=None, description="Which of inspect, deploy and control are offered"
    )


def _status(request: Request) -> McpStatus:
    """
    Args:
        request: The request, for the address the console is reached at.

    Returns:
        Where MCP stands.
    """
    settings = load_settings()
    scheme = "https" if is_secure_request(request) else "http"
    host = request.headers.get("host") or "localhost"
    configured = str(Config().get("web.public_url") or "").strip().rstrip("/")
    base = configured or f"{scheme}://{host}"
    return McpStatus(
        enabled=settings.enabled,
        groups=[g for g in GROUPS if g in settings.groups],
        all_groups=list(GROUPS),
        endpoint=f"{base}/api/mcp",
        protocol_versions=list(SUPPORTED_VERSIONS),
        tools=[
            ToolInfo(
                name=tool.name,
                title=tool.title,
                group=tool.group,
                permission=permission_for_route(tool.method, tool.path) or "",
                read_only=tool.read_only,
                destructive=tool.destructive,
                description=tool.description,
            )
            for tool in TOOLS
        ],
        prompts=sorted(PROMPTS),
        resources=[r["uri"] for r in RESOURCES] + [t["uriTemplate"] for t in TEMPLATES],
    )


@router.get("/status", response_model=McpStatus)
def mcp_status(
    request: Request, session: Annotated[dict, Depends(get_current_session)]
) -> McpStatus:
    """
    Say whether MCP is on, where to connect and what it offers.

    Args:
        request: The request.
        session: The authenticated session.

    Returns:
        The status.
    """
    return _status(request)


@router.put("/settings", response_model=McpStatus)
def mcp_settings(
    request: Request,
    body: McpSettings,
    session: Annotated[dict, Depends(require_elevated)],
) -> McpStatus:
    """
    Turn MCP on or off, or choose which groups of tools are offered, in sudo mode.

    Args:
        request: The request.
        body: What to change; what is left out stays.
        session: The authenticated, elevated session.

    Returns:
        The status as it is now.

    Raises:
        ConfigError: When a group is not one of the three.
    """
    config = Config()
    changed: dict[str, Any] = {}
    if body.groups is not None:
        config.set("mcp.groups", body.groups)
        changed["groups"] = body.groups
    if body.enabled is not None:
        config.set("mcp.enabled", body.enabled)
        changed["enabled"] = body.enabled
    if changed:
        audit_record("mcp.settings", actor=actor_of(session), target="setting:mcp", details=changed)
    return _status(request)


__all__ = ["router"]
