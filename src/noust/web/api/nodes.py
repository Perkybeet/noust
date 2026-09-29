# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central's registry of nodes, over HTTP.

A thin layer over :class:`noust.fleet.nodes.NodeManager` (rule 3): listing,
registering with a join code, removing, testing, and the key a node has to
authorize. Registering and removing are destructive for the fleet and ask for
sudo mode; the key is readable by an ``admin`` credential only, because the
command that comes with it is what enrolls a server.

The fleet modules are imported where they are used, not at the top: the
panel of a server that is only ever a node never opens a tunnel, and an
import error in the fleet must not take the whole API down with it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from noust.core.exceptions import NodeError
from noust.web.api.deps import NoustErrorRoute, require_elevated, require_scope
from noust.web.auth import actor_label, get_audit_logger, get_client_ip, require_auth
from noust.web.pydantic_compat import field_validator

if TYPE_CHECKING:
    from noust.core.store import NodeRecord
    from noust.fleet.client import NodeClient
    from noust.fleet.nodes import NodeManager
    from noust.fleet.tunnels import TunnelManager

router = APIRouter(route_class=NoustErrorRoute)

#: What a node name may look like in a URL. The manager validates the name it
#: stores; this only keeps a path segment from being anything but a name.
_NODE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def node_manager() -> NodeManager:
    """
    The fleet's node registry.

    Returns:
        A node manager.
    """
    from noust.fleet.nodes import NodeManager

    return NodeManager()


def tunnels() -> TunnelManager:
    """
    The process-wide tunnel manager.

    Returns:
        The tunnel manager.
    """
    from noust.fleet.tunnels import get_tunnels

    return get_tunnels()


def node_client(node: NodeRecord) -> NodeClient:
    """
    An HTTP client for one node, through its tunnel.

    Args:
        node: The node.

    Returns:
        The client, built by the node manager so it reads the same secrets
        and tunnels the registry does.
    """
    return node_manager().client(node.name)


def find_node(name: str) -> NodeRecord:
    """
    Look a node up by name, or answer 404.

    Args:
        name: The node's name.

    Returns:
        The node.

    Raises:
        HTTPException: 404 when no node has that name, or the name could not
            be one.
    """
    if not _NODE_NAME.fullmatch(name):
        raise HTTPException(status_code=404, detail=f"Node not found: {name}")
    try:
        return node_manager().get(name)
    except NodeError as exc:
        # get() raises only for a name that is not registered or not valid.
        raise HTTPException(status_code=404, detail=exc.message) from exc


class NodeResponse(BaseModel):
    """
    One node, as the central knows it.

    Attributes:
        name: The node's name on this central.
        ssh_host: Host the central's tunnel connects to.
        ssh_port: SSH port on that host.
        ssh_user: Account whose ``authorized_keys`` holds the central's key.
        host_key: The pinned ``known_hosts`` line.
        console_port: Loopback port of the node's console.
        version: Noust version the node last reported.
        status: ``unknown``, ``reachable``, ``unreachable`` or ``refused``.
        last_seen: When the node last answered, ISO 8601 UTC.
        allow_shell: Whether the node allows commands over SSH.
        created_at: When the node was registered.
        tunnel: The tunnel's current state, as the tunnel manager reports it.
    """

    name: str
    ssh_host: str
    ssh_port: int
    ssh_user: str
    host_key: str
    console_port: int
    version: str | None = None
    status: str
    last_seen: str | None = None
    allow_shell: bool = False
    created_at: str | None = None
    tunnel: dict[str, Any] = Field(default_factory=dict)


class NodeListResponse(BaseModel):
    """
    Response for ``GET /api/nodes``.

    Attributes:
        items: Every node, as :class:`NodeResponse`.
    """

    items: list[NodeResponse]


class NodeAddRequest(BaseModel):
    """
    Body of ``POST /api/nodes``.

    Attributes:
        name: The name the node gets on this central.
        ssh_target: Where the node's SSH answers: ``host``, ``user@host`` or
            ``user@host:port``.
        join_code: The one-line code ``noust fleet authorize`` printed on the
            node.
    """

    name: str
    ssh_target: str = Field(min_length=1, max_length=255)
    join_code: str = Field(min_length=1, max_length=8192)

    @field_validator("name")
    @classmethod
    def _a_name(cls, value: str) -> str:
        """
        Refuse what could never be a node name, before the manager's own check.

        Args:
            value: The name as sent.

        Returns:
            The name.

        Raises:
            ValueError: When it is not 1 to 64 letters, digits, dots,
                underscores or hyphens, starting with a letter or digit.
        """
        if not _NODE_NAME.fullmatch(value):
            raise ValueError("A node name is letters, digits and hyphens, starting with one")
        return value


class NodeRemovedResponse(BaseModel):
    """
    Response for ``DELETE /api/nodes/{node}``.

    Attributes:
        name: The node that was removed.
        messages: What the removal did, and what it could not do - a token
            that could not be revoked because the node was unreachable.
    """

    name: str
    messages: list[str]


