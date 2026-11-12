# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central tells its operator about collecting (item 195).

The same channels and the same switch as any backup that did not complete (``backup_failed``): a
copy the central could not take is a copy that is nowhere else. The collector decides *when* (it owns
the state of each server, so it owns the "once per outage" rule); this only composes and delivers,
on the notification worker, in order.
"""

from __future__ import annotations

from collections.abc import Callable

from noust.core.notifications.composers import compose_backup_failed
from noust.core.notifier import notify_composed

__all__ = ["Notify", "notify_failure"]

#: What the collector calls to tell somebody: the server, a stable word and the words.
Notify = Callable[[str, str, str], None]


def notify_failure(node: str, code: str, text: str) -> None:
    """
    Tell the operator the central could not take a server's copies.

    Args:
        node: The server.
        code: A stable word for what went wrong (``disk_full``, ``mismatch``, ``unreachable``...).
        text: What happened and what to do, verbatim.
    """
    notify_composed(
        lambda ctx: compose_backup_failed(
            None, f"Collecting the copies of {node} failed\n  Details: {text}", ctx
        )
    )
