# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Telling the operator, through the channels, that a change to how the server is reached was undone (item 138).

The ledger (:mod:`noust.managers.server.security_pending`) undoes a change nobody confirmed in time,
from a transient unit that has no console and no person in front of it. The audit entry is the
record; this is the news, published synchronously because the process that undoes the change exits
right after and a worker thread would not be given time to deliver.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from noust.core.exceptions import NoustError
from noust.core.notifications.composers_access import compose_access_change_reverted
from noust.core.notifications.context import NotificationContext
from noust.core.notifier import Notifier, fresh_config

if TYPE_CHECKING:
    from noust.managers.server.security_pending import PendingChange

log = logging.getLogger(__name__)

#: What publishing may raise and still leave the undo done: the notifier isolates a dead channel
#: itself, so what reaches here is a configuration that cannot be read or a message that cannot be
#: composed, never a reason to leave a change in place.
_PUBLISH_ERRORS: tuple[type[Exception], ...] = (NoustError, OSError, ValueError, KeyError)


def announce_expired(change: PendingChange) -> None:
    """
    Publish that a change was undone because nobody confirmed it.

    Never raises for a publication problem: the change is already back, and a channel that is down
    must not turn that into a failure of the undo.

    Args:
        change: The change, as the ledger saved it once undone (``resolution`` holds what the undo
            printed).
    """
    try:
        config = fresh_config()
        notification = compose_access_change_reverted(
            change.kind,
            change.title,
            NotificationContext.from_config(config),
            actor=change.actor,
            applied_at=change.applied_at,
            window_s=change.expires_at - change.applied_at,
            output=change.resolution,
            what=change.what,
            values=change.values,
        )
        Notifier(config).notify(notification)
    except _PUBLISH_ERRORS as exc:
        log.warning("Could not publish that change %s was undone: %s", change.id, exc)
