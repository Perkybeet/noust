# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who is listening on a link's port.

A server that dials in leaves a listener on a fixed loopback port of its
central, and the central sends the server's fleet token to whatever answers
there. A local account that took the port while the link was down would
receive that token. The kernel says who owns every listening socket
(``/proc/net/tcp``), and what sshd opens for a remote forward is owned by the
account the session runs as (``noust-link``): the central sends nothing to a
port whose listener is anyone else's.
"""

from __future__ import annotations

import pwd
from collections.abc import Callable
from pathlib import Path

from noust.core.exceptions import NodeUnreachableError

#: The account servers that dial in log in as, on their central. Whatever sshd
#: opens for one of their forwards is owned by it.
LINK_USER = "noust-link"

#: The kernel's socket tables.
PROC_NET = Path("/proc/net")

#: ``st`` of a listening socket in ``/proc/net/tcp``.
_LISTEN = "0A"

#: The addresses a connection to 127.0.0.1 can reach a listener on, as
#: ``/proc/net`` spells them (little-endian hex): 127.0.0.1 and 0.0.0.0 in
#: ``tcp``; ``::``, ``::ffff:127.0.0.1`` and ``::ffff:0.0.0.0`` in ``tcp6``,
#: where a dual-stack socket also answers IPv4.
_V4_ADDRESSES = frozenset({"0100007F", "00000000"})
_V6_ADDRESSES = frozenset(
    {
        "00000000000000000000000000000000",
        "0000000000000000FFFF00000100007F",
        "0000000000000000FFFF000000000000",
    }
)


def link_account_uids(
    passwd: Callable[[str], pwd.struct_passwd] = pwd.getpwnam,
) -> frozenset[int]:
    """
    Name the accounts whose listener on a link's port is a link's: the link account, and root.

    Root can read the fleet token anyway; a listener of its own is no leak.

    Args:
        passwd: Account lookup.

    Returns:
        The uids; only root's when the link account does not exist.
    """
    try:
        return frozenset({0, passwd(LINK_USER).pw_uid})
    except KeyError:
        return frozenset({0})


def listener_uids(port: int, proc_net: Path = PROC_NET) -> set[int]:
    """
    Name the accounts that own a listener a connection to 127.0.0.1:port would reach.

    Args:
        port: The loopback port.
        proc_net: ``/proc/net``.

    Returns:
        The owners' uids; empty when nothing listens there (or the tables cannot be read).
    """
    owners: set[int] = set()
    for table, addresses in (("tcp", _V4_ADDRESSES), ("tcp6", _V6_ADDRESSES)):
        try:
            lines = (proc_net / table).read_text(encoding="ascii", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 8 or fields[3] != _LISTEN:
                continue
            address, _, hex_port = fields[1].rpartition(":")
            if address.upper() not in addresses or not hex_port:
                continue
            try:
                if int(hex_port, 16) != port:
                    continue
                owners.add(int(fields[7]))
            except ValueError:
                continue
    return owners


def require_trusted_listener(
    node: str, port: int, allowed: frozenset[int], proc_net: Path = PROC_NET
) -> None:
    """
    Refuse a link's port unless something is listening there and only the link account owns it.

    Args:
        node: The server, for the message.
        port: The link's loopback port.
        allowed: The uids whose listener this is: the link account, and root.
        proc_net: ``/proc/net``.

    Raises:
        NodeUnreachableError: When nothing listens (the link is down) or a listener
            belongs to someone else (nothing is sent to it).
    """
    owners = listener_uids(port, proc_net)
    if not owners:
        raise NodeUnreachableError(
            f"{node} is not connected: nothing listens on 127.0.0.1:{port}",
            details=(
                f"{node} dials this central from behind its NAT and keeps the link open. "
                "On it, 'systemctl status noust-link-<central>' says why it is not: the "
                "central's address, its pinned host key, or a firewall in the way."
            ),
        )
    if not owners <= allowed:
        raise NodeUnreachableError(
            f"Something else listens on 127.0.0.1:{port}, the port of {node}'s link",
            details=(
                "Only the link account's sshd may hold it. Nothing is sent to it, so a local "
                f"account cannot read {node}'s fleet token. Find it with 'ss -ltnp "
                f"sport = :{port}' and stop it."
            ),
        )
