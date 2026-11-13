# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether the built-in assistant is on, read without loading any of it (item 171).

The console asks this on every sign-in (the session says whether to draw the assistant at all), and
a feature that is off must cost nothing there: this module imports nothing of the assistant, not
its client, its settings or its limits, and reads a few facts out of the one row. Everything that
needs more than that goes through :class:`noust.core.assistant.settings.AssistantSettings`.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = ["State", "read_state"]

#: The places that are the operator's own: nothing a conversation says leaves their side.
_OWN_SIDE = frozenset({"machine", "network"})


class State(NamedTuple):
    """
    What the session needs to know of the assistant.

    Attributes:
        enabled: It is on.
        calls_tools: Its model was found to call tools, so it may investigate.
        model: The model's name; empty when none was chosen.
        location: Where what is said to it ends up (``machine``, ``network``, ``eu``,
            ``outside`` or ``unknown``).
        mask_personal: Emails and addresses are disguised before they are sent: always, but for a
            provider on the operator's side that they chose not to disguise.
    """

    enabled: bool = False
    calls_tools: bool = False
    model: str = ""
    location: str = "unknown"
    mask_personal: bool = True


def read_state(store: NoustStore | None = None) -> State:
    """
    Say whether the assistant is on, and what a person using it should know of it.

    Args:
        store: The store; the process-wide one when None.

    Returns:
        The state. All defaults (off) when nothing was ever configured, and when the store has no
        such table yet (a store that is being upgraded).
    """
    if store is None:
        from noust.core.store import get_store

        store = get_store()
    try:
        with store._transaction() as cursor:
            cursor.execute(
                "SELECT enabled, probe, model, location, mask_personal "
                "FROM assistant_settings WHERE id = 1"
            )
            row = cursor.fetchone()
    except sqlite3.OperationalError:
        return State()
    if row is None:
        return State()
    try:
        probe = json.loads(row["probe"] or "{}")
    except ValueError:
        probe = {}
    return State(
        enabled=bool(row["enabled"]),
        calls_tools=isinstance(probe, dict) and probe.get("tools") is True,
        model=row["model"],
        location=row["location"],
        mask_personal=bool(row["mask_personal"]) or row["location"] not in _OWN_SIDE,
    )
