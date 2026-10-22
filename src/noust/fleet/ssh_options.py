# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How the central connects to one server: how long it waits, and a bastion in between.

The port was always the node's (``--ssh USER@HOST:PORT``). The wait for the connection
was one constant for every server, and a server reachable only through a bastion could
not be a node at all. Both are the server's own now, kept in ``node_ssh_options``
(schema v14) and read in one place by the tunnel (:func:`noust.fleet.tunnels.ssh_argv`).

**The bastion is a hop, not a trust.** The central opens ``ssh -W`` to the bastion and
asks it for one stream to the server's sshd; the server's host key is still pinned and
checked by the outer ssh, end to end, so the bastion carries bytes it cannot read or
change. The bastion's own host key is pinned too, from the operator (never learnt on
first use), and the hop runs with the same hardening as every other connection of the
central. It is *not* ``ssh -J``: the second ssh that ``-J`` starts is told only the
account and the port, and with ``-F /dev/null`` it would read the operator's keys and
``known_hosts`` and could ask about an unknown host. The central builds the hop's whole
command line instead (:func:`noust.fleet.tunnels.bastion_proxy_command`).

The key the central presents at the bastion is the one it holds for the server, so a
bastion's operator authorizes one more line, restricted to one stream to that server
(:meth:`Bastion.authorized_key_line`); the central still gets no shell anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NodeError
from noust.core.store import NodeRecord, NoustStore, get_store
from noust.fleet.models import (
    SSHTarget,
    parse_public_key,
    parse_ssh_target,
    validate_node_name,
    validate_port,
)

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "MAX_CONNECT_TIMEOUT",
    "MIN_CONNECT_TIMEOUT",
    "Bastion",
    "NodeSSHOptions",
    "SSHOptions",
    "parse_bastion",
    "parse_bastion_address",
    "validate_connect_timeout",
]

#: Seconds ssh waits for a connection unless the server says otherwise.
DEFAULT_CONNECT_TIMEOUT = 10

#: The range an operator may choose from: a LAN answers in one second, a satellite
#: link or a slow VPN in thirty, and past five minutes it is not a connection.
MIN_CONNECT_TIMEOUT = 1
MAX_CONNECT_TIMEOUT = 300

#: Leave a field as it is, in :meth:`NodeSSHOptions.configure`: None is a value there
#: (the default timeout, no bastion).
UNSET: Any = object()


def validate_connect_timeout(value: object) -> int:
    """
    Check the seconds ssh waits for a connection.

    Args:
        value: What the operator asked for.

    Returns:
        The seconds.

    Raises:
        NodeError: When it is not a whole number from 1 to 300.
    """
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not MIN_CONNECT_TIMEOUT <= value <= MAX_CONNECT_TIMEOUT
    ):
        raise NodeError(
            f"Invalid connection timeout: {value!r}",
            details=f"Use a whole number of seconds from {MIN_CONNECT_TIMEOUT} to "
            f"{MAX_CONNECT_TIMEOUT}.",
            field="connect_timeout",
        )
    return value


