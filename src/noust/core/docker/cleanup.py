# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cleaning Docker as a plan that is shown first.

``docker system prune`` takes the volumes with it, and a volume is somebody's data; ``docker image
prune -a`` takes the image an application keeps to go back to. Neither is ever run here. A cleanup is
a plan instead: what would go, whose it is, how much it holds and why it is not needed, and what
stays and why, and only what the person ticked is removed, each thing by the gate that judges it again.

What goes into the plan, and what does not:

- **Images** that no container uses (a stopped container uses its image). One with no name is ticked;
  one with a name is offered and not ticked. The image an application keeps to go back to is never
  offered: it is said to stay, and so is what Noust itself uses.
- **Containers** that are stopped, that nobody of Noust made and that are not part of a stack. Never
  ticked: a stopped container is somebody's work.
- **Volumes** that no container mounts and that nobody of Noust made. Never ticked, and the person
  types the name of each before it goes: what is inside is not known, only how much of it there is.
- **Networks** that nobody is on, that nobody of Noust made and that Docker did not make itself.
- **The build cache** older than a week, pruned by age and never together with the volumes.

What an application or a database engine owns is not in it: it goes with the application or the
engine, which say what that takes.

A plan has a token: a hash of what is in it (never of how big the things are, which move). The plan
is applied with the token it was shown with, and one that is not the plan of now is not applied: it is
shown again, because what the person agreed to is not what would be removed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from noust.core.docker.models import NOBODY, Container, Image, Network, Owner, OwnerKind, Volume

__all__ = [
    "ORDER",
    "CleanupPlan",
    "CleanupResult",
    "Kept",
    "PlanItem",
    "build_plan",
    "parse_reclaimed",
    "plan_token",
]

#: The order things are removed in: what holds others goes first, so that what it held is free.
ORDER = ("container", "image", "network", "volume", "cache")

#: What the name of a volume nobody named looks like.
_ANONYMOUS = re.compile(r"[0-9a-f]{64}")

_RECLAIMED = re.compile(r"Total reclaimed space:\s*([\d.]+\s*[kKMGTP]?B)")


@dataclass(frozen=True)
class PlanItem:
    """
    One thing a cleanup would remove.

    Attributes:
        id: What identifies it to Docker: an image's or a network's id, a container's id, a volume's
            name, ``cache``. The plan is applied by these.
        kind: ``image``, ``container``, ``volume``, ``network`` or ``cache``.
        name: What a person calls it.
        owner: Whose it is: always nobody's (what Noust made goes with what made it), said anyway.
        size_bytes: How much it holds, when Docker said.
        why: ``dangling`` (no name and no container), ``unused`` (no container uses it),
            ``stopped`` or ``old_cache``.
        tick: Whether it is marked when the plan is shown.
        type_name: The person types its name before it goes.
        anonymous: A volume that a container made without naming it.
    """

    id: str
    kind: str
    name: str
    owner: Owner
    size_bytes: int | None
    why: str
    tick: bool
    type_name: bool = False
    anonymous: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "owner": self.owner.to_dict(),
            "size_bytes": self.size_bytes,
            "why": self.why,
            "tick": self.tick,
            "type_name": self.type_name,
            "anonymous": self.anonymous,
        }


@dataclass(frozen=True)
class Kept:
    """
    Something that looks unused and stays, and why.

    Attributes:
        name: What it is called.
        owner: Whose it is.
        size_bytes: How much it holds, when Docker said.
        why: ``rollback`` (an application keeps it to go back to) or ``noust`` (Noust uses it).
    """

    name: str
    owner: Owner
    size_bytes: int | None
    why: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "name": self.name,
            "owner": self.owner.to_dict(),
            "size_bytes": self.size_bytes,
            "why": self.why,
        }


