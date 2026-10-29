# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's slow look at the servers a route can reach now.

A channel routed by domain or tag reaches a server once an application there can match it, and an
application is deployed on the server without the central being told. So, now and then, the
watcher asks where each setting stands and queues the servers a channel could now be sent to and
was not. What is pinned: nobody is asked when no route depends on the applications; only a server
whose route can match now and which has not got the setting is queued; the look is slow and does
not repeat a server it just tried; and a failure of the look is the loop's to log, never the look's
to hide.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.exceptions import NoustError
from noust.core.store import NoustStore
from noust.fleet.settings.desired import FleetSettings
from noust.web.fleet_settings_actions import REACH_EVERY, RETRY_AFTER, SettingsWatcher

HELD = frozenset({"settings.manage"})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def cell(node: str, sync: str, reaches: bool | None) -> dict[str, Any]:
    return {"node": node, "sync": sync, "reach": {"reaches": reaches, "why": None}}


class Rig:
    def __init__(self, store: NoustStore) -> None:
        self.settings = FleetSettings(store)
        self.clock = Clock()
        self.queued: list[list[str]] = []
        self.cells: list[dict[str, Any]] = []
        self.looked = 0
        self.watcher = SettingsWatcher(
            queue=lambda nodes, **kw: self.queued.append(list(nodes)),
            clock=self.clock,
            settings=lambda: self.settings,
            read=self.read,
        )

    def read(self, settings: FleetSettings) -> dict[str, Any]:
        self.looked += 1
        return {"settings": [{"resource": "channel", "name": "ops", "servers": self.cells}]}

    def route(self, **route: Any) -> None:
        self.settings.save(
            "channel",
            "ops",
            {"type": "slack", "route": route} if route else {"type": "slack"},
            person="carol",
            held=HELD,
        )


@pytest.fixture
def rig(store: NoustStore) -> Rig:
    from noust.core.store import NodeRecord

    for name in ("web-1", "web-2", "web-3"):
        store.save_node(
            NodeRecord(
                name=name, ssh_host=name, ssh_port=22, ssh_user="u", host_key="k", console_port=1
            )
        )
    return Rig(store)


def test_nobody_is_asked_when_no_route_depends_on_the_applications(rig: Rig) -> None:
    rig.route(servers=["web-1"], min_severity="failed")

    assert rig.watcher.look_reach() == []
    assert rig.looked == 0


def test_a_server_where_the_route_can_match_now_and_has_not_got_it_is_queued(rig: Rig) -> None:
    rig.route(apps=["*.example.com"])
    rig.cells = [
        cell("web-1", "in_sync", True),
        cell("web-2", "pending", True),
        cell("web-3", "pending", False),
    ]

    assert rig.watcher.look_reach() == ["web-2"]
    assert rig.queued == [["web-2"]]


def test_a_server_whose_applications_are_not_known_is_not_queued(rig: Rig) -> None:
    rig.route(apps=["*.example.com"])
    rig.cells = [cell("web-1", "pending", None)]

    assert rig.watcher.look_reach() == []
    assert rig.queued == []


def test_the_look_is_slow(rig: Rig) -> None:
    rig.route(apps=["*.example.com"])
    rig.cells = [cell("web-2", "pending", True)]
    rig.watcher.look_reach()

    rig.clock.now += REACH_EVERY - 1
    assert rig.watcher.look_reach() == []
    assert rig.looked == 1

    rig.clock.now += 2
    rig.watcher.look_reach()
    assert rig.looked == 2


def test_a_server_it_just_tried_is_not_queued_again_before_its_time(rig: Rig) -> None:
    rig.route(apps=["*.example.com"])
    rig.cells = [cell("web-2", "pending", True)]
    rig.watcher.look_reach()

    rig.clock.now += REACH_EVERY
    assert RETRY_AFTER < REACH_EVERY  # the look is slower than the retry, so it may try again
    assert rig.watcher.look_reach() == ["web-2"]
    assert rig.queued == [["web-2"], ["web-2"]]


def test_a_server_the_other_look_just_queued_is_not_queued_twice(rig: Rig) -> None:
    rig.route(apps=["*.example.com"])
    rig.cells = [cell("web-2", "pending", True)]
    rig.settings.request_resync("web-2")
    assert rig.watcher.look() == ["web-2"]

    assert rig.watcher.look_reach() == []
    assert rig.queued == [["web-2"]]


def test_a_failed_look_is_raised_for_the_loop_to_log(rig: Rig) -> None:
    rig.route(apps=["*.example.com"])

    def broken(settings: FleetSettings) -> dict[str, Any]:
        raise NoustError("this central is locked")

    rig.watcher._read = broken

    with pytest.raises(NoustError):
        rig.watcher.look_reach()
