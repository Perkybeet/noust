# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's API does not leave a file open for every worker thread that comes and goes (issue 19).

A central's console reached its limit of 1024 open files and answered every request, its own pages
included, with "Too many open files". ``tests/test_store_thread_connections.py`` holds the stores
to closing the connection of a thread that has ended; this is the same thing as the operator met
it: the API under requests, with the cyclic collector off (the worst case, since a large heap makes
a full collection rare) and worker threads that end after a moment and are replaced, counting the
files the process really holds. The routes are the three the issue names: ``/api/overview``,
``/api/timeline`` and the observations.

A measurement that never sees a leak proves nothing, so the same run is made once with the stores'
cleanup of ended threads taken out, and it must grow.
"""

from __future__ import annotations

import gc
import os
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import sqlite_threads
from noust.core.runner import FakeRunner
from noust.web.server import create_app, get_token_manager
from tests.support.web_auth import bearer, make_config

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="counts open files in /proc/self/fd"
)

#: Requests in flight at once, and rounds of them: each round is a burst, then a pause long enough for
#: every idle worker to end, so the next burst starts new ones.
CONCURRENCY = 6
ROUNDS = 14
#: How long a worker thread stays alive while idle, in seconds, instead of anyio's ten.
IDLE = 0.05
#: What a process may hold more than it did before: a connection per worker that ended since the last
#: one opened its own (a burst of six workers over three stores is eighteen), and the sockets that come
#: and go. A leak is a connection per worker per round, which is hundreds.
SLACK = 40


def _open_files() -> int:
    """Count the descriptors this process has open."""
    return len(os.listdir("/proc/self/fd"))


@pytest.fixture
def collector_off() -> Iterator[None]:
    """Keep the cyclic collector from closing the connections in the tests' place."""
    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


@pytest.fixture
def quick_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a worker thread of the server end after a moment, as the console's do after ten seconds."""
    from anyio._backends import _asyncio as backend

    monkeypatch.setattr(backend.WorkerThread, "MAX_IDLE_TIME", IDLE)


@pytest.fixture
def app(sandbox: Path, runner: FakeRunner) -> FastAPI:
    """The real console application, over a store of its own and a machine that answers nothing."""
    return create_app(make_config(sandbox))


def _paths() -> list[str]:
    now = int(time.time())
    return [
        "/api/overview",
        f"/api/timeline?start={now - 3600}&end={now}",
        "/api/monitor/observations",
    ]


def _burst(app: FastAPI, headers: dict[str, str]) -> list[int]:
    """Ask for each route a few times at once, each request from a client of its own."""
    paths = _paths() * 2

    def ask(path: str) -> int:
        return TestClient(app, client=("testclient", 50000)).get(path, headers=headers).status_code

    with ThreadPoolExecutor(CONCURRENCY) as pool:
        return list(pool.map(ask, paths))


def _grown_by(app: FastAPI) -> int:
    """Make the rounds and say how many files the process holds more than before them."""
    headers = bearer(get_token_manager().generate_master_token())
    # Once first, one after another and then all at once: the first request of each route creates what
    # every one after it reuses, and a burst leaves a connection per thread that ended since the last
    # one opened its own, which is bounded and not what grows.
    client = TestClient(app, client=("testclient", 50000))
    for path in _paths():
        assert client.get(path, headers=headers).status_code == 200, path
    assert set(_burst(app, headers)) == {200}
    time.sleep(IDLE * 4)
    before = _open_files()
    for _ in range(ROUNDS):
        answered = _burst(app, headers)
        assert set(answered) == {200}, answered
        time.sleep(IDLE * 4)
    return _open_files() - before


def test_requests_served_by_threads_that_come_and_go_do_not_grow_the_files_a_process_holds(
    app: FastAPI, collector_off: None, quick_workers: None
) -> None:
    grown = _grown_by(app)

    assert grown <= SLACK, f"the process holds {grown} more files after {ROUNDS} rounds of requests"


def test_the_same_run_grows_when_a_store_forgets_the_threads_that_ended(
    app: FastAPI, collector_off: None, quick_workers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The way the stores were before 4.2.1: a connection stays until the collector frees it.
    def forgetful(self: sqlite_threads.ThreadConnections, opener):  # type: ignore[no-untyped-def]
        thread = threading.current_thread()
        connection = self._by_thread.get(thread)
        if connection is None:
            connection = opener()
            with self._lock:
                self._by_thread[thread] = connection
        return connection

    monkeypatch.setattr(sqlite_threads.ThreadConnections, "get", forgetful)

    grown = _grown_by(app)

    assert grown > SLACK, "the measurement did not see a leak that was put there"
