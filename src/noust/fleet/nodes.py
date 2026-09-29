# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central's node registry: the one place nodes are added, tested and removed.

``noust node ...`` and ``/api/nodes`` are both clients of :class:`NodeManager`,
so every rule about registering a node - two-factor sign-in first, a join
code for the key this central actually holds, a host key pinned from that
code, a token that works before anything is kept - is enforced once, here.

Enrollment, from the central's side:

1. ``noust node key NAME`` (:meth:`NodeManager.central_public_key` and
   :meth:`NodeManager.authorize_command`) generates the key pair for NAME and
   prints the command to run on the node.
2. The operator runs it on the node, which prints a join code.
3. ``noust node add NAME --ssh USER@HOST --join-code CODE``
   (:meth:`NodeManager.add`) checks the code, pins the host key, stores the
   token, opens the tunnel and asks the node for its version with the token.
   Any failure undoes everything ``add`` wrote.
"""

from __future__ import annotations

import builtins
import getpass
import shlex
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import NodeError, NodeRefusedError
from noust.core.runner import CommandRunner
from noust.core.secrets import SecretStore
from noust.core.store import NodeRecord, NoustStore, get_store
from noust.fleet.client import NodeClient, actor_label
from noust.fleet.joincode import JoinCode

if TYPE_CHECKING:
    import httpx
from noust.fleet.keys import KNOWN_HOSTS, TOKEN, NodeKeys, known_hosts_line, secret_name
from noust.fleet.models import central_name, parse_ssh_target, validate_node_name
from noust.fleet.policy import node_registration_blockers
from noust.fleet.tunnels import TunnelManager, get_tunnels

#: What a node answers to prove the token works, and says its version with.
VERSION_PATH = "/api/system/version"

#: Where a fleet token revokes itself on its node. Allowed to a fleet token
#: (the only credential endpoint that is); it only ever revokes the token
#: that calls it.
FLEET_REVOKE_PATH = "/api/auth/fleet/revoke"


def cli_actor() -> str:
    """
    Name the operator of a CLI command, as the node's audit log will show it.

    Returns:
        ``cli:<user>@<central>``, reduced to an actor label.
    """
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "unknown"
    return actor_label(f"cli:{user}@{central_name()}")


class NodeManager:
    """
    Add, list, test and remove the nodes this central manages.

    Args:
        store: The store; the process-wide one by default.
        secrets: The secret store; the process-wide one by default.
        runner: The runner ssh-keygen and ssh go through.
        tunnels: The tunnel manager; the process-wide one by default.
        blockers: The registration policy; :func:`node_registration_blockers`.
        transport: An httpx transport for :class:`NodeClient` (tests).
    """

    def __init__(
        self,
        *,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        runner: CommandRunner | None = None,
        tunnels: TunnelManager | None = None,
        blockers: Callable[[], builtins.list[str]] = node_registration_blockers,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets or SecretStore()
        self._keys = NodeKeys(self._secrets, runner)
        self._tunnels = tunnels
        self._blockers = blockers
        self._transport = transport

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def keys(self) -> NodeKeys:
        """The per-node SSH material."""
        return self._keys

    @property
    def tunnels(self) -> TunnelManager:
        """The tunnel manager."""
        return self._tunnels or get_tunnels()

    def client(
        self, name: str, *, timeout: float = 30.0, retry_refused: bool = False
    ) -> NodeClient:
        """
        Build a client for one node's API.

        Args:
            name: The node's name.
            timeout: Seconds per request.
            retry_refused: Present the token even if the node refused it last
                time; only an explicit test or a registration does.

        Returns:
            The client.
        """
        return NodeClient(
            name,
            tunnels=self.tunnels,
            timeout=timeout,
            secrets=self._secrets,
            transport=self._transport,
            store=self.store,
            retry_refused=retry_refused,
        )

    # builtins.list below: inside this class, 'list' is this method.
    def list(self) -> builtins.list[NodeRecord]:
        """
        List the registered nodes, by name.

        Returns:
            The nodes.
        """
        return self.store.list_nodes()

    def get(self, name: str) -> NodeRecord:
        """
        Read one node.

        Args:
            name: The node's name.

        Returns:
            The node.

        Raises:
            NodeError: When no node has that name.
        """
        record = self.store.get_node(validate_node_name(name))
        if record is None:
            raise NodeError(
                f"No node named {name} is registered on this central",
                details="List them with 'noust node list'.",
            )
        return record

    def central_public_key(self, name: str) -> str:
        """
        Return this central's public key for a node, generating the pair once.

        Args:
            name: The node's name (it need not be registered yet).

        Returns:
            ``ssh-ed25519 AAAA... noust-central@<central>``.

        Raises:
            NodeError: When the name is invalid or ssh-keygen fails.
        """
        return self._keys.ensure_keypair(validate_node_name(name), central_name()).line()

    def authorize_command(self, name: str) -> str:
        """
        Spell the command the operator runs on the node to authorize this central.

        Args:
            name: The node's name.

        Returns:
            ``noust fleet authorize --central-key '...' --name <central>``.

        Raises:
            NodeError: As :meth:`central_public_key`.
        """
        key = self.central_public_key(name)
        return f"noust fleet authorize --central-key {shlex.quote(key)} --name {central_name()}"

    def add(
        self, name: str, *, ssh_target: str, join_code: str, actor: str | None = None
    ) -> NodeRecord:
        """
        Register a node from the join code it printed.

        Args:
            name: The node's name on this central.
            ssh_target: ``[USER@]HOST[:PORT]``; the user and port default to
                what the join code says.
            join_code: The line ``noust fleet authorize`` printed on the node.
            actor: Who registers it, for the node's audit log (an actor label);
                :func:`cli_actor` when None.

        Returns:
            The registered node, ``reachable``, with the version it reported.

        Raises:
            NodeError: When the central may not register nodes yet (the
                policy's sentences are the details), the name is taken or
                invalid, the address or the code is malformed, or the code is
                for another key. Nothing is written in any of these cases.
            NodeUnreachableError: When the tunnel does not open, including a
                host key that does not match the code. Everything written is
                undone.
            NodeRefusedError: When the node refuses the token. Undone too.
        """
        name = validate_node_name(name)
        if self.store.get_node(name) is not None:
            raise NodeError(
                f"A node named {name} is already registered",
                details=f"Pick another name, or remove it first: noust node remove {name}",
                field="name",
            )
        blockers = self._blockers()
        if blockers:
            raise NodeError("This central may not register nodes yet", details="\n".join(blockers))
        target = parse_ssh_target(ssh_target)
        code = JoinCode.decode(join_code)
        if target.user is not None and target.user != code.ssh_user:
            raise NodeError(
                f"The join code authorizes {code.ssh_user}, not {target.user}",
                details=(
                    f"The node installed this central's key for {code.ssh_user}. Use "
                    f"{code.ssh_user}@{target.host}, or authorize again on the node with "
                    f"--ssh-user {target.user}."
                ),
                field="ssh",
            )
        public = self._keys.public_key(name)
        if public is None:
            raise NodeError(
                f"This central holds no key for {name} yet",
                details=(
                    f"Run 'noust node key {name}', run the command it prints on the node, "
                    "and paste the join code that prints."
                ),
            )
        if public.fingerprint != code.central_key_fp:
            raise NodeError(
                f"This join code authorizes another key than the one this central holds for {name}",
                details=(
                    f"The code is for {code.central_key_fp}; this central's key for {name} is "
                    f"{public.fingerprint}. Run 'noust node key {name}' and authorize the "
                    "key it prints on the node."
                ),
                field="join_code",
            )

        record = NodeRecord(
            name=name,
            ssh_host=target.host,
            ssh_port=target.port or code.ssh_port,
            ssh_user=code.ssh_user,
            host_key=known_hosts_line(name, code.ssh_host_key),
            console_port=code.console_port,
            version=code.noust_version,
        )
        registered = False
        try:
            self._keys.pin_host_key(name, record.host_key)
            self._secrets.write(secret_name(name, TOKEN), code.token)
            self.store.save_node(record)
            # A version check is read-only, and this call carries no scope a
            # human actor granted: it asks for read, not the admin a missing
            # header used to grant by accident.
            info = self.client(name, retry_refused=True).get_json(
                VERSION_PATH, actor=actor or cli_actor(), actor_scope="read"
            )
            version = info.get("current_version") if isinstance(info, dict) else None
            self.store.set_node_status(
                name, "reachable", version=version if isinstance(version, str) else None
            )
            registered = True
        finally:
            if not registered:
                self._undo_add(name)
        return self.get(name)

    def _undo_add(self, name: str) -> None:
        """
        Remove what a failed :meth:`add` wrote; the key pair predates it and stays.

        Args:
            name: The node's name.
        """
        self.tunnels.close(name)
        self.store.delete_node(name)
        self._secrets.delete(secret_name(name, TOKEN))
        self._secrets.delete(secret_name(name, KNOWN_HOSTS))

    def remove(
        self, name: str, *, revoke: bool = True, actor: str | None = None
    ) -> builtins.list[str]:
        """
        Forget a node: revoke its token there, close its tunnel, delete its secrets and row.

        Args:
            name: The node's name.
            revoke: Ask the node to revoke the token first, when it is reachable.
            actor: Who removes it, for the node's audit log; :func:`cli_actor`
                when None.

        Returns:
            Warnings for the operator: what could not be done on the node,
            and what only the node's operator can do.

        Raises:
            NodeError: When no node has that name.
        """
        record = self.get(name)
        warnings: builtins.list[str] = []
        deauthorize = (
            f"On the node, 'noust fleet deauthorize --name {central_name()}' removes this "
            f"central's key from {record.ssh_user}'s authorized_keys and revokes any token left."
        )
        if revoke:
            warnings += self._revoke(name, actor or cli_actor())
        else:
            warnings.append(f"The fleet token was left valid on {name}.")
        warnings.append(deauthorize)
        self.tunnels.close(name)
        self._keys.forget(name)
        self.store.delete_node(name)
        return warnings

    def _revoke(self, name: str, actor: str) -> builtins.list[str]:
        """
        Ask a node to revoke the fleet token this central holds for it.

        Args:
            name: The node's name.
            actor: Who asks, for the node's audit log.

        Returns:
            Warnings; empty when the node revoked it, or no longer accepts it.
        """
        try:
            # Revoking a token is a write the node requires admin scope for
            # (noust.web.auth.required_scope); nothing lesser reaches it, and
            # this is the one call a fleet token may make about itself.
            response = self.client(name, timeout=15.0).request(
                "POST", FLEET_REVOKE_PATH, actor=actor, actor_scope="admin"
            )
        except NodeRefusedError as exc:
            if exc.status_code == 401:
                # Already refused: revoked on the node, which is the goal.
                return []
            return [
                f"{name} did not revoke its fleet token: {exc.message}. {exc.output or ''}".strip()
            ]
        except NodeError as exc:
            return [
                f"{name} could not be reached to revoke its fleet token ({exc.message}); "
                "it stays valid there until revoked on the node."
            ]
        if response.status_code >= 400:
            return [
                f"{name} did not revoke its fleet token (HTTP {response.status_code}); "
                f"it stays valid there until revoked on the node. {response.text[:500]}".strip()
            ]
        return []

    def test(self, name: str, *, actor: str | None = None) -> dict[str, Any]:
        """
        Check a node end to end: tunnel, token, API. Records the outcome.

        Unlike every other call, this presents the token to a node recorded
        as ``refused``: it is how the operator tells the central to resume
        after authorizing it again on the node.

        Args:
            name: The node's name.
            actor: Who asks, for the node's audit log; :func:`cli_actor` when None.

        Returns:
            ``reachable``, ``version``, ``latency_ms``, ``error`` (a sentence,
            or None), ``details`` (ssh's or the node's own words) and
            ``status`` as recorded.

        Raises:
            NodeError: When no node has that name.
        """
        record = self.get(name)
        started = time.monotonic()
        try:
            # The one call that asks a refused node again: the operator asked.
            # It is a status check: read is enough.
            info = self.client(name, retry_refused=True).get_json(
                VERSION_PATH, actor=actor or cli_actor(), actor_scope="read"
            )
        except NodeRefusedError as exc:
            self.store.set_node_status(name, "refused")
            return self._outcome(False, record.version, None, "refused", exc)
        except NodeError as exc:
            self.store.set_node_status(name, "unreachable")
            return self._outcome(False, record.version, None, "unreachable", exc)
        latency = round((time.monotonic() - started) * 1000, 1)
        version = info.get("current_version") if isinstance(info, dict) else None
        version = version if isinstance(version, str) else None
        self.store.set_node_status(name, "reachable", version=version)
        return self._outcome(True, version or record.version, latency, "reachable", None)

    @staticmethod
    def _outcome(
        reachable: bool,
        version: str | None,
        latency_ms: float | None,
        status: str,
        error: NodeError | None,
    ) -> dict[str, Any]:
        """
        Shape a test result.

        Args:
            reachable: Whether the node answered with the token.
            version: Its version.
            latency_ms: Round trip of the check, when it answered.
            status: The status recorded.
            error: The failure, if any.

        Returns:
            The result.
        """
        return {
            "reachable": reachable,
            "status": status,
            "version": version,
            "latency_ms": latency_ms,
            "error": error.message if error else None,
            "details": (error.details or None) if error else None,
        }
