# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Servers that dial in to this central, over HTTP: ``/api/fleet/link``.

A thin layer over :class:`noust.fleet.link.central.LinkManager` and
:class:`noust.fleet.nodes.NodeManager` (rule 3). A server with no SSH port a
central can reach (behind NAT, behind a carrier's CGNAT) is added in three
steps, the first and last here and the middle on the server:

1. ``POST /api/fleet/link/invites`` gives the server a loopback port on this
   machine that only it will listen on, and the command to run on it;
2. on the server, ``noust fleet link ...`` keeps a connection to this central
   open and prints a link code;
3. ``POST /api/fleet/link/accept`` takes the code, installs the server's key
   (it may listen on its one port and nothing else) and registers it.

Preparing the central itself (the account servers dial in as, and its sshd
block) is ``noust node link setup``, a command: it rewrites sshd's configuration,
which the console does not do. ``GET /api/fleet/link`` says whether that has
been done and gives the command when it has not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.web.api.deps import NoustErrorRoute, require_elevated, require_scope
from noust.web.api.nodes import NodeResponse, describe, node_manager
from noust.web.auth import actor_label

if TYPE_CHECKING:
    from noust.fleet.link.central import LinkManager

router = APIRouter(route_class=NoustErrorRoute)


class LinkEntryOut(BaseModel):
    """
    One server that dials in, or was invited to.

    Attributes:
        node: The server's name.
        port: The loopback port its link listens on, on this machine.
        state: ``invited`` or ``active``.
        central_host: The address it was told to dial.
        central_port: The SSH port it was told to dial.
        created_at: When it was invited.
        created_by: Who invited it.
        activated_at: When its code was accepted.
    """

    node: str
    port: int
    state: str
    central_host: str
    central_port: int
    created_at: str
    created_by: str | None = None
    activated_at: str | None = None


class LinkStatusOut(BaseModel):
    """
    Whether this central can take servers that dial in.

    Attributes:
        ready: A server can be invited now.
        problems: What stops it, one actionable sentence each.
        account: The account servers dial in as exists.
        ssh_port: This central's SSH port.
        host_key_fingerprint: Its SSH host key, which a server pins.
        links: Every server that dials in or was invited.
        setup_command: The command that prepares this central.
    """

    ready: bool
    problems: list[str] = Field(default_factory=list)
    account: bool = False
    ssh_port: int = 22
    host_key_fingerprint: str | None = None
    links: list[LinkEntryOut] = Field(default_factory=list)
    setup_command: str


class InviteBody(BaseModel):
    """
    Body of ``POST /api/fleet/link/invites``.

    Attributes:
        name: The name the server gets on this central.
        host: The address the server dials: a name or an IP address.
        ssh_port: The SSH port it dials; this central's sshd's by default.
        port: The loopback port its link listens on, here; one is chosen by default.
    """

    name: str = Field(min_length=1, max_length=32)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    port: int | None = Field(default=None, ge=1024, le=65535)


class InviteOut(BaseModel):
    """
    What to do on the server.

    Attributes:
        node: The server's name.
        central: This central's name, as the server will call it.
        host: The address it dials.
        ssh_port: The SSH port it dials.
        port: The loopback port its link listens on, here.
        host_key_fingerprint: This central's SSH host key, to compare by eye.
        expires_at: When the invitation stops being answerable.
        command: The command to run on the server, as root.
    """

    node: str
    central: str
    host: str
    ssh_port: int
    port: int
    host_key_fingerprint: str
    expires_at: str
    command: str


class AcceptBody(BaseModel):
    """
    Body of ``POST /api/fleet/link/accept``.

    Attributes:
        name: The server's name, as it was invited.
        link_code: The one-line code ``noust fleet link`` printed on the server.
        wait_seconds: How long the server may take to connect once its key is
            installed; the console asks for the default.
    """

    name: str = Field(min_length=1, max_length=32)
    link_code: str = Field(min_length=1, max_length=8192)
    wait_seconds: int = Field(default=30, ge=0, le=120)


def _links() -> LinkManager:
    """
    Build the link manager.

    Returns:
        A :class:`~noust.fleet.link.central.LinkManager`.
    """
    from noust.fleet.link.central import LinkManager

    return LinkManager()


@router.get("", response_model=LinkStatusOut)
def link_status(
    session: Annotated[dict[str, Any], Depends(require_scope("admin"))],
) -> dict[str, Any]:
    """
    Say whether this central can take servers that dial in, and which do.

    Args:
        session: The authenticated session, of ``admin`` scope.

    Returns:
        The status; ``problems`` says what to run when it is not ready.
    """
    return _links().status().to_dict()


@router.post("/invites", response_model=InviteOut, status_code=201)
def invite_server(
    body: InviteBody, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> dict[str, Any]:
    """
    Invite a server to dial in: give it a port and the command to run there.

    Args:
        body: The name, the address to dial and optionally the ports.
        session: The authenticated session in sudo mode.

    Returns:
        The invitation, with the command to run on the server.

    Raises:
        NodeError: 400 when this central is not ready (the sentences are the
            details), the name is registered, the host or a port is invalid, or
            no port is free.
    """
    invite = _links().invite(
        body.name,
        host=body.host,
        ssh_port=body.ssh_port,
        port=body.port,
        actor=actor_label(session),
    )
    return invite.to_dict()


@router.delete("/invites/{name}", status_code=204)
def cancel_invite(name: str, session: Annotated[dict[str, Any], Depends(require_elevated)]) -> None:
    """
    Cancel an invitation nobody answered.

    Args:
        name: The server's name.
        session: The authenticated session in sudo mode.

    Raises:
        NodeError: 400 when there is no invitation, or the server already dials in.
    """
    _links().cancel(name, actor=actor_label(session))


@router.post("/accept", response_model=NodeResponse, status_code=201)
def accept_server(
    body: AcceptBody, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> NodeResponse:
    """
    Register a server that dials in, from the link code it printed.

    Waits for the server's link to come up and for it to answer with its token;
    nothing is kept unless it does.

    Args:
        body: The name and the link code.
        session: The authenticated session in sudo mode.

    Returns:
        The registered node.

    Raises:
        NodeError: 400 with the policy's sentences when the registration is
            refused, or when the code is not the invitation's; 502
            ``node_unreachable`` when the server does not connect in time;
            502 ``node_refused`` when it refused the token.
    """
    node = node_manager().add_linked(
        body.name, link_code=body.link_code, wait=float(body.wait_seconds)
    )
    return describe(node)
