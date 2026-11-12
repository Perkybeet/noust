# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central tells its operator about collecting (item 195).

The same channels and the same switch as any backup that did not complete (``backup_failed``): a
copy the central could not take is a copy that is nowhere else. The collector decides *when* (it owns
the state of each server, so it owns the "once per outage" rule); what is said, and the one door it
is said through, is with the rest of what a central says (:mod:`noust.core.notifications.fleet`).
"""

from __future__ import annotations

from collections.abc import Callable

from noust.core.notifications.fleet import notify_collect_failed

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
    notify_collect_failed(node, text)
