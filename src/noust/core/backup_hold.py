# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server does not rotate away a copy its central has not taken yet (item 195).

A central that collects a server's copies (:mod:`noust.fleet.collect`) comes after them: when the
server finishes one, and in a pass that takes what was left behind. If the central was off for a
weekend, the copies of those days are on the server and nowhere else, and the server's retention,
which knows nothing of the central, would see them as old enough to go. This is the one place that
says "not that one yet", asked by every retention of every kind of copy (an application's, a
database's, a volume's and Noust's own).

The rule is small on purpose:

* A copy is **spared** when a central that spoke recently
  (:data:`~noust.core.backup_collection_store.FRESH_DAYS`) does not hold it yet **and** the copy is
  younger than :data:`HOLD_MAX_DAYS`. Nothing here ever makes a retention delete more; it only
  deletes less, and for a limited time.
* A central that has not spoken in a week does not count: a central that was switched off for good
  must not make a server keep every copy for ever.
* Past :data:`HOLD_MAX_DAYS` the server's own retention decides again, because a server that waits
  for ever for a central that never comes fills its disk. When a retention then deletes a copy a
  central that follows does not hold, it says so (:func:`note_rotated_out`): in the audit trail and
  the log, with who was waiting.
* A retention that cannot ask keeps what it was about to judge, once, rather than delete a copy
  nobody could say was safe elsewhere.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

from noust.core import audit
from noust.core.backup_collection_store import CentralHeld
from noust.core.exceptions import NoustError

__all__ = ["HOLD_MAX_DAYS", "note_rotated_out", "rotate_out", "spared", "waiting"]

_log = logging.getLogger(__name__)

#: The longest a server keeps a copy for a central that has not taken it, beyond what its own
#: retention would. After it the retention decides.
HOLD_MAX_DAYS = 14


def _age(created: str | datetime, now: datetime) -> timedelta:
    """
    Args:
        created: When a copy was taken, as its manager records it (a time with no zone is this
            server's own).
        now: The moment to measure at.

    Returns:
        How old it is; zero for a time that cannot be read, so it is not mistaken for an old one.
    """
    moment = created
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        except ValueError:
            return timedelta(0)
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return max(timedelta(0), now - moment.astimezone(timezone.utc))


def waiting(kind: str, item_id: str, *, now: datetime | None = None) -> list[str]:
    """
    Name the centrals that follow this server and do not hold a copy yet.

    Args:
        kind: The kind of copy.
        item_id: Its identifier on this server.
        now: The moment to judge at; now by default.

    Returns:
        The centrals, by name; empty when no central is waiting for it.

    Raises:
        sqlite3.Error: The store could not be read.
        NoustError: The store refused the query.
    """
    moment = now or datetime.now(timezone.utc)
    held = CentralHeld()
    following = {sync.central for sync in held.following(moment)}
    return sorted(following - held.holders_map(kind, moment).get(item_id, set()))


def spared(
    kind: str,
    copies: Iterable[tuple[str, str | datetime]],
    *,
    now: datetime | None = None,
) -> set[str]:
    """
    Say which copies a retention leaves alone because a central has not taken them yet.

    Args:
        kind: The kind of copy.
        copies: ``(identifier, when it was taken)`` of what the retention is judging.
        now: The moment to judge at; now by default.

    Returns:
        The identifiers to leave, from none to all of them.
    """
    moment = now or datetime.now(timezone.utc)
    listed = list(copies)
    try:
        held = CentralHeld()
        following = {sync.central for sync in held.following(moment)}
        if not following:
            return set()
        holders = held.holders_map(kind, moment)
    except (sqlite3.Error, NoustError) as exc:
        _log.warning(
            "Could not ask which %s copies a central still waits for (%s); keeping them this time",
            kind,
            exc,
        )
        return {item for item, _ in listed}
    limit = timedelta(days=HOLD_MAX_DAYS)
    return {
        item
        for item, created in listed
        # One central that holds a copy is not every central: it is spared while any that follows
        # lacks it.
        if following - holders.get(item, set()) and _age(created, moment) <= limit
    }


def rotate_out(
    delete: Callable[[str], object],
    kind: str,
    item_id: str,
    owner: str,
    *,
    now: datetime | None = None,
) -> None:
    """
    Delete a copy a retention chose, and say so when a central had not taken it yet.

    The one way a retention of an application's backups deletes, so a copy that goes is never
    deleted in silence: asked before it goes, because afterwards the server holds nothing to ask.

    Args:
        delete: Deletes a copy by its identifier on this server; what it raises is the caller's.
        kind: The kind of copy.
        item_id: Its identifier on this server.
        owner: What it was a copy of, for the sentence.
        now: The moment to judge at; now by default.
    """
    try:
        lacking = waiting(kind, item_id, now=now)
    except (sqlite3.Error, NoustError) as exc:
        _log.debug("Could not ask who waited for %s %s: %s", kind, item_id, exc)
        lacking = []
    delete(item_id)
    if lacking:
        _said(kind, item_id, owner, lacking)


def note_rotated_out(kind: str, item_id: str, *, owner: str, now: datetime | None = None) -> None:
    """
    Say that a retention deleted a copy a central that follows did not hold yet.

    Nothing is said when no central was waiting for it, which is the usual case.

    Args:
        kind: The kind of copy.
        item_id: Its identifier on this server.
        owner: What it was a copy of, for the sentence.
        now: The moment to judge at; now by default.
    """
    try:
        lacking = waiting(kind, item_id, now=now)
    except (sqlite3.Error, NoustError) as exc:
        _log.debug("Could not ask who waited for %s %s: %s", kind, item_id, exc)
        return
    if lacking:
        _said(kind, item_id, owner, lacking)


def _said(kind: str, item_id: str, owner: str, lacking: list[str]) -> None:
    """
    Record that a copy went before the centrals that follow had it.

    Args:
        kind: The kind of copy.
        item_id: Its identifier on this server.
        owner: What it was a copy of.
        lacking: The centrals that were waiting for it.
    """
    _log.warning(
        "The retention deleted %s copy %s of %s before %s took it",
        kind,
        item_id,
        owner,
        ", ".join(lacking),
    )
    audit.record(
        "backup.uncollected_delete",
        target=f"{kind}:{owner}",
        outcome="ok",
        details={"copy": item_id, "centrals": lacking},
    )
