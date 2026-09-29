# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tests for :mod:`noust.core.background`.

Defended: tasks run in submission order on one thread (a notification's
"failed" never overtakes its "Deploying"), submitting never waits for the
work, the exit drain waits for work already queued but never past its cap,
and a task that raises does not strand what was queued behind it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from noust.core import background
from noust.core.background import BackgroundQueue


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep this module's queues out of the process-wide exit drain."""
    monkeypatch.setattr(background, "_queues", [])
    monkeypatch.setattr(background, "_atexit_registered", False)
    monkeypatch.setattr("atexit.register", lambda fn: fn)
    yield


def test_tasks_run_in_submission_order_on_one_thread() -> None:
    queue = BackgroundQueue("test-order")
    seen: list[tuple[int, str]] = []

    def task(n: int, delay: float) -> Any:
        def run() -> None:
            time.sleep(delay)
            seen.append((n, threading.current_thread().name))

        return run

    # The first is the slowest: with a thread per task it would finish last.
    queue.submit(task(1, 0.1))
    queue.submit(task(2, 0.0))
    queue.submit(task(3, 0.0))
    assert queue.drain(timeout=5)

    assert [n for n, _ in seen] == [1, 2, 3]
    assert {name for _, name in seen} == {"test-order"}


def test_submit_returns_before_the_task_runs() -> None:
    queue = BackgroundQueue("test-async")
    release = threading.Event()
    queue.submit(lambda: release.wait(5))

    started = time.perf_counter()
    queue.submit(lambda: None)
    assert time.perf_counter() - started < 0.05
    assert queue.pending == 2

    release.set()
    assert queue.drain(timeout=5)
    assert queue.pending == 0


def test_drain_gives_up_at_its_cap() -> None:
    queue = BackgroundQueue("test-cap")
    release = threading.Event()
    queue.submit(lambda: release.wait(5))

    started = time.perf_counter()
    assert queue.drain(timeout=0.05) is False
    assert time.perf_counter() - started < 1.0
    release.set()
    assert queue.drain(timeout=5)


def test_drain_all_shares_one_budget_across_queues() -> None:
    release = threading.Event()
    first, second = BackgroundQueue("test-a"), BackgroundQueue("test-b")
    first.submit(lambda: release.wait(5))
    second.submit(lambda: release.wait(5))

    started = time.perf_counter()
    assert background.drain_all(timeout=0.1) is False
    assert time.perf_counter() - started < 1.0
    release.set()
    assert background.drain_all(timeout=5)


def test_the_first_submission_registers_the_exit_drain_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered: list[Any] = []
    monkeypatch.setattr("atexit.register", registered.append)
    queue = BackgroundQueue("test-atexit")

    queue.submit(lambda: None)
    queue.submit(lambda: None)
    BackgroundQueue("test-atexit-2").submit(lambda: None)
    background.drain_all(timeout=5)

    assert registered == [background.drain_all]


def test_a_task_that_raises_does_not_strand_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    hooked: list[BaseException | None] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: hooked.append(args.exc_value))
    queue = BackgroundQueue("test-raise")
    ran: list[str] = []

    def broken() -> None:
        raise RuntimeError("a bug")

    queue.submit(broken)
    queue.submit(lambda: ran.append("after"))

    assert queue.drain(timeout=5)
    assert ran == ["after"]
    assert any(isinstance(exc, RuntimeError) for exc in hooked)