class NodeKeyResponse(BaseModel):
    """
    Response for ``GET /api/nodes/{node}/key``.

    Attributes:
        name: The node the key is for.
        public_key: The central's public key for that node.
        authorize_command: The command to run on the node, as root, which
            installs the key restricted to the tunnel and prints the join code.
    """

    name: str
    public_key: str
    authorize_command: str


def describe(node: NodeRecord) -> NodeResponse:
    """
    Render a node with its tunnel's state.

    Args:
        node: The node.

    Returns:
        The API representation.
    """
    fields = node.to_dict()
    fields.pop("updated_at", None)
    return NodeResponse(**fields, tunnel=dict(tunnels().status(node.name)))


def _audit(
    request: Request,
    session: dict[str, Any],
    action: str,
    node: str,
    detail: str | None = None,
) -> None:
    """
    Record a fleet action with its node.

    Args:
        request: The incoming request.
        session: The credential that acted.
        action: What was done, such as ``fleet.node.add``.
        node: The node it was done to.
        detail: Extra context. Never a credential.
    """
    audit = get_audit_logger()
    if audit is None:
        return
    audit.record(
        action=action,
        result="ok",
        client_ip=get_client_ip(request),
        actor=actor_label(session),
        resource=f"node:{node}",
        detail=detail,
    )


@router.get("", response_model=NodeListResponse)
def list_nodes(session: Annotated[dict[str, Any], Depends(require_auth)]) -> NodeListResponse:
    """
    List every node with its status and its tunnel's.

    Args:
        session: The authenticated session.

    Returns:
        The nodes.
    """
    return NodeListResponse(items=[describe(node) for node in node_manager().list()])


@router.post("", response_model=NodeResponse, status_code=201)
def add_node(
    body: NodeAddRequest,
    request: Request,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> NodeResponse:
    """
    Register a node with the join code it printed.

    Args:
        body: The name, the SSH address and the join code.
        request: The incoming request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        The registered node.

    Raises:
        NodeError: 400 with the policy's sentences when the registration is
            refused; 502 ``node_unreachable`` with ssh's stderr when the node
            cannot be reached; 502 ``node_refused`` when it refused the token.
    """
    node = node_manager().add(body.name, ssh_target=body.ssh_target, join_code=body.join_code)
    _audit(request, session, "fleet.node.add", node.name, detail=f"ssh {body.ssh_target}")
    return describe(node)


@router.get("/{node}", response_model=NodeResponse)
def get_node(node: str, session: Annotated[dict[str, Any], Depends(require_auth)]) -> NodeResponse:
    """
    Show one node.

    Args:
        node: The node's name.
        session: The authenticated session.

    Returns:
        The node.
    """
    return describe(find_node(node))


@router.delete("/{node}", response_model=NodeRemovedResponse)
def remove_node(
    node: str,
    request: Request,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
    revoke: Annotated[
        bool, Query(description="Revoke the fleet token on the node first, when it answers")
    ] = True,
) -> NodeRemovedResponse:
    """
    Remove a node from this central, closing its tunnel.

    Args:
        node: The node's name.
        request: The incoming request, for the audit record.
        session: The authenticated, elevated session.
        revoke: Whether to revoke the fleet token on the node first.

    Returns:
        What the removal did.
    """
    record = find_node(node)
    messages = node_manager().remove(record.name, revoke=revoke)
    _audit(request, session, "fleet.node.remove", record.name, detail=f"revoke={revoke}")
    return NodeRemovedResponse(name=record.name, messages=list(messages))


@router.post("/{node}/test", response_model=dict[str, Any])
def test_node(
    node: str,
    request: Request,
    session: Annotated[dict[str, Any], Depends(require_auth)],
) -> dict[str, Any]:
    """
    Open the node's tunnel and ask its API who it is.

    Args:
        node: The node's name.
        request: The incoming request, for the audit record.
        session: The authenticated session; a POST, so ``admin`` scope.

    Returns:
        What the test found, as the node manager reports it.
    """
    record = find_node(node)
    result = dict(node_manager().test(record.name))
    _audit(request, session, "fleet.node.test", record.name)
    return result


@router.get("/{node}/key", response_model=NodeKeyResponse)
def get_node_key(
    node: str,
    request: Request,
    session: Annotated[dict[str, Any], Depends(require_scope("admin"))],
) -> NodeKeyResponse:
    """
    Show the key a node has to authorize, and the command that does it.

    Works for a name that is not registered yet: this is the first step of
    adding a node, before it has a join code to register with.

    Args:
        node: The node's name.
        request: The incoming request, for the audit record.
        session: The authenticated session, of ``admin`` scope.

    Returns:
        The public key and the command.
    """
    manager = node_manager()
    public_key = manager.central_public_key(node)
    command = manager.authorize_command(node)
    _audit(request, session, "fleet.node.key", node)
    return NodeKeyResponse(name=node, public_key=public_key, authorize_command=command)
