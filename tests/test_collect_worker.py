# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The thread that keeps the central's copies up to date (item 195).

What is pinned is when it looks and when it does not: a server is looked at when it is followed and
due, never twice at once, less and less often while it fails, at once when somebody asks, and not at
all while the central cannot open a tunnel; one pass that blows up takes nothing else with it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NodeRecord, NoustStore
from noust.fleet.collect.collector import PassResult
from noust.fleet.collect.records import NodeStates
from noust.fleet.collect.settings import Follows, SettingsStore
from noust.fleet.collect.worker import MAX_BACKOFF, CollectWorker


class FakeCollector:
    """Records the passes it was asked to make."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.states: dict[str, str] = {}
        self.blow_up: set[str] = set()
        self.gate: threading.Event | None = None

    def collect(self, node: str, *, force: bool = False) -> PassResult:
        self.calls.append((node, force))
        if self.gate is not None:
            self.gate.wait(5)
        if node in self.blow_up:
            raise RuntimeError("a bug")
        return PassResult(node, self.states.get(node, "ok"))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for name in ("web-1", "web-2", "web-3"):
        instance.save_node(
            NodeRecord(
                name=name,
                ssh_host=f"{name}.example.com",
                ssh_port=22,
                ssh_user="noust-tunnel",
                host_key=f"noust-node-{name} ssh-ed25519 AAAA",
                console_port=8080,
            )
        )
    SettingsStore(instance).set(enabled=True, poll_seconds=60)
    yield instance
    NoustStore.reset_instance()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(store: NoustStore, collector: FakeCollector, clock: Clock, **kw: Any) -> CollectWorker:
    return CollectWorker(
        collector,  # type: ignore[arg-type]
        store=store,
        clock=clock,
        reachable=kw.pop("reachable", lambda _s: True),
    )


def wait_for(condition: Callable[[], bool], seconds: float = 5.0) -> None:
    """Wait for what a test asserts, up to a ceiling, and not for a time."""
    gate = threading.Event()
    for _ in range(int(seconds / 0.01)):
        if condition():
            return
        gate.wait(0.01)


def finish(worker: CollectWorker) -> None:
    """Wait for every pass to end, whether it returned or raised (a pass that raised is a defect the worker logs)."""
    for future in list(worker._running.values()):
        future.exception(10)


class TestWhenItLooks:
    def test_every_followed_server_is_looked_at_and_none_that_is_not(
        self, store: NoustStore
    ) -> None:
        Follows(store).set_follow("web-2", False)
        collector, clock = FakeCollector(), Clock()
        worker = make(store, collector, clock)

        started = worker.tick()
        finish(worker)

        assert started == 2
        assert sorted(node for node, _force in collector.calls) == ["web-1", "web-3"]

    def test_nothing_is_looked_at_when_the_fleet_is_not_followed(self, store: NoustStore) -> None:
        SettingsStore(store).set(enabled=False)
        collector = FakeCollector()

        assert make(store, collector, Clock()).tick() == 0

    def test_not_again_before_the_poll_time_and_again_after(self, store: NoustStore) -> None:
        collector, clock = FakeCollector(), Clock()
        worker = make(store, collector, clock)
        worker.tick()
        finish(worker)

        clock.now += 30
        assert worker.tick() == 0
        clock.now += 31
        assert worker.tick() == 3

    def test_never_twice_at_once_over_the_same_server(self, store: NoustStore) -> None:
        collector, clock = FakeCollector(), Clock()
        collector.gate = threading.Event()
        worker = make(store, collector, clock)
        worker.tick()
        clock.now += 1000

        again = worker.tick()
        collector.gate.set()
        finish(worker)

        assert again == 0 and len(collector.calls) == 3

    def test_nothing_while_the_central_cannot_open_a_tunnel(self, store: NoustStore) -> None:
        collector = FakeCollector()

        assert make(store, collector, Clock(), reachable=lambda _s: False).tick() == 0
        assert collector.calls == []


class TestAServerThatKeepsFailing:
    def test_it_is_looked_at_less_and_less_often_up_to_a_limit(self, store: NoustStore) -> None:
        collector, clock = FakeCollector(), Clock()
        collector.states["web-1"] = "failed"
        worker = make(store, collector, clock)
        for failures in (1, 2, 3, 20):
            NodeStates(store).record_pass("web-1", ok=False, error="x", code="unreachable")
            worker._running.clear()
            worker._next.clear()
            worker.tick()
            finish(worker)
            gap = worker._next["web-1"] - clock.now
            assert gap == min(60.0 * 2 ** min(failures, 6), MAX_BACKOFF), failures

    def test_a_server_that_works_goes_back_to_the_poll_time(self, store: NoustStore) -> None:
        collector, clock = FakeCollector(), Clock()
        worker = make(store, collector, clock)
        worker.tick()
        finish(worker)

        assert worker._next["web-1"] - clock.now == 60.0


class TestWhenSomebodyAsks:
    def test_a_wake_makes_a_server_due_now(self, store: NoustStore) -> None:
        collector, clock = FakeCollector(), Clock()
        worker = make(store, collector, clock)
        worker.tick()
        finish(worker)
        collector.calls.clear()

        worker.wake("web-2")
        worker.tick()
        finish(worker)

        assert collector.calls == [("web-2", False)]

    def test_a_forced_one_reads_the_whole_list(self, store: NoustStore) -> None:
        collector, clock = FakeCollector(), Clock()
        worker = make(store, collector, clock)

        worker.wake("web-3", force=True)
        worker.tick()
        finish(worker)

        assert ("web-3", True) in collector.calls and ("web-1", False) in collector.calls
        collector.calls.clear()
        clock.now += 100
        worker.tick()
        finish(worker)
        assert ("web-3", False) in collector.calls


class TestABugInOnePass:
    def test_it_is_logged_and_the_other_servers_are_still_looked_at(
        self, store: NoustStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        collector, clock = FakeCollector(), Clock()
        collector.blow_up.add("web-1")
        worker = make(store, collector, clock)

        worker.tick()
        finish(worker)

        assert sorted(node for node, _f in collector.calls) == ["web-1", "web-2", "web-3"]
        # The line is written by the future's callback, on the pass's own thread just after the pass ends.
        wait_for(lambda: "A pass of collecting over web-1 failed" in caplog.text)
        assert "A pass of collecting over web-1 failed" in caplog.text
        # What it logs is the defect itself, with its traceback, not a sentence that hides it.
        assert "RuntimeError: a bug" in caplog.text

    def test_the_server_it_hit_backs_off_like_one_that_failed(self, store: NoustStore) -> None:
        collector, clock = FakeCollector(), Clock()
        collector.blow_up.add("web-1")
        worker = make(store, collector, clock)
        NodeStates(store).record_pass("web-1", ok=False, error="x", code="error")

        worker.tick()
        finish(worker)

        assert worker._next["web-1"] > worker._next["web-2"]


class TestTheThread:
    def test_it_starts_looks_and_stops(self, store: NoustStore) -> None:
        collector, clock = FakeCollector(), Clock()
        worker = make(store, collector, clock)

        worker.start()
        worker.start()
        deadline = threading.Event()
        for _ in range(100):
            if len(collector.calls) >= 3:
                break
            deadline.wait(0.05)
        worker.stop()

        assert len(collector.calls) >= 3
        assert worker._thread is None

    def test_what_the_disk_or_the_store_refuses_is_logged_and_the_next_look_goes_on(
        self, store: NoustStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        worker = make(store, FakeCollector(), Clock())
        looks: list[int] = []

        def tick() -> int:
            looks.append(1)
            if len(looks) == 1:
                raise OSError("No space left on device")
            return 0

        worker.tick = tick  # type: ignore[method-assign]
        worker.start()
        deadline = threading.Event()
        for _ in range(200):
            worker.wake()
            if len(looks) >= 2:
                break
            deadline.wait(0.05)
        worker.stop()

        assert len(looks) >= 2
        assert "Collecting could not look at the servers" in caplog.text

    def test_a_defect_ends_the_thread_loudly_and_the_next_wake_starts_it_again(
        self, store: NoustStore
    ) -> None:
        worker = make(store, FakeCollector(), Clock())
        looks: list[int] = []

        def tick() -> int:
            looks.append(1)
            if len(looks) == 1:
                raise AttributeError("a defect")
            return 0

        worker.tick = tick  # type: ignore[method-assign]
        thread_reports: list[BaseException] = []
        original = threading.excepthook
        threading.excepthook = lambda args: thread_reports.append(args.exc_value)  # type: ignore[arg-type]
        try:
            worker.start()
            first = worker._thread
            assert first is not None
            first.join(5)
            assert not first.is_alive()
            worker.wake()
            deadline = threading.Event()
            for _ in range(200):
                if len(looks) >= 2:
                    break
                deadline.wait(0.05)
            worker.stop()
        finally:
            threading.excepthook = original

        assert [type(report).__name__ for report in thread_reports] == ["AttributeError"]
        assert len(looks) >= 2