@dataclass(frozen=True)
class Bastion:
    """
    The host a server is reached through.

    Attributes:
        host: Its host name or address (IPv6 without brackets).
        port: Its SSH port.
        user: The account the central logs in as there.
        host_key: Its ed25519 host key, ``ssh-ed25519 AAAA...``: pinned, never learnt.
    """

    host: str
    user: str
    host_key: str
    port: int = 22

    @property
    def address(self) -> str:
        """``user@host:port``, as the operator types it."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.user}@{host}:{self.port}"

    @property
    def fingerprint(self) -> str:
        """The host key's ``SHA256:...``, as ``ssh-keygen -l`` prints it."""
        return parse_public_key(self.host_key, what="bastion host key").fingerprint

    def authorized_key_line(self, central_key: str, node: NodeRecord) -> str:
        """
        Spell the line the bastion's operator adds to the account's ``authorized_keys``.

        It allows what the central does there and nothing else: one stream to the
        server's sshd. No shell (``restrict``), no command, no other destination.

        Args:
            central_key: The central's public key for the server
                (``noust node key``).
            node: The server it reaches.

        Returns:
            The line.
        """
        key = parse_public_key(central_key, what="central key")
        host = f"[{node.ssh_host}]" if ":" in node.ssh_host else node.ssh_host
        return (
            f'restrict,port-forwarding,permitopen="{host}:{node.ssh_port}",'
            f'command="/usr/bin/false" {key.line()}'
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the bastion for the API and the CLI.

        Returns:
            The address and the fingerprint of its pinned key; not the key.
        """
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "address": self.address,
            "fingerprint": self.fingerprint,
        }


def parse_bastion_address(target: str) -> tuple[str, str, int]:
    """
    Read the address of a bastion: ``USER@HOST[:PORT]``.

    Args:
        target: What the operator typed after ``--bastion``.

    Returns:
        The account, the host and the port (22 when not given).

    Raises:
        NodeError: When the account is missing or a part is malformed.
    """
    parsed: SSHTarget = parse_ssh_target(target)
    if parsed.user is None:
        raise NodeError(
            "The bastion needs an account",
            details="Give it as USER@HOST[:PORT], such as hop@bastion.example.com:22.",
            field="bastion",
        )
    return parsed.user, parsed.host, parsed.port or 22


def parse_bastion(target: str, host_key: str) -> Bastion:
    """
    Read a bastion from ``USER@HOST[:PORT]`` and its host key.

    Args:
        target: What the operator typed after ``--bastion``.
        host_key: Its ed25519 host key line (as ``ssh-keyscan -t ed25519`` prints it,
            without the host); a comment is dropped.

    Returns:
        The bastion.

    Raises:
        NodeError: When the account is missing, a part is malformed, or the key is
            not an ed25519 key.
    """
    user, host, port = parse_bastion_address(target)
    key = parse_public_key(host_key, what="bastion host key")
    return Bastion(host=host, user=user, host_key=key.bare, port=port)


@dataclass(frozen=True)
class SSHOptions:
    """
    How the central connects to one server, beyond its address.

    Attributes:
        connect_timeout: Seconds to wait for the connection; None is the default.
        bastion: The host it is reached through; None is directly.
    """

    connect_timeout: int | None = None
    bastion: Bastion | None = None

    @property
    def effective_timeout(self) -> int:
        """The seconds ssh waits: the server's own, or the default."""
        return self.connect_timeout or DEFAULT_CONNECT_TIMEOUT

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the options for the API and the CLI.

        Returns:
            The timeout (None for the default), the effective one, and the bastion.
        """
        return {
            "connect_timeout": self.connect_timeout,
            "effective_timeout": self.effective_timeout,
            "default_timeout": DEFAULT_CONNECT_TIMEOUT,
            "bastion": self.bastion.to_dict() if self.bastion else None,
        }


class NodeSSHOptions:
    """
    Read and write the connection options of the servers of this central.

    The only reader and writer of ``node_ssh_options``.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def get(self, node: str) -> SSHOptions:
        """
        Read one server's options.

        Args:
            node: The server's name.

        Returns:
            Its options; the defaults for a server that has none.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT connect_timeout, bastion_host, bastion_port, bastion_user, "
                "bastion_host_key FROM node_ssh_options WHERE node = ?",
                (node,),
            )
            .fetchone()
        )
        if row is None:
            return SSHOptions()
        bastion = None
        if row["bastion_host"]:
            bastion = Bastion(
                host=row["bastion_host"],
                port=row["bastion_port"] or 22,
                user=row["bastion_user"],
                host_key=row["bastion_host_key"],
            )
        return SSHOptions(connect_timeout=row["connect_timeout"], bastion=bastion)

    def configure(
        self,
        node: str,
        *,
        connect_timeout: int | None = UNSET,
        bastion: Bastion | None = UNSET,
        actor: str | None = None,
    ) -> SSHOptions:
        """
        Change a server's options; what is not named is kept.

        Args:
            node: The server's name; it must be registered.
            connect_timeout: Seconds to wait, or None for the default.
            bastion: The bastion, or None to connect directly.
            actor: Who changed it.

        Returns:
            The options now.

        Raises:
            NodeError: When the server is not registered or dials in (it has no SSH
                address to configure), the timeout is out of range, or the bastion is
                the server itself.
        """
        name = validate_node_name(node)
        record = self.store.get_node(name)
        if record is None:
            raise NodeError(
                f"No node named {name} is registered on this central",
                details="List them with 'noust node list'.",
                field="node",
            )
        if self._dials_in(name):
            raise NodeError(
                f"{name} dials this central: it has no SSH address to configure",
                details="Its connection is the link it keeps open; see 'noust node show'.",
                field="node",
            )
        current = self.get(name)
        timeout = current.connect_timeout
        if connect_timeout is not UNSET:
            timeout = None if connect_timeout is None else validate_connect_timeout(connect_timeout)
        chosen = current.bastion if bastion is UNSET else bastion
        if chosen is not None:
            validate_port(chosen.port, "bastion port")
            if (chosen.host.lower(), chosen.port) == (record.ssh_host.lower(), record.ssh_port):
                raise NodeError(
                    f"The bastion is {name} itself",
                    details="A bastion is another host that can reach it; leave the bastion out "
                    "to connect directly.",
                    field="bastion",
                )
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO node_ssh_options (node, connect_timeout, bastion_host, bastion_port, "
                "bastion_user, bastion_host_key, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(node) DO UPDATE SET connect_timeout = excluded.connect_timeout, "
                "bastion_host = excluded.bastion_host, bastion_port = excluded.bastion_port, "
                "bastion_user = excluded.bastion_user, "
                "bastion_host_key = excluded.bastion_host_key, "
                "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (
                    name,
                    timeout,
                    chosen.host if chosen else None,
                    chosen.port if chosen else None,
                    chosen.user if chosen else None,
                    chosen.host_key if chosen else None,
                    datetime.now(timezone.utc).isoformat(),
                    actor,
                ),
            )
        return SSHOptions(connect_timeout=timeout, bastion=chosen)

    def _dials_in(self, node: str) -> bool:
        """
        Tell whether a server reaches this central through a link it keeps open.

        Args:
            node: The server's name.

        Returns:
            True when there is a link record for it.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT 1 FROM node_links WHERE node = ?", (node,))
            .fetchone()
        )
        return row is not None
