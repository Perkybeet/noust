# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's watcher of the servers that came back.

Whichever process saw a server answer after a silence left a note in the store; the watcher is
the one thing that acts on it, in the console's process, where the jobs run. What is pinned: it
queues the servers that are waiting, all in one job; a server it could not get done is not
retried at every look; and a failure of one look never ends it.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.fleet.settings.desired import FleetSettings
from noust.web.fleet_settings_actions import RETRY_AFTER, SettingsWatcher


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def rig(store: NoustStore) -> tuple[SettingsWatcher, FleetSettings, Clock, list[list[str]]]:
    settings = FleetSettings(store)
    clock = Clock()
    queued: list[list[str]] = []
    watcher = SettingsWatcher(
        queue=lambda nodes, **kw: queued.append(list(nodes)),
        clock=clock,
        settings=lambda: settings,
    )
    return watcher, settings, clock, queued


def test_nothing_waiting_queues_nothing(rig: Any) -> None:
    watcher, _settings, _clock, queued = rig

    assert watcher.look() == []
    assert queued == []


def test_the_servers_that_are_waiting_go_in_one_job(rig: Any) -> None:
    watcher, settings, _clock, queued = rig
    settings.request_resync("web-3")
    settings.request_resync("web-1")

    assert watcher.look() == ["web-1", "web-3"]
    assert queued == [["web-1", "web-3"]]


def test_a_server_the_job_did_not_get_done_is_not_tried_at_every_look(rig: Any) -> None:
    watcher, settings, clock, queued = rig
    settings.request_resync("web-1")
    watcher.look()

    clock.now += RETRY_AFTER - 1
    assert watcher.look() == []

    clock.now += 2
    assert watcher.look() == ["web-1"]
    assert queued == [["web-1"], ["web-1"]]


def test_a_server_whose_turn_is_not_yet_does_not_hold_back_one_that_is_new(rig: Any) -> None:
    watcher, settings, clock, queued = rig
    settings.request_resync("web-1")
    watcher.look()
    clock.now += 10
    settings.request_resync("web-2")

    assert watcher.look() == ["web-2"]


def test_a_server_that_was_done_is_not_asked_about_again(rig: Any) -> None:
    watcher, settings, clock, queued = rig
    settings.request_resync("web-1")
    watcher.look()
    settings.clear_resync("web-1")  # what a job that got it done does

    clock.now += RETRY_AFTER * 2

    assert watcher.look() == []


def test_the_watcher_starts_and_stops(rig: Any) -> None:
    watcher, *_ = rig

    watcher.start()
    watcher.start()
    watcher.stop()
    watcher.stop()


def test_a_look_that_fails_does_not_end_the_watcher(
    store: NoustStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import threading

    from noust.core.exceptions import NoustError

    settings = FleetSettings(store)
    settings.request_resync("web-1")
    calls: list[int] = []
    first, second = threading.Event(), threading.Event()

    def queue(nodes: Any, **kw: Any) -> None:
        calls.append(len(calls))
        if len(calls) == 1:
            first.set()
            raise NoustError("the registry of servers is locked")
        second.set()

    clock = Clock()
    watcher = SettingsWatcher(queue=queue, clock=clock, settings=lambda: settings)
    monkeypatch.setattr("noust.web.fleet_settings_actions.WATCH_EVERY", 0.01)

    watcher.start()
    try:
        assert first.wait(10), "the watcher never looked"
        # The look failed; after the wait for a retry the next one must still happen.
        clock.now += RETRY_AFTER + 1
        assert second.wait(10), "the watcher stopped after a failed look"
    finally:
        watcher.stop()
    assert "The fleet settings watcher failed" in caplog.text
