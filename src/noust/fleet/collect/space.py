# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The room a central has for copies, and the guard that keeps some of it free (item 195).

A central whose disk is full stops taking copies, says so, and **deletes nothing to make room**:
the copies it holds are what the servers' own were meant to be backed up by, and which of them to let
go is the operator's decision (or the retention they set), never the disk's. The guard is one
function, :meth:`Guard.require`, asked before every copy and again between the pieces of a large one.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import BackupError

__all__ = ["DiskFullError", "Guard", "Space", "space_of"]


class DiskFullError(BackupError):
    """The central has no more room for copies, beyond what it keeps free."""


@dataclass(frozen=True)
class Space:
    """
    The disk the copies are on.

    Attributes:
        total: Its size in bytes.
        free: What is free.
        used: What the collected copies weigh, as recorded.
        min_free: What is always left alone.
    """

    total: int
    free: int
    used: int
    min_free: int

    @property
    def available(self) -> int:
        """Returns: What may still be taken: the free room less what is always left."""
        return max(0, self.free - self.min_free)

    @property
    def low(self) -> bool:
        """Returns: Whether less than a tenth of the disk, or the room kept free, is left."""
        return self.free < max(self.min_free, self.total // 10) if self.total else False

    def to_dict(self) -> dict[str, int | bool]:
        """
        Returns:
            The space, JSON-ready.
        """
        return {
            "total": self.total,
            "free": self.free,
            "used": self.used,
            "min_free": self.min_free,
            "available": self.available,
            "low": self.low,
        }


def space_of(path: Path, *, used: int = 0, min_free: int = 0) -> Space:
    """
    Measure the disk a folder is on, at its nearest existing parent: where its first file will land.

    Args:
        path: The folder.
        used: What the collected copies weigh.
        min_free: What is always left alone.

    Returns:
        The space; zeros when no parent can be measured.
    """
    for candidate in (path, *path.parents):
        try:
            if not candidate.exists():
                continue
            usage = shutil.disk_usage(candidate)
        except OSError:
            break
        return Space(usage.total, usage.free, used, min_free)
    return Space(0, 0, used, min_free)


class Guard:
    """
    Refuse to take what would leave less room than the operator said to keep.

    Args:
        root: The folder the copies are in.
        min_free: The room always left, in bytes.
        measure: Measures the disk (tests replace it).
    """

    def __init__(
        self, root: Path, min_free: int, *, measure: Callable[..., Space] = space_of
    ) -> None:
        self.root = root
        self.min_free = min_free
        self._measure = measure

    def require(self, needed: int) -> None:
        """
        Check there is room for more bytes.

        Args:
            needed: Bytes about to be written, at most.

        Raises:
            DiskFullError: There is not, and says how much is and what to do. Nothing is deleted.
        """
        space = self._measure(self.root, min_free=self.min_free)
        if space.total and needed > space.available:
            raise DiskFullError(
                f"The central's disk has no room for {needed} more bytes "
                f"({space.free} free, {self.min_free} kept free)",
                details="Nothing was deleted to make room. Free some space, let the central's "
                "retention go further (Settings > Backups), or move the folder of the copies "
                "to a bigger disk; collecting goes on by itself afterwards.",
            )
