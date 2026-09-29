# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
HTTP to a node's API, through its tunnel, with the fleet token.

The central is a client of each node's API exactly as the console is a client
of the local one (rule 3): nothing here knows what an application is. This
module only knows how to reach a node, how to present the token, and how to
turn "could not reach it" and "it refused the token" into the two errors
every caller handles.

The token travels in the ``Authorization`` header and nowhere else: never in
a URL (which ends up in access logs), never in argv, never in a message.

A node that answers 401 to the token is recorded as ``refused`` and is not
presented the token again - by the console's polling, the proxy or ``noust
fleet status`` - until the operator asks for it explicitly with ``noust node
test`` (or re-adds the node). Every refused presentation is an audited
failure on the node, and a central that kept retrying a revoked token would
only fill the node's audit log.
"""

from __future__ import annotations

import re
from types import ModuleType
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import (
    FleetUnavailableError,
    NodeError,
    NodeRefusedError,
    NodeUnreachableError,
)
from noust.core.secrets import SecretStore
from noust.core.store import NoustStore, get_store
from noust.fleet.keys import TOKEN, secret_name
from noust.fleet.tunnels import TunnelManager, get_tunnels

if TYPE_CHECKING:
    import httpx


def load_httpx() -> ModuleType:
    """
    Import httpx, the one library the fleet needs beyond the core.

    Imported here rather than at the top of every fleet module so a server that
    never talks to a node starts its console, and runs its CLI, without it.

    Returns:
        The httpx module.

    Raises:
        FleetUnavailableError: httpx is not installed.
    """
    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise FleetUnavailableError(
            "The fleet needs the httpx library, which is not installed here",
            details=(
                "Install python3-httpx (python3xx-httpx on openSUSE) or "
                "pip install 'noust[web]', then restart noust-web."
            ),
        ) from exc
    return httpx


#: Who on the central is behind a request. The node honours it only on a
#: fleet token and records it in its audit log. Must match
#: ``noust.web.auth.FLEET_ACTOR_HEADER``.
ACTOR_HEADER = "X-Noust-Actor"

#: The scope that operator holds on the central; the node narrows the fleet
#: token to it. Must match ``noust.web.auth.FLEET_ACTOR_SCOPE_HEADER``.
ACTOR_SCOPE_HEADER = "X-Noust-Actor-Scope"

#: ``1`` when that operator is in sudo mode on the central (or is a credential
#: sudo mode does not ask). Must match ``noust.web.auth.FLEET_ELEVATED_HEADER``.
ELEVATED_HEADER = "X-Noust-Elevated"

#: What an actor label may look like; the node refuses anything else with 400.
#: Mirrors ``noust.web.auth.FLEET_ACTOR_PATTERN``.
ACTOR_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,63}")

#: Scopes an operator on the central can hold.
ACTOR_SCOPES = ("read", "deploy", "admin")

#: Most of a node's answer quoted back in an error.
MAX_QUOTED_BODY = 4000


def actor_label(text: str) -> str:
    """
    Reduce a free-form name to an actor label the node accepts.

    Args:
        text: Such as ``cli:root`` or a session label.

    Returns:
        The label, with anything outside the allowed characters as ``-``,
        at most 64 characters; ``unknown`` when nothing is left.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._:@+-]", "-", text)[:64].lstrip("._:@+-")
    return cleaned if ACTOR_PATTERN.fullmatch(cleaned) else "unknown"


def _quoted(response: httpx.Response) -> str:
    """
    Quote a node's answer for an error's details, verbatim but bounded.

    Args:
        response: The node's response.

    Returns:
        Its body as text, cut at :data:`MAX_QUOTED_BODY` characters.
    """
    http = load_httpx()
    try:
        text = response.text
    except (UnicodeDecodeError, http.ResponseNotRead):
        return ""
    return text[:MAX_QUOTED_BODY]


