# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The services a monitor watch is lent in a test, and what it published.

A watch is driven with a controlled clock, a real store for what it remembers and fakes for what
it looks at, so nothing touches the machine. Shared by the tests of the watches the network and
monitoring lane added.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from noust.core.notification_state import NotificationState
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.monitor.watches import WatchServices

NOW = 1_800_000_000.0


class Settings:
    """A configuration with ``get``, answering what a test set and the default otherwise."""

    def __init__(self, **values: Any) -> None:
        self.values = {key.replace("__", "."): value for key, value in values.items()}

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


class Rig:
    """The services a watch is lent, the clock it reads and the notifications it published."""

    def __init__(self, tmp_path: Path, **settings: Any) -> None:
        NoustStore.reset_instance()
        self.store = NoustStore(tmp_path / "noust.db")
        self.now = NOW
        self.sent: list[Notification] = []
        self.runner = FakeRunner()
        self.context = NotificationContext(locale="en", server="web-1", public_url="")
        self.services = WatchServices(
            publish=lambda build: self.sent.append(build(self.context)),
            state=NotificationState(self.store, clock=lambda: self.now),
            runner=self.runner,
            config=Settings(**settings),  # type: ignore[arg-type]
            clock=lambda: self.now,
        )

    def close(self) -> None:
        NoustStore.reset_instance()

    def codes(self) -> list[str]:
        """Returns: The codes of what was published, in order."""
        return [item.code for item in self.sent]
