# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stand-in for the rule a retention asks, to see what each kind of retention asks of it (item 195).

The rule itself is pinned in ``tests/test_backup_hold.py`` against a real store. What the retention
of an application's backups, a database's, a volume's and Noust's own are held to is the other half:
they ask it with their own kind and their own identifiers, leave alone what it names, and say so
when they delete what a central was waiting for.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from noust.core import backup_hold


@dataclass
class Hold:
    """What was asked of the rule, and what it is made to answer."""

    spare: set[str] = field(default_factory=set)
    asked: list[tuple[str, list[str]]] = field(default_factory=list)
    noted: list[tuple[str, str, str]] = field(default_factory=list)

    def spared(
        self, kind: str, copies: Iterable[tuple[str, Any]], *, now: datetime | None = None
    ) -> set[str]:
        ids = [item for item, _ in copies]
        self.asked.append((kind, ids))
        return {item for item in ids if item in self.spare}

    def note(self, kind: str, item_id: str, *, owner: str, now: datetime | None = None) -> None:
        self.noted.append((kind, item_id, owner))

    def rotate_out(
        self,
        delete: Callable[[str], object],
        kind: str,
        item_id: str,
        owner: str,
        *,
        now: datetime | None = None,
    ) -> None:
        delete(item_id)
        self.noted.append((kind, item_id, owner))


@pytest.fixture
def hold(monkeypatch: pytest.MonkeyPatch) -> Hold:
    probe = Hold()
    monkeypatch.setattr(backup_hold, "spared", probe.spared)
    monkeypatch.setattr(backup_hold, "note_rotated_out", probe.note)
    monkeypatch.setattr(backup_hold, "rotate_out", probe.rotate_out)
    return probe
