# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central tells its operator: its own events and nothing a server says for itself.

Each server delivers its own notifications through its own channels (its deploys, its disk, its
certificates, its alerts), so they keep arriving when the central is down or the line is cut. The
central says three things, about the servers it manages: one stopped answering, one answers again,
one presented another SSH host key. If the central also relayed what its servers raise, the
operator would hear everything twice, once from the server and once from the central, and a
channel sent to both would double. A central that collects its servers' copies says one thing
about its own work, which no server can say: it could not take them. What is pinned: the three, the
one, and that nothing under the fleet's code reaches the notification pipeline by any other door.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from noust.core.notifications import fleet as central_events
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import EVENT_KINDS, Notification

FLEET = Path(central_events.__file__).parents[2] / "fleet"

#: What the fleet's code may import from the notification pipeline: the central's own three, and
#: the pure routing of a channel (a fleet route is judged by the rule a server's own route has).
ALLOWED = {"noust.core.notifications.fleet", "noust.core.notifications.routing"}


def test_the_central_says_three_things() -> None:
    assert central_events.CENTRAL_KINDS == (
        "node_unreachable",
        "node_recovered",
        "node_host_key_changed",
    )
    assert set(central_events.CENTRAL_KINDS) <= set(EVENT_KINDS)


@pytest.fixture
def composed(monkeypatch: pytest.MonkeyPatch) -> list[Notification]:
    made: list[Notification] = []

    def deliver(compose: Callable[[NotificationContext], Notification]) -> None:
        made.append(compose(NotificationContext(server="central")))

    monkeypatch.setattr(central_events, "notify_composed", deliver)
    return made


def test_each_of_them_is_one_of_the_three_and_about_a_server(composed: list[Notification]) -> None:
    central_events.notify_node_unreachable("web-2", reason="timeout", address="10.0.0.2")
    central_events.notify_node_recovered("web-2", down_for_s=90.0)
    central_events.notify_node_host_key_changed("web-2", pinned="SHA256:a", presented="SHA256:b")

    assert tuple(n.kind for n in composed) == central_events.CENTRAL_KINDS
    for notification in composed:
        assert "web-2" in notification.subject or "web-2" in notification.summary
        # Said by the central, about a server: not an application's event.
        assert notification.domain is None


def test_a_collection_that_failed_is_said_as_a_backup_that_did_not_complete(
    composed: list[Notification],
) -> None:
    central_events.notify_collect_failed("web-2", "The disk of the central is full")

    (notification,) = composed
    assert notification.kind == central_events.COLLECT_KIND == "backup_failed"
    assert notification.kind not in central_events.CENTRAL_KINDS
    # The central's own work, about a server: not an application's backup.
    assert notification.domain is None
    assert (notification.code, notification.subject) == ("backup.collect_failed", "web-2")
    assert notification.excerpt is not None
    assert notification.excerpt.lines == ("The disk of the central is full",)


def _imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            names: list[Any] = [alias.name for alias in node.names]
            found.add(module)
            found.update(f"{module}.{name}" for name in names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_nothing_in_the_fleets_code_speaks_to_the_channels_by_another_door() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(FLEET.rglob("*.py")):
        doors = sorted(
            name
            for name in _imports_of(path)
            if name.startswith(("noust.core.notifier", "noust.core.notifications"))
            and not any(name == ok or name.startswith(ok + ".") for ok in ALLOWED)
            and name not in {"noust.core.notifications"}
        )
        if doors:
            offenders[str(path.relative_to(FLEET))] = doors

    assert offenders == {}
