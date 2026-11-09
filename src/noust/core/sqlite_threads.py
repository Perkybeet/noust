# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The connections a SQLite store keeps, one per thread, closed once that thread has ended.

The store, the metrics and the process observations each keep a connection per
thread, because a ``sqlite3.Connection`` is not meant to be shared by threads
that write at once. They used to keep it in a ``threading.local``, trusting that
the connection is closed when its thread ends and the local is dropped. It is
not: a connection holds its own statement cache and the cache holds the
connection, so only the cyclic garbage collector frees one, and on a console
with a large heap a full collection is rare. The console runs its handlers on
worker threads that end after ten idle seconds and are replaced by new ones, so
every quiet moment left an open file behind per database. A central reached its
limit of 1024 open files in under three days and then answered every request,
the console's own pages included, with "Too many open files".

:class:`ThreadConnections` records which thread each connection belongs to and,
before it opens a new one, closes those whose thread is gone, so a store holds
at most one connection per thread alive. A store that is dropped closes all of
its connections at once: the console creates an :class:`ObservationStore` per
request, and each one used to leave its connection to the collector too.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import weakref
from collections.abc import Callable

logger = logging.getLogger(__name__)


class ThreadConnections:
    """
    One SQLite connection per thread for one store.

    A connection is used only by the thread that opened it, but it may be
    closed by another one once that thread has ended or the store is dropped,
    so the opener given to :meth:`get` must open it with
    ``check_same_thread=False``.
    """

    def __init__(self) -> None:
        """Start with no connection; each thread opens its own on first use."""
        self._lock = threading.Lock()
        self._by_thread: dict[threading.Thread, sqlite3.Connection] = {}
        # The finalizer holds the dictionary and the lock, never the store:
        # holding the store would keep it alive and the finalizer would never run.
        weakref.finalize(self, _close_all, self._by_thread, self._lock)

    def get(self, opener: Callable[[], sqlite3.Connection]) -> sqlite3.Connection:
        """
        Return the calling thread's connection, opening it on first use.

        Args:
            opener: Opens and configures a new connection. It is passed on each
                call, rather than kept, so that this object never refers back to
                the store that owns it.

        Returns:
            The open connection belonging to the calling thread.
        """
        thread = threading.current_thread()
        connection = self._by_thread.get(thread)
        if connection is not None:
            return connection
        # Opened outside the lock: opening can wait on a busy database, and
        # the other threads already have theirs.
        connection = opener()
        with self._lock:
            ended = [other for other in self._by_thread if not other.is_alive()]
            stale = [self._by_thread.pop(other) for other in ended]
            self._by_thread[thread] = connection
        for old in stale:
            _close(old)
        return connection

    def close(self) -> None:
        """Close the calling thread's connection; its next :meth:`get` opens a new one."""
        with self._lock:
            connection = self._by_thread.pop(threading.current_thread(), None)
        if connection is not None:
            _close(connection)

    def open_count(self) -> int:
        """
        Count the connections this store holds.

        Returns:
            How many threads have a connection open.
        """
        with self._lock:
            return len(self._by_thread)


def _close_all(by_thread: dict[threading.Thread, sqlite3.Connection], lock: threading.Lock) -> None:
    """
    Close every connection of a store that has been dropped.

    Args:
        by_thread: The store's connections, by thread.
        lock: The lock that guards them.
    """
    with lock:
        connections = list(by_thread.values())
        by_thread.clear()
    for connection in connections:
        _close(connection)


def _close(connection: sqlite3.Connection) -> None:
    """
    Close a connection that nothing will use again.

    Args:
        connection: The connection to close.
    """
    try:
        connection.close()
    except sqlite3.Error as exc:
        # This is cleanup after a thread or a store that is gone. Nothing waits
        # on it, and a close that fails must not fail the request that happened
        # to trigger it.
        logger.warning("Could not close a SQLite connection nobody uses any more: %s", exc)
