# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What to take from a server, and what retention lets go of (item 195).

Two small functions of what exists and nothing else, so both are pinned without a disk or a
network:

- :func:`make_plan` compares the copies a server lists with the ones the central already has (held
  or let go of), and says what to take, newest first (the latest copy matters most if a pass is cut
  short), and what is left and why.
- :func:`expired` is the central's own retention, with the one rule of retention there is
  (:func:`noust.managers.retention.select_expired`): of the **scheduled** copies of one application
  on one server, the newest always stays, and what is beyond the count, the age or the weight the
  operator set goes. A copy made by hand and a restore's safety copy are never let go by a
  retention, whatever their age, and what a server says (it deleted a copy; it lists fewer) never
  makes the central delete one of its own.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone

from noust.fleet.collect.names import RemoteItem
from noust.fleet.collect.records import HELD, Collected
from noust.fleet.collect.settings import CollectSettings
from noust.managers.retention import gigabytes_to_bytes, select_expired

__all__ = ["Plan", "Skipped", "expired", "make_plan"]


@dataclass(frozen=True)
class Skipped:
    """
    A copy the server lists that is not taken.

    Attributes:
        item: The copy.
        reason: ``blocked`` (the server will not let it go as it is), ``not_collected_kind`` (this
            kind is not collected), ``retired`` (the central let it go and does not ask again).
    """

    item: RemoteItem
    reason: str


@dataclass
class Plan:
    """
    What a pass over a server does.

    Attributes:
        fetch: The copies to take, newest first.
        skipped: The copies it leaves, each with why.
        held: How many it already holds.
        changed: Copies the central holds whose main file the server now says is another one (its
            digest differs from what was taken): said, never taken again over the first.
    """

    fetch: list[RemoteItem] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    held: int = 0
    changed: list[RemoteItem] = field(default_factory=list)


def make_plan(
    items: Iterable[RemoteItem],
    have: Mapping[tuple[str, str], Collected],
    *,
    kinds: Collection[str],
) -> Plan:
    """
    Decide what to take from what a server lists.

    Args:
        items: The copies the server lists, already judged.
        have: The copies the central has of that server, by ``(kind, identifier)``, held or let go.
        kinds: The kinds that are collected.

    Returns:
        The plan.
    """
    plan = Plan()
    for item in sorted(items, key=lambda it: it.created_at, reverse=True):
        row = have.get((item.kind, item.id))
        if row is not None:
            if row.state == HELD:
                plan.held += 1
                recorded = next((f.sha256 for f in row.files if f.role == "archive"), None)
                if item.main.sha256 and recorded and item.main.sha256 != recorded:
                    plan.changed.append(item)
            else:
                plan.skipped.append(Skipped(item, "retired"))
            continue
        if item.kind not in kinds:
            plan.skipped.append(Skipped(item, "not_collected_kind"))
        elif item.blocked:
            plan.skipped.append(Skipped(item, "blocked"))
        else:
            plan.fetch.append(item)
    return plan


def expired(
    held: Iterable[Collected], settings: CollectSettings, *, now: datetime | None = None
) -> list[Collected]:
    """
    Choose which copies the central's own retention lets go of.

    Args:
        held: The copies the central holds.
        settings: The retention it was given.
        now: The clock, injectable for tests.

    Returns:
        The copies to let go, oldest last. Never one made by hand or for safety, never the newest
        scheduled copy of an application, and nothing when no limit was set.
    """
    if not (settings.retention_count or settings.retention_days or settings.retention_gb):
        return []
    # A server keeps the data of an application per target (its uploads, its volume), each with its
    # own newest copy, so the central does too.
    groups: dict[tuple[str, str, str, str], list[Collected]] = {}
    for copy in held:
        if copy.state == HELD and copy.origin == "scheduled":
            key = (copy.node, copy.kind, copy.owner, copy.meta.get("target", ""))
            groups.setdefault(key, []).append(copy)
    reference = now or datetime.now(timezone.utc)
    chosen: list[Collected] = []
    for members in groups.values():
        by_id = {copy.item_id: copy for copy in members}
        gone = select_expired(
            [(copy.item_id, datetime.fromisoformat(copy.created_at)) for copy in members],
            count=settings.retention_count,
            days=settings.retention_days,
            now=reference,
            max_bytes=gigabytes_to_bytes(settings.retention_gb),
            sizes={copy.item_id: copy.size for copy in members},
        )
        chosen.extend(by_id[item_id] for item_id in gone)
    return chosen
