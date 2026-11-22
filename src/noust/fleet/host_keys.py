# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Taking the new SSH host key of a server, once a person has looked at it.

A central pins each server's host key when it is added and never learns it again: a server that presents
another one gets its tunnel closed and a notice (:mod:`noust.fleet.tunnels`), because accepting a changed
key without looking is how a machine in the middle is trusted. The only way out was to remove the server
and add it again. This is the decision itself, in two steps that cannot be one:

1. **Look.** The central asks the server's own address for the key it presents now and says it beside the
   one that is pinned (:func:`reading`): two fingerprints, the person compares them with what the server
   says of itself (``ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub``, or the provider's console).
2. **Accept.** The person gives the fingerprint they read, and the central takes the key only when what
   answers is exactly that: a key that changed again between the two steps, or an address that now
   answers for another server, is refused with nothing changed
   (:meth:`noust.fleet.nodes.NodeManager.accept_host_key`).

Both steps are :class:`~noust.fleet.nodes.NodeManager`'s (rule 4: the one registry the command line and the
API are clients of), and neither is possible for a server the central does not reach by SSH directly: one
that dials in has no host key of this central's, and one behind a bastion cannot be asked from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from noust.core.exceptions import NodeError
from noust.core.server_text import say
from noust.core.store import NodeRecord
from noust.fleet.keys import pinned_fingerprint
from noust.fleet.models import FINGERPRINT
from noust.fleet.ssh_options import SSHOptions

__all__ = [
    "HostKeyAccepted",
    "HostKeyReading",
    "reading",
    "require_direct",
    "require_fingerprint",
]


@dataclass(frozen=True)
class HostKeyReading:
    """
    What a server presents now, beside what is pinned.

    Attributes:
        node: The server's name.
        address: Where the central asked, ``user@host:port``.
        pinned: The fingerprint of the key pinned when the server was added; None when the pinned
            line cannot be read.
        presented: The fingerprint of the key it presents now.
        key: That key, ``ssh-ed25519 AAAA...``: what is pinned when it is accepted. Not shown.
        changed: Whether the two differ.
    """

    node: str
    address: str
    pinned: str | None
    presented: str
    key: str
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            What a person is shown: the fingerprints and whether they differ, never the key.
        """
        return {
            "node": self.node,
            "address": self.address,
            "pinned": self.pinned,
            "presented": self.presented,
            "changed": self.changed,
        }


@dataclass(frozen=True)
class HostKeyAccepted:
    """
    What accepting a host key did.

    Attributes:
        node: The server's name.
        previous: The fingerprint that was pinned.
        presented: The fingerprint that is pinned now.
        changed: False when the server presented the key that was already pinned and there was
            nothing to accept.
    """

    node: str
    previous: str | None
    presented: str
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The result as plain data.
        """
        return {
            "node": self.node,
            "previous": self.previous,
            "presented": self.presented,
            "changed": self.changed,
        }


def require_fingerprint(text: str) -> str:
    """
    Check that a text is a host key fingerprint, before anything is asked of the network.

    Args:
        text: What a person typed or a script gave.

    Returns:
        The fingerprint, as given.

    Raises:
        NodeError: When it is not ``SHA256:`` and 43 characters of base64, the way ``ssh-keygen -l``
            prints it.
    """
    if not isinstance(text, str) or FINGERPRINT.fullmatch(text) is None:
        raise NodeError.from_text(say("fleet.host_key.fingerprint_malformed"), field="fingerprint")
    return text


def require_direct(record: NodeRecord, options: SSHOptions) -> None:
    """
    Refuse a server this central cannot ask directly.

    Args:
        record: The server.
        options: How the central connects to it.

    Raises:
        NodeError: When it is reached through a bastion: ``ssh-keyscan`` would ask the bastion's
            network, not the server's, and what answers there says nothing of it.
    """
    if options.bastion is not None:
        raise NodeError.from_text(say("fleet.host_key.bastion", node=record.name))


def reading(record: NodeRecord, presented_key: str, presented_fingerprint: str) -> HostKeyReading:
    """
    Put what a server presents beside what is pinned.

    Args:
        record: The server, whose ``host_key`` is the pinned line.
        presented_key: The key it presents, ``ssh-ed25519 AAAA...``.
        presented_fingerprint: Its fingerprint.

    Returns:
        The reading.
    """
    pinned = pinned_fingerprint(record.host_key)
    return HostKeyReading(
        node=record.name,
        address=f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}",
        pinned=pinned,
        presented=presented_fingerprint,
        key=presented_key,
        changed=pinned != presented_fingerprint,
    )
