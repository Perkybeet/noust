# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Slow answers, kept for a while, and never computed in a request.

The overview of a server wants a dozen facts: how many updates are pending,
whether a reboot is due, how full the fullest disk is. Some of them cost a
second (``apt-get -s`` simulates an upgrade) or a process per unit. A page that
computes them on every load is a page that takes a second to open, and a fleet
view that asks twenty nodes for them is twenty pages worth of load.

So the cache gives an answer that is old and says how old, and refreshes it in
the background: the first look at a fact starts computing it and returns
"unknown yet", the next one sees the result. Two callers asking for the same fact
at once share one computation, so a burst of page loads costs one ``apt-get``.

A loader that fails does not leave a hole. The failure is kept as the fact's
``error``, in the tool's own words, so the page says "could not check: ..." and a
cosmetic empty box never stands in for a broken probe.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NoustError

log = logging.getLogger(__name__)

#: What a loader can fail with in operation. Named, so a bug in a loader is a
#: traceback and not a box that quietly says "unavailable".
LOADER_ERRORS: tuple[type[Exception], ...] = (NoustError, OSError, ValueError, sqlite3.Error)


@dataclass(frozen=True)
class Fact:
    """
    One remembered answer.

    Attributes:
        value: What the loader returned; None when it failed the first time.
        checked_at: When it was computed, ISO 8601 UTC.
        age_seconds: How old it is now.
        error: Why the last computation failed, verbatim, or None. When it is
            set and ``value`` is not None, ``value`` is the last good answer.
        fresh: It is younger than the time it is allowed to live.
    """

    value: Any
    checked_at: str
    age_seconds: float
    error: str | None = None
    fresh: bool = True


class _Entry:
    """A cached answer and the lock that keeps two computations of it apart."""

    def __init__(self) -> None:
        self.value: Any = None
        self.error: str | None = None
        self.stamp: float | None = None
        self.wall: str = ""
        self.lock = threading.Lock()
        self.running = False


class FactCache:
    """Remembers answers, computes them once at a time, and can do it in the background."""

    def __init__(
        self, *, background: bool = True, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """
        Args:
            background: Compute a stale or missing fact on a thread and return
                what there is now. False computes it in the caller, which is what
                tests and the command line want.
            clock: Monotonic seconds; replaced in tests.
        """
        self._background = background
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._guard = threading.Lock()

    def _entry(self, key: str) -> _Entry:
        """
        Find or create the entry of a key.

        Args:
            key: The fact's name.

        Returns:
            Its entry.
        """
        with self._guard:
            return self._entries.setdefault(key, _Entry())

    def _fact(self, entry: _Entry, ttl: float) -> Fact | None:
        """
        Describe an entry as a fact.

        Args:
            entry: The entry.
            ttl: How long an answer stays fresh.

        Returns:
            The fact, or None when nothing was ever computed or failed.
        """
        if entry.stamp is None:
            return None
        age = self._clock() - entry.stamp
        return Fact(entry.value, entry.wall, age, entry.error, fresh=age < ttl)

    def peek(self, key: str, ttl: float = 0.0) -> Fact | None:
        """
        Read what is there without computing anything.

        Args:
            key: The fact's name.
            ttl: How long an answer counts as fresh.

        Returns:
            The fact, or None.
        """
        return self._fact(self._entry(key), ttl)

    def put(self, key: str, value: Any) -> None:
        """
        Store an answer computed elsewhere, such as by a job that just refreshed it.

        Args:
            key: The fact's name.
            value: The answer.
        """
        entry = self._entry(key)
        entry.value, entry.error = value, None
        entry.stamp, entry.wall = self._clock(), datetime.now(timezone.utc).isoformat()

    def invalidate(self, *keys: str) -> None:
        """
        Forget answers, so the next look computes them again.

        Args:
            keys: The facts' names; all of them when none are given.
        """
        with self._guard:
            for key in keys or tuple(self._entries):
                if key in self._entries:
                    self._entries[key].stamp = None

    def _compute(self, entry: _Entry, loader: Callable[[], Any]) -> None:
        """
        Run a loader and store its answer or its failure.

        Args:
            entry: Where to store it.
            loader: The computation.
        """
        try:
            value = loader()
        except LOADER_ERRORS as exc:
            # The words the tool said, not a paraphrase; a good earlier answer stays.
            text = (
                f"{exc.message}: {exc.output}"
                if isinstance(exc, NoustError) and exc.output
                else str(exc)
            )
            entry.error = text
            log.warning("A fact could not be computed: %s", text)
        else:
            entry.value, entry.error = value, None
        finally:
            entry.stamp, entry.wall = self._clock(), datetime.now(timezone.utc).isoformat()

    def get(
        self, key: str, loader: Callable[[], Any], ttl: float, *, wait: bool = False
    ) -> Fact | None:
        """
        Read a fact, computing it when it is missing or too old.

        Args:
            key: The fact's name.
            loader: Computes it.
            ttl: How long an answer stays fresh, in seconds.
            wait: Compute in the caller even when background work is on, for a
                page whose whole content is this fact.

        Returns:
            The fact. With background work on and nothing computed yet, None: the
            computation has started and the next look finds it. A stale fact is
            returned as it is, with ``fresh`` false, while a new one is computed.
        """
        entry = self._entry(key)
        current = self._fact(entry, ttl)
        if current is not None and current.fresh:
            return current
        if self._background and not wait:
            self._start(entry, loader)
            return current
        with entry.lock:
            # Another caller may have computed it while this one waited.
            again = self._fact(entry, ttl)
            if again is not None and again.fresh:
                return again
            self._compute(entry, loader)
        return self._fact(entry, ttl)

    def _start(self, entry: _Entry, loader: Callable[[], Any]) -> None:
        """
        Compute a fact on a thread, unless one is already doing it.

        Args:
            entry: The fact's entry.
            loader: The computation.
        """
        with self._guard:
            if entry.running:
                return
            entry.running = True

        def work() -> None:
            try:
                with entry.lock:
                    self._compute(entry, loader)
            finally:
                entry.running = False

        threading.Thread(target=work, name="server-facts", daemon=True).start()