class NodeClient:
    """
    Talk to one node's API.

    Args:
        node: The node's name on this central.
        tunnels: The tunnel manager; the process-wide one by default.
        timeout: Seconds for each request.
        secrets: Where the node's token is; the process-wide store by default.
        transport: An httpx transport to use instead of the network (tests,
            and the development server's direct URL).
        store: Where the node's status is read and a refusal recorded; the
            process-wide store by default.
        retry_refused: Present the token even to a node recorded as
            ``refused``: what ``noust node test`` and registration do.
    """

    def __init__(
        self,
        node: str,
        *,
        tunnels: TunnelManager | None = None,
        timeout: float = 30.0,
        secrets: SecretStore | None = None,
        transport: httpx.BaseTransport | None = None,
        store: NoustStore | None = None,
        retry_refused: bool = False,
    ) -> None:
        self.node = node
        self._tunnels = tunnels
        self.timeout = timeout
        self._secrets = secrets or SecretStore()
        self._transport = transport
        self._store = store
        self.retry_refused = retry_refused

    def __repr__(self) -> str:
        """Name the client without anything secret."""
        return f"NodeClient(node={self.node!r})"

    @property
    def tunnels(self) -> TunnelManager:
        """The tunnel manager."""
        return self._tunnels or get_tunnels()

    @property
    def store(self) -> NoustStore:
        """The store the node's status is kept in."""
        return self._store or get_store()

    def _refuse_if_refused(self) -> None:
        """
        Stop before presenting a token the node already refused.

        Raises:
            NodeRefusedError: When the node is recorded as ``refused`` and this
                client was not asked to retry; ``status_code`` is 401, what
                the node answered.
        """
        if self.retry_refused:
            return
        record = self.store.get_node(self.node)
        if record is None or record.status != "refused":
            return
        refused = NodeRefusedError(
            f"{self.node} refused this central's fleet token; the central stopped asking it",
            details=(
                "Once the node accepts this central again (noust fleet authorize there, "
                f"or a new join code), run 'noust node test {self.node}' to resume, or "
                f"re-add it: noust node remove {self.node}, then noust node add."
            ),
        )
        refused.status_code = 401
        raise refused

    def mark_refused(self) -> None:
        """Record that the node refused the token, so it is not asked again."""
        self.store.set_node_status(self.node, "refused")

    def base_url(self) -> str:
        """
        Return where the node's API answers on this machine, opening the tunnel if needed.

        Returns:
            ``http://127.0.0.1:<port>``.

        Raises:
            NodeError: When the node is not registered.
            NodeUnreachableError: When the tunnel cannot be opened.
        """
        host, port = self.tunnels.endpoint(self.node)
        return f"http://{host}:{port}"

    def _token(self) -> str:
        """
        Read the node's fleet token.

        Returns:
            The token.

        Raises:
            NodeError: When none is stored for this node.
        """
        token = self._secrets.read(secret_name(self.node, TOKEN))
        if not token or not token.strip():
            raise NodeError(
                f"No fleet token is stored for node {self.node}",
                details=f"Remove the node and add it again: noust node remove {self.node}",
            )
        return token.strip()

    def auth_headers(
        self,
        actor: str | None = None,
        *,
        actor_scope: str | None = None,
        elevated: bool = False,
    ) -> dict[str, str]:
        """
        Build the headers every request to the node carries.

        Args:
            actor: Who on the central acts, such as ``cli:root``; omitted when None.
            actor_scope: That operator's scope on the central; omitted when
                None, which the node reads as ``read`` (fails closed), never
                ``admin``. A caller with no human actor to narrow to (a
                status poll) should still pass ``read`` explicitly, and one
                acting with this central's own full authority (the CLI,
                which already runs as local root) passes ``admin``.
            elevated: Whether that operator is confirmed in sudo mode.

        Returns:
            ``Authorization`` with the fleet token, and the actor headers.

        Raises:
            NodeError: When no token is stored, or the actor or scope is malformed.
            NodeRefusedError: When the node already refused the token
                (:meth:`mark_refused`) and this client does not retry.
        """
        self._refuse_if_refused()
        headers = {"Authorization": f"Bearer {self._token()}"}
        if actor is not None:
            if not ACTOR_PATTERN.fullmatch(actor):
                raise NodeError(
                    f"Invalid actor label: {actor!r}",
                    details="Build it with noust.fleet.client.actor_label().",
                )
            headers[ACTOR_HEADER] = actor
        if actor_scope is not None:
            if actor_scope not in ACTOR_SCOPES:
                raise NodeError(f"Invalid actor scope: {actor_scope!r}")
            headers[ACTOR_SCOPE_HEADER] = actor_scope
        if elevated:
            headers[ELEVATED_HEADER] = "1"
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        actor: str | None = None,
        actor_scope: str | None = None,
        elevated: bool = False,
    ) -> httpx.Response:
        """
        Send one request to the node's API and return its answer.

        Args:
            method: HTTP method.
            path: Absolute path on the node, such as ``/api/apps``.
            json: Body, sent as JSON.
            params: Query parameters.
            actor: Who on the central acts.
            actor_scope: That operator's scope on the central.
            elevated: Whether that operator is in sudo mode on the central.

        Returns:
            The response, whatever its status, except 401 and 403.

        Raises:
            NodeError: When the path is not a path on the node.
            NodeUnreachableError: When the tunnel or the connection fails.
            NodeRefusedError: When the node answers 401 or 403 to the token (a
                401 is recorded, and the node is not asked again until
                ``noust node test``), or already refused it.
        """
        if not path.startswith("/") or path.startswith("//") or any(c in path for c in "\r\n"):
            raise NodeError(f"Not a path on the node: {path!r}")
        headers = self.auth_headers(actor, actor_scope=actor_scope, elevated=elevated)
        url = self.base_url() + path
        http = load_httpx()
        try:
            # trust_env=False: an HTTP(S)_PROXY in the central's environment
            # must never see a request, or a token, meant for a tunnel.
            with http.Client(
                transport=self._transport,
                timeout=self.timeout,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response: httpx.Response = client.request(
                    method, url, json=json, params=params, headers=headers
                )
        except http.TransportError as exc:
            tunnel = self.tunnels.status(self.node).get("last_error") or ""
            raise NodeUnreachableError(
                f"{self.node} did not answer through its tunnel ({type(exc).__name__})",
                details="\n".join(part for part in (str(exc), tunnel) if part),
            ) from exc
        if response.status_code in (401, 403):
            if response.status_code == 401:
                # 401 is about the token itself; a 403 is about one request.
                self.mark_refused()
            refused = NodeRefusedError(
                f"{self.node} refused the fleet token (HTTP {response.status_code})",
                details=(
                    "If the token was revoked on the node, authorize this central there "
                    f"again and re-add it: noust node remove {self.node}, then noust node "
                    f"add.\n\n{_quoted(response)}"
                ).rstrip(),
                output=_quoted(response),
            )
            refused.status_code = response.status_code
            raise refused
        return response

    def get_json(self, path: str, **kw: Any) -> Any:
        """
        GET a path and return the JSON the node answered.

        Args:
            path: Absolute path on the node.
            **kw: Passed to :meth:`request`.

        Returns:
            The decoded body.

        Raises:
            NodeError: When the node answers an error status (its body is in
                the details, verbatim) or something that is not JSON.
            NodeUnreachableError: As :meth:`request`.
            NodeRefusedError: As :meth:`request`.
        """
        response = self.request("GET", path, **kw)
        if response.status_code >= 400:
            raise NodeError(
                f"{self.node} answered {response.status_code} to GET {path}",
                details=_quoted(response),
                output=_quoted(response),
            )
        try:
            return response.json()
        except ValueError as exc:
            raise NodeError(
                f"{self.node} answered GET {path} with something that is not JSON",
                details=_quoted(response),
            ) from exc