def plan_token(items: Sequence[PlanItem]) -> str:
    """
    Hash what a plan holds.

    Args:
        items: The plan's items.

    Returns:
        A token that is the same for the same things, whatever their sizes and whatever the order.
    """
    body = "\n".join(sorted(f"{item.kind}:{item.id}" for item in items))
    return hashlib.sha256(body.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class CleanupPlan:
    """
    What a cleanup would do, worked out and not done.

    Attributes:
        token: What the plan is applied with (see :func:`plan_token`).
        items: What would go, in the order it goes.
        kept: What looks unused and stays.
    """

    token: str
    items: tuple[PlanItem, ...]
    kept: tuple[Kept, ...] = ()

    @property
    def default_bytes(self) -> int:
        """What is recovered with what is ticked when the plan is shown."""
        return sum(item.size_bytes or 0 for item in self.items if item.tick)

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The token, the items, what stays and what the default selection recovers.
        """
        return {
            "token": self.token,
            "items": [item.to_dict() for item in self.items],
            "kept": [kept.to_dict() for kept in self.kept],
            "default_bytes": self.default_bytes,
        }


@dataclass
class CleanupResult:
    """
    What applying a plan did.

    Attributes:
        removed: What was removed: ``kind``, ``name`` and ``size_bytes`` of each.
        failed: What Docker refused to remove: ``kind``, ``name`` and its own words in ``error``.
        freed_bytes: What the removed things held, and what the cache pruned said it freed.
    """

    removed: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    freed_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {"removed": self.removed, "failed": self.failed, "freed_bytes": self.freed_bytes}


def parse_reclaimed(text: str) -> int | None:
    """
    Read what ``docker builder prune`` says it freed.

    Args:
        text: Its output.

    Returns:
        The bytes, or None when it did not say.
    """
    from noust.core.docker.reading import parse_size

    found = _RECLAIMED.search(text)
    return parse_size(found.group(1)) if found else None


def _label(image: Image) -> str:
    """
    Args:
        image: An image.

    Returns:
        What a person calls it: its first name, or ``<none>`` and the first digits of its id.
    """
    if image.tags:
        return image.tags[0]
    return f"<none> {image.id.removeprefix('sha256:')[:12]}"


def build_plan(
    *,
    images: Sequence[Image],
    containers: Sequence[Container],
    volumes: Sequence[Volume],
    networks: Sequence[Network],
    volume_sizes: Mapping[str, int],
    cache_bytes: int | None,
) -> CleanupPlan:
    """
    Work out what a cleanup would do.

    Args:
        images: The images the principal sees, each with its owner and who runs it.
        containers: The containers the principal sees, each with its owner.
        volumes: The volumes the principal sees, each with who mounts it.
        networks: The networks the principal sees, each with who is on it.
        volume_sizes: How much each volume holds, by name; a volume that is not in it is of an
            unknown size.
        cache_bytes: How much of the build cache is reclaimable; None or 0 for none.

    Returns:
        The plan. A principal that sees only what some teams own gets an empty one: nothing that
        goes into it is a team's.
    """
    items: list[PlanItem] = []
    kept: list[Kept] = []
    for image in images:
        if image.used_by:
            continue
        if image.way_back:
            kept.append(Kept(_label(image), image.owner, image.size_bytes, "rollback"))
            continue
        if image.owner.kind is OwnerKind.NOUST:
            kept.append(Kept(_label(image), image.owner, image.size_bytes, "noust"))
            continue
        if image.owner.kind is not OwnerKind.NOBODY:
            continue
        dangling = not image.tags
        items.append(
            PlanItem(
                id=image.id,
                kind="image",
                name=_label(image),
                owner=NOBODY,
                size_bytes=image.size_bytes,
                why="dangling" if dangling else "unused",
                tick=dangling,
            )
        )
    for container in containers:
        if (
            container.owner.kind is OwnerKind.NOBODY
            and container.state == "exited"
            and not container.project
        ):
            items.append(
                PlanItem(
                    id=container.id,
                    kind="container",
                    name=container.name,
                    owner=NOBODY,
                    size_bytes=None,
                    why="stopped",
                    tick=False,
                )
            )
    for volume in volumes:
        if volume.owner.kind is OwnerKind.NOBODY and not volume.used_by:
            items.append(
                PlanItem(
                    id=volume.name,
                    kind="volume",
                    name=volume.name,
                    owner=NOBODY,
                    size_bytes=volume_sizes.get(volume.name),
                    why="unused",
                    tick=False,
                    type_name=True,
                    anonymous=_ANONYMOUS.fullmatch(volume.name) is not None,
                )
            )
    for network in networks:
        if (
            network.owner.kind is OwnerKind.NOBODY
            and not network.builtin
            and not network.containers
        ):
            items.append(
                PlanItem(
                    id=network.id,
                    kind="network",
                    name=network.name,
                    owner=NOBODY,
                    size_bytes=None,
                    why="unused",
                    tick=True,
                )
            )
    if cache_bytes:
        items.append(
            PlanItem(
                id="cache",
                kind="cache",
                name="",
                owner=NOBODY,
                size_bytes=cache_bytes,
                why="old_cache",
                tick=True,
            )
        )
    ordered = tuple(sorted(items, key=lambda item: (ORDER.index(item.kind), item.name)))
    return CleanupPlan(plan_token(ordered), ordered, tuple(kept))
