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

Two changes go the same way round, the node authorizing first and the
central switching only once the node answers: ``noust node rekey`` (a new key
pair, :meth:`NodeManager.rekey`) and ``noust node migrate-tunnel`` (a node
authorized as root by 3.0 moving to the ``noust-tunnel`` account,
:meth:`NodeManager.migrate_tunnel`). Both are resumable: the same join code
can be pasted again, and a failure puts back what the central had.
"""

from __future__ import annotations

import builtins
import getpass
import shlex
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from noust.core.exceptions import NodeError, NodeRefusedError, ValidationError
from noust.core.runner import CommandRunner
from noust.core.secrets import SecretStore
from noust.core.store import NodeRecord, NoustStore, get_store
from noust.fleet.client import NodeClient, actor_label
from noust.fleet.joincode import JoinCode

if TYPE_CHECKING:
    import httpx
from noust.fleet.keys import KNOWN_HOSTS, TOKEN, NodeKeys, known_hosts_line, secret_name
from noust.fleet.models import PublicKey, central_name, parse_ssh_target, validate_node_name
from noust.fleet.policy import FleetAccess, node_registration_blockers
from noust.fleet.tunnels import TunnelManager, get_tunnels

#: What a node answers to prove the token works, and says its version with.
VERSION_PATH = "/api/system/version"

#: Where a fleet token revokes itself on its node. Allowed to a fleet token
#: (the only credential endpoint that is); it only ever revokes the token
#: that calls it.
FLEET_REVOKE_PATH = "/api/auth/fleet/revoke"

#: Where a node publishes the most any central may do on it (Noust 3.1+).
FLEET_SELF_PATH = "/api/auth/fleet/self"

#: The account a 3.1 node's ``noust fleet authorize`` gives centrals.
TUNNEL_USER = "noust-tunnel"


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
        return _authorize_line(key)

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
        self.refresh_access(name, actor=actor)
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
        self.refresh_access(name, actor=actor)
        return self._outcome(True, version or record.version, latency, "reachable", None)

    def refresh_access(self, name: str, *, actor: str | None = None) -> FleetAccess | None:
        """
        Ask a node the most it lets this central do, and record it.

        The node enforces its ceiling whatever the central believes; this is
        so the central can show it and grey out what the node would refuse.
        A node that does not publish one (3.0: 404) is recorded as unknown; a
        node that cannot be asked right now keeps what was known.

        Args:
            name: The node's name.
            actor: Who asks, for the node's audit log; :func:`cli_actor` when None.

        Returns:
            The ceiling, or None when it is not known.
        """
        try:
            body = self.client(name, timeout=15.0).get_json(
                FLEET_SELF_PATH, actor=actor or cli_actor(), actor_scope="read"
            )
        except NodeRefusedError:
            # A node whose permission table predates the endpoint refuses it
            # to a fleet token (403); nothing to learn, nothing to forget.
            return self._recorded_access(name)
        except NodeError as exc:
            if "answered 404" in exc.message:
                self.store.set_node_access(name, None, None)
                return None
            return self._recorded_access(name)
        try:
            access = FleetAccess.from_dict(body)
        except ValidationError:
            return self._recorded_access(name)
        self.store.set_node_access(name, access.level, access.host_access)
        return access

    def _recorded_access(self, name: str) -> FleetAccess | None:
        """
        Read the ceiling last recorded for a node.

        Args:
            name: The node's name.

        Returns:
            It, or None when none was.
        """
        record = self.store.get_node(name)
        if record is None or record.access_level is None:
            return None
        try:
            return FleetAccess.from_dict(
                {"level": record.access_level, "host_access": bool(record.host_access)}
            )
        except ValidationError:
            return None

    # -------------------------------------------------------------------------
    # Key rotation and the move to the tunnel account
    # -------------------------------------------------------------------------

    def rekey_command(self, name: str) -> str:
        """
        Start rotating a node's key: generate the new pair once, and spell the node's command.

        Asking again before the rotation finishes gives the same key and
        command, so it can be copied again.

        Args:
            name: The node's name.

        Returns:
            ``noust fleet authorize --central-key '<new key>' --name <central>``.

        Raises:
            NodeError: When the node is not registered, or ssh-keygen fails.
        """
        self.get(name)
        return _authorize_line(self._keys.ensure_pending_keypair(name, central_name()).line())

    def rekey(self, name: str, *, join_code: str, actor: str | None = None) -> NodeRecord:
        """
        Finish rotating a node's key, from the join code the node printed for the new one.

        The new pair becomes the node's, the code's token replaces the old one,
        and the node must answer with both; otherwise the old key, token and
        record are put back and the new pair keeps waiting, so the same code
        can be tried again. The old private key is then gone from this central.

        Args:
            name: The node's name.
            join_code: What ``noust fleet authorize`` printed for the new key.
            actor: Who rotates it, for the node's audit log.

        Returns:
            The node, reachable with its new key.

        Raises:
            NodeError: When no rotation is under way, the code is for another
                key or another server, or the node does not answer.
        """
        record = self.get(name)
        code = JoinCode.decode(join_code)
        pending = self._keys.pending_public_key(name)
        current = self._keys.public_key(name)
        if pending is None:
            if current is not None and current.fingerprint == code.central_key_fp:
                # The rotation already swapped the pair; the rest is resumable.
                return self._rejoin(record, code, actor=actor)
            raise NodeError(
                f"No new key is waiting for {name}",
                details=f"Start with 'noust node rekey {name}', run what it prints on the node, "
                "and paste the join code that prints.",
                field="join_code",
            )
        if code.central_key_fp != pending.fingerprint:
            raise NodeError(
                f"This join code authorizes another key than the new one for {name}",
                details=_fingerprint_help(name, code.central_key_fp, pending, current),
                field="join_code",
            )
        previous = self._keys.promote_pending(name)
        node = self._rejoin(
            record, code, actor=actor, undo=lambda: self._keys.write_pair(name, previous)
        )
        self._keys.discard_pending(name)
        return node

    def migrate_tunnel_command(self, name: str) -> str:
        """
        Spell the command that moves a node from root to the tunnel account.

        Args:
            name: The node's name.

        Returns:
            ``noust fleet authorize`` with this central's current key for it:
            on a Noust 3.1 node it installs the key for ``noust-tunnel`` and
            takes it out of root's ``authorized_keys``.

        Raises:
            NodeError: When the node is not registered or has no key here.
        """
        self.get(name)
        current = self._keys.public_key(name)
        if current is None:
            raise NodeError(
                f"This central holds no key for {name}",
                details=f"Rotate it instead: noust node rekey {name}",
            )
        return _authorize_line(current.line())

    def migrate_tunnel(self, name: str, *, join_code: str, actor: str | None = None) -> NodeRecord:
        """
        Move a node to the account its join code names, keeping the key.

        Args:
            name: The node's name.
            join_code: What ``noust fleet authorize`` printed on the node.
            actor: Who moves it, for the node's audit log.

        Returns:
            The node, reachable as the new account.

        Raises:
            NodeError: When the code is for root, another key or another
                server, or the node does not answer (the central is put back).
        """
        record = self.get(name)
        code = JoinCode.decode(join_code)
        current = self._keys.public_key(name)
        if current is None or code.central_key_fp != current.fingerprint:
            raise NodeError(
                f"This join code authorizes another key than the one this central holds for {name}",
                details=_fingerprint_help(name, code.central_key_fp, None, current),
                field="join_code",
            )
        if code.ssh_user == "root":
            raise NodeError(
                f"The join code still authorizes root on {name}",
                details=(
                    f"Run the command 'noust node migrate-tunnel {name}' printed, without "
                    f"--ssh-user root: the node's Noust must be 3.1 or later, which "
                    f"authorizes {TUNNEL_USER}."
                ),
                field="join_code",
            )
        return self._rejoin(record, code, actor=actor)

    def _rejoin(
        self,
        record: NodeRecord,
        code: JoinCode,
        *,
        actor: str | None,
        undo: Callable[[], None] | None = None,
    ) -> NodeRecord:
        """
        Switch a registered node to a new join code's account, port and token, and prove it.

        Args:
            record: The node as recorded.
            code: The new code, already matched to the key it is for.
            actor: Who does it, for the node's audit log.
            undo: Puts back anything the caller changed first (the key pair).

        Returns:
            The node, reachable.

        Raises:
            NodeError: When the code is for another server, or the node does
                not answer; everything is put back first.
        """
        name = record.name
        pinned = known_hosts_line(name, code.ssh_host_key)
        if pinned != record.host_key:
            if undo is not None:
                undo()
            raise NodeError(
                f"This join code is from another server than {name}",
                details=(
                    "Its SSH host key is not the one pinned when the node was added. If the "
                    f"server was reinstalled, remove it and add it again: noust node remove "
                    f"{name}."
                ),
                field="join_code",
            )
        old_token = self._secrets.read(secret_name(name, TOKEN))
        updated = NodeRecord(
            **{
                **record.to_dict(),
                "ssh_user": code.ssh_user,
                "console_port": code.console_port,
            }
        )
        self.tunnels.close(name)
        switched = False
        try:
            self._secrets.write(secret_name(name, TOKEN), code.token)
            self.store.save_node(updated)
            info = self.client(name, retry_refused=True).get_json(
                VERSION_PATH, actor=actor or cli_actor(), actor_scope="read"
            )
            version = info.get("current_version") if isinstance(info, dict) else None
            self.store.set_node_status(
                name, "reachable", version=version if isinstance(version, str) else None
            )
            switched = True
        finally:
            if not switched:
                self.tunnels.close(name)
                if old_token is not None:
                    self._secrets.write(secret_name(name, TOKEN), old_token)
                self.store.save_node(record)
                if undo is not None:
                    undo()
        self.refresh_access(name, actor=actor)
        return self.get(name)

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


def _authorize_line(key: str) -> str:
    """
    Spell the command a node's operator runs to authorize this central's key.

    Args:
        key: The public key line.

    Returns:
        ``noust fleet authorize --central-key '...' --name <central>``.
    """
    return f"noust fleet authorize --central-key {shlex.quote(key)} --name {central_name()}"


def _fingerprint_help(
    name: str, code_fp: str, pending: PublicKey | None, current: PublicKey | None
) -> str:
    """
    Explain which key a join code is for, against the keys this central holds for a node.

    Args:
        name: The node's name.
        code_fp: The fingerprint the code carries.
        pending: The key waiting in a rotation.
        current: The key in use.

    Returns:
        The details of the error.
    """
    held = []
    if pending is not None:
        held.append(f"the new key is {pending.fingerprint}")
    if current is not None:
        held.append(f"the key in use is {current.fingerprint}")
    return (
        f"The code is for {code_fp}; for {name} "
        + ("; ".join(held) or "this central holds no key")
        + ". Run the command this central printed for it on the node, and paste the join "
        "code that prints."
    )
