# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the collector is taking right now, for the console to show (item 195).

An in-memory board: a transfer is something that is happening in this process, and a console that
restarted has nothing to show until the next pass begins, which is the truth. The collector writes
it (one entry per server, while a pass runs), the API reads it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

__all__ = ["Board", "Moving", "get_board"]


@dataclass
class Moving:
    """
    A pass that is running over a server.

    Attributes:
        node: The server.
        started_at: When the pass began, epoch seconds.
        item: The copy being taken, as ``<kind>: <owner>`` for a person to read.
        file: The file of it.
        done: Bytes of that file taken.
        size: Its size.
        taken: Copies this pass has finished.
        todo: Copies this pass set out to take.
    """

    node: str
    started_at: float
    item: str = ""
    file: str = ""
    done: int = 0
    size: int = 0
    taken: int = 0
    todo: int = 0


class Board:
    """What is moving, by server."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._moving: dict[str, Moving] = {}

    def begin(self, node: str) -> None:
        """
        Note that a pass over a server began.

        Args:
            node: The server.
        """
        with self._lock:
            self._moving[node] = Moving(node, time.time())

    def update(self, node: str, **fields: Any) -> None:
        """
        Change what a pass is doing.

        Args:
            node: The server.
            **fields: Attributes of :class:`Moving`.
        """
        with self._lock:
            entry = self._moving.get(node)
            if entry is not None:
                for key, value in fields.items():
                    setattr(entry, key, value)

    def end(self, node: str) -> None:
        """
        Note that a pass ended.

        Args:
            node: The server.
        """
        with self._lock:
            self._moving.pop(node, None)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """
        Returns:
            What is moving, by server, as plain data.
        """
        with self._lock:
            return {node: asdict(entry) for node, entry in self._moving.items()}


_board = Board()


def get_board() -> Board:
    """
    Returns:
        The process-wide board.
    """
    return _board
