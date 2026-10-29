# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The thread that keeps the central's GitHub sources: what it does on each turn and what never stops it.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from noust.core.exceptions import NoustError
from noust.fleet.aggregate import Failure
from noust.fleet.sources.keeper import LOOK_EVERY
from noust.fleet.sources.worker import BUSY_EVERY, IDLE_EVERY, SourcesWorker


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Part:
    """A stand-in for the keeper, the forwarder or the follower: it counts what it was asked."""

    def __init__(self, fail: Exception | None = None, busy: Any = 0) -> None:
        self.calls = 0
        self.fail = fail
        self.busy = busy

    def sweep(self) -> list[Any]:
        return self._call()

    def deliver_due(self) -> list[Any]:
        return self._call()

    def step(self) -> list[Any]:
        return self._call()

    def _call(self) -> list[Any]:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return []

    def pending(self) -> int:
        return int(self.busy)

    def active(self) -> list[Any]:
        return [object()] * int(self.busy)


class Forwarder(Part):
    def __init__(self, fail: Exception | None = None, busy: Any = 0) -> None:
        super().__init__(fail, busy)
        self.forwards = self
        self.purged = 0

    def purge(self) -> None:
        self.purged += 1


class Follower(Part):
    def __init__(self, fail: Exception | None = None, busy: Any = 0) -> None:
        super().__init__(fail, busy)
        self.follows = self
        self.purged = 0

    def purge(self) -> None:
        self.purged += 1


def worker(keeper: Part, forwarder: Forwarder, follower: Follower, clock: Clock) -> SourcesWorker:
    return SourcesWorker(keeper, forwarder, follower, clock)  # type: ignore[arg-type]


class TestATurn:
    def test_the_leases_are_looked_at_once_a_minute_and_the_rest_at_every_turn(self) -> None:
        clock, keeper, forwarder, follower = Clock(), Part(), Forwarder(), Follower()
        w = worker(keeper, forwarder, follower, clock)

        w.tick()
        clock.now += LOOK_EVERY - 1
        w.tick()
        clock.now += 2
        w.tick()

        assert keeper.calls == 2
        assert forwarder.calls == 3 and follower.calls == 3

    @pytest.mark.parametrize(
        "failure",
        [NoustError("x"), Failure("unreachable", "down"), OSError("disk"), RuntimeError("x")],
    )
    def test_a_part_that_fails_does_not_keep_the_others_from_being_done(
        self, failure: Exception, caplog: pytest.LogCaptureFixture
    ) -> None:
        clock, keeper, forwarder, follower = Clock(), Part(fail=failure), Forwarder(), Follower()
        w = worker(keeper, forwarder, follower, clock)

        w.tick()

        assert keeper.calls == 1 and forwarder.calls == 1 and follower.calls == 1
        assert "leases failed" in caplog.text

    def test_what_was_settled_long_ago_is_forgotten_once_an_hour(self) -> None:
        clock, forwarder, follower = Clock(), Forwarder(), Follower()
        w = worker(Part(), forwarder, follower, clock)

        w.tick()
        w.tick()
        clock.now += 3601
        w.tick()

        assert forwarder.purged == 2 and follower.purged == 2

    def test_it_is_busy_while_a_delivery_waits_or_a_deployment_is_followed(self) -> None:
        clock = Clock()

        assert not worker(Part(), Forwarder(), Follower(), clock).busy()
        assert worker(Part(), Forwarder(busy=1), Follower(), clock).busy()
        assert worker(Part(), Forwarder(), Follower(busy=2), clock).busy()

    def test_it_looks_sooner_while_it_is_busy(self) -> None:
        assert BUSY_EVERY < IDLE_EVERY


class TestTheThread:
    def test_waking_it_looks_now_and_stopping_it_ends_the_thread(self) -> None:
        looked = threading.Event()

        class Looking(Forwarder):
            def deliver_due(self) -> list[Any]:
                looked.set()
                return super().deliver_due()

        clock, keeper, follower = Clock(), Part(), Follower()
        w = worker(keeper, Looking(), follower, clock)

        w.start()
        assert looked.wait(5)
        looked.clear()
        w.wake()
        assert looked.wait(5)
        w.stop()

        assert w._thread is None

    def test_starting_twice_starts_one(self) -> None:
        w = worker(Part(), Forwarder(), Follower(), Clock())

        w.start()
        first = w._thread
        w.start()

        assert w._thread is first
        w.stop()
