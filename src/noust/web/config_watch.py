# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The running console reads ``config.yaml`` again when another process changed it.

Every command of the CLI that changes a setting (``noust compliance enable``,
``noust config set``) writes the file from a process of its own. The console loads it
once, when it starts, and every control, every policy and every framework it enforces is
read from that copy: a control turned off at the terminal (``restricted_tokens``, in the
fleet's case) kept refusing until ``systemctl restart noust-web``.

The file is looked at when a request arrives, at most once per interval, and read again
only when it changed (its modification time, size or inode: writers replace the file
through a rename, so a new inode is a change whatever the clock says). No thread polls: a
console nobody talks to looks at nothing, and one that is busy looks once per interval, not
once per request.

This is the console's own concern and not :class:`~noust.core.config.Config`'s: what a
long-lived process does about a file other processes write is a decision of that process
(the monitor already reloads in its own loop), and the configuration module is already
past the size the project allows a module to grow.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from noust.core.exceptions import NoustError

logger = logging.getLogger(__name__)

#: How long the console waits between two looks at the file. A change made at the terminal is
#: in force in the console within this long of the next request; shorter would stat the file
#: on most requests of a busy console for nothing, longer would make "no restart needed" a
#: promise with a long wait in it.
CHECK_INTERVAL_SECONDS = 1.0

#: What identifies one version of the file: modification time, size and inode.
Signature = tuple[int, int, int]


class ConfigWatch:
    """
    Reload the configuration when its file changed, looking at it at most once per interval.

    The signature of the file at construction is the one the process has loaded, so the
    first look after a start finds nothing to do and a file edited before it never wipes a
    value set in memory.
    """

    def __init__(
        self,
        path: Callable[[], Path],
        reload: Callable[[], None],
        *,
        interval: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        stat: Callable[[Path], os.stat_result] = lambda candidate: candidate.stat(),
    ) -> None:
        """
        Args:
            path: The configuration file, asked at each look (it may be moved in a test).
            reload: Reads the file again into the process's configuration.
            interval: Seconds between two looks; :data:`CHECK_INTERVAL_SECONDS` when None.
            clock: A monotonic clock, in seconds.
            stat: Reads a file's status; replaceable to count the looks.
        """
        self._path = path
        self._reload = reload
        self._interval = CHECK_INTERVAL_SECONDS if interval is None else interval
        self._clock = clock
        self._stat = stat
        self._lock = threading.Lock()
        self._signature = self._read()
        self._looked_at = clock()

    def _read(self) -> Signature | None:
        """
        Returns:
            The file's signature, or None when it does not exist or cannot be examined.
        """
        try:
            status = self._stat(self._path())
        except OSError:
            return None
        return (status.st_mtime_ns, status.st_size, status.st_ino)

    def due(self) -> bool:
        """
        Returns:
            Whether the interval since the last look has passed.
        """
        return self._clock() - self._looked_at >= self._interval

    def check(self) -> bool:
        """
        Look at the file if the interval has passed, and reload it if it changed.

        Returns:
            True when the configuration was read again. False when nothing was due, nothing
            changed or the file could not be read (the settings the console has stay, and the
            log says why).
        """
        if not self.due():
            return False
        with self._lock:
            if not self.due():
                return False
            self._looked_at = self._clock()
            signature = self._read()
            if signature == self._signature:
                return False
            # Taken before the reload: a file that cannot be read is tried again when it
            # changes, not every interval.
            self._signature = signature
            try:
                self._reload()
            except (NoustError, OSError, ValueError) as exc:
                logger.warning(
                    "Could not read the configuration again, keeping the last one: %s", exc
                )
                return False
        return True


def default_watch() -> ConfigWatch:
    """
    Returns:
        The watch of the process-wide configuration and its file.
    """
    from noust.core import config as config_module

    return ConfigWatch(
        lambda: Path(config_module.DEFAULT_CONFIG_PATH),
        lambda: config_module.Config().reload(),
    )


class ConfigWatchMiddleware:
    """
    Raw ASGI middleware that lets the watch look before a request is handled.

    Raw, like the security middleware, so that a WebSocket (the terminal, the job logs) is
    covered too: a connection opened after a change sees it.
    """

    def __init__(self, app: ASGIApp, watch: ConfigWatch | None = None) -> None:
        """
        Args:
            app: The application to wrap.
            watch: The watch; the process-wide configuration's by default.
        """
        self.app = app
        self.watch = watch or default_watch()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Check the file if it is time, then pass the connection on.

        Args:
            scope: The ASGI connection scope.
            receive: ASGI receive channel.
            send: ASGI send channel.
        """
        if scope["type"] in ("http", "websocket") and self.watch.due():
            # Off the event loop: reading the file again is a parse, rare but not free.
            await run_in_threadpool(self.watch.check)
        await self.app(scope, receive, send)
