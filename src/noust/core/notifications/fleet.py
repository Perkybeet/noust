# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central tells its operator about the servers it manages.

A node notifies from its own process (its deploys, its disk, its
certificates); nobody notifies for a node that cannot. That is the central's
job, and these three functions are how the fleet code does it:

- :func:`notify_node_unreachable` when a node's tunnel has been down long
  enough that it is not a blip;
- :func:`notify_node_recovered` when it is back, once, so the alert closes;
- :func:`notify_node_host_key_changed` when a node presents another SSH host
  key: the tunnel stays closed, and someone has to look.

The fleet code decides *when* (it owns the tunnels and their state, so it
also owns the "once per outage" rule, the way the monitor does for units);
these functions only compose and deliver. Delivery is on the notification
worker, in order, and honours the operator's ``node_*`` switches.
"""

from __future__ import annotations

from datetime import datetime

from noust.core.notifications.composers import (
    compose_node_host_key_changed,
    compose_node_recovered,
    compose_node_unreachable,
)
from noust.core.notifier import notify_composed


def notify_node_unreachable(
    node: str,
    *,
    reason: str | None = None,
    address: str | None = None,
    since: datetime | None = None,
) -> None:
    """
    Tell the operator a managed server cannot be reached.

    Args:
        node: The server's name in the fleet registry.
        reason: Why, in the tunnel's own words (ssh's stderr), verbatim.
        address: Where the central tries to reach it.
        since: When it was last known to be up.
    """
    notify_composed(
        lambda ctx: compose_node_unreachable(node, ctx, reason=reason, address=address, since=since)
    )


def notify_node_recovered(node: str, *, down_for_s: float | None = None) -> None:
    """
    Tell the operator a managed server is reachable again.

    Args:
        node: The server's name in the fleet registry.
        down_for_s: How long it was unreachable, in seconds.
    """
    notify_composed(lambda ctx: compose_node_recovered(node, ctx, down_for_s=down_for_s))


def notify_node_host_key_changed(
    node: str,
    *,
    address: str | None = None,
    pinned: str | None = None,
    presented: str | None = None,
    command: str | None = None,
) -> None:
    """
    Tell the operator a managed server presented a different SSH host key.

    Args:
        node: The server's name in the fleet registry.
        address: Where the central reached it.
        pinned: The fingerprint pinned when the server was added.
        presented: The fingerprint it presented now.
        command: The command that checks and, when it is right, accepts the
            new key, if the fleet has one to suggest.
    """
    notify_composed(
        lambda ctx: compose_node_host_key_changed(
            node, ctx, address=address, pinned=pinned, presented=presented, command=command
        )
    )
