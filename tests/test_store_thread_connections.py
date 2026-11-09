# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A store keeps one SQLite connection per thread alive, and none for a thread that has ended.

A central's console reached its limit of 1024 open files in under three days: the
console's worker threads end after ten idle seconds, and each one left its
connection to the store and to the metrics open until a full garbage collection,
which a large heap makes rare. These tests turn the collector off, which is the
worst case, and count the files the process really has open.
"""

from __future__ import annotations

import gc
import os
import sqlite3
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from noust.core.fs import RecordingFileSystem
from noust.core.sqlite_threads import ThreadConnections
from noust.core.store import NoustStore
from noust.monitor.observation_store import ObservationStore
from noust.monitor.timeseries import MetricsStore

THREADS = 40

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="counts open files in /proc/self/fd"
)


def _open_files(path: Path) -> int:
    """Count this process's descriptors open on ``path`` itself (not its -wal or -shm)."""
    target = str(path.resolve())
    count = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            if os.readlink(f"/proc/self/fd/{fd}") == target:
                count += 1
        except OSError:
            continue
    return count


def _in_ended_threads(work: Callable[[], object], count: int = THREADS) -> None:
    """Run ``work`` once in each of ``count`` threads, one after another, each ending before the next."""
    for _ in range(count):
        thread = threading.Thread(target=work)
        thread.start()
        thread.join()


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
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store singleton this test owns, on a file of its own."""
    NoustStore.reset_instance()
    yield NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
    NoustStore.reset_instance()


def _files_after_ended_threads(path: Path, work: Callable[[], object]) -> tuple[int, int]:
    """
    Count the files open on ``path`` after a few ended threads, and after many more.

    SQLite keeps one descriptor of a closed connection while another connection
    of the process holds a lock on the same file (POSIX locks belong to the
    process, and closing any descriptor would drop them all) and reuses it for
    the next one it opens, so the count settles one above the connections
    alive. What matters is that it settles.
    """
    _in_ended_threads(work, count=3)
    settled = _open_files(path)
    _in_ended_threads(work)
    return settled, _open_files(path)


def test_the_store_closes_the_connection_of_a_thread_that_ended(store, collector_off):
    settled, after = _files_after_ended_threads(store.db_path, store.list_apps)

    assert after == settled <= 3
    # Each new thread closes those of the threads before it; what is left is the
    # last thread's and the test's own.
    assert store._connections.open_count() == 2


def test_the_metrics_close_the_connection_of_a_thread_that_ended(tmp_path, collector_off):
    metrics = MetricsStore(tmp_path / "metrics.db", clock=lambda: 1_800_000_000.0)

    settled, after = _files_after_ended_threads(metrics.db_path, metrics.list_metrics)

    assert after == settled <= 3


def test_an_observation_store_that_is_dropped_closes_its_connections(tmp_path, collector_off):
    path = tmp_path / "observations.db"

    # The console makes one per request, on whatever thread answers it.
    for _ in range(THREADS):
        ObservationStore(db_path=path).recent(limit=5)

    assert _open_files(path) <= 1


def test_closing_closes_only_the_calling_threads_connection(store):
    other_ready, release = threading.Event(), threading.Event()

    def hold_one() -> None:
        store.list_apps()
        other_ready.set()
        release.wait(timeout=10)

    other = threading.Thread(target=hold_one)
    other.start()
    try:
        assert other_ready.wait(timeout=10)
        store.list_apps()
        assert store._connections.open_count() == 2

        store.close()

        assert store._connections.open_count() == 1
        # The next use opens a new one, and the store still answers.
        assert store.list_apps() == []
        assert store._connections.open_count() == 2
    finally:
        release.set()
        other.join(timeout=10)


def test_a_connection_is_reused_by_its_own_thread():
    connections = ThreadConnections()
    opened: list[sqlite3.Connection] = []

    def opener() -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        opened.append(connection)
        return connection

    first = connections.get(opener)

    assert connections.get(opener) is first
    assert len(opened) == 1
