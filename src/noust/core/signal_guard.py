# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Let a ``finally`` run when the process is told to stop.

systemd stops a unit with SIGTERM, a terminal closing sends SIGHUP, and the
default action of both ends a Python process on the spot: no ``finally``, no
``with``. For a command that has stilled something on purpose (stopped an
application to copy its data, frozen its units) that is the one outcome that
must not happen, because the application stays down. Inside
:func:`stop_as_exit` those signals raise ``SystemExit`` where the process is, so
every ``finally`` between there and the top runs and the application is let go.

Handlers can only be set from the main thread; elsewhere (a console job, which
the web process ends by itself) it does nothing, and says so by returning
``False`` to the ``with``.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

#: The signals that stop the process the way an operator or systemd does.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


@contextmanager
def stop_as_exit() -> Iterator[bool]:
    """
    Turn SIGTERM and SIGHUP into ``SystemExit`` for the length of the block.

    Yields:
        Whether the handlers are installed: False off the main thread, where
        the process's own signal handling stays as it was.
    """
    if threading.current_thread() is not threading.main_thread():
        yield False
        return

    def stop(signum: int, frame: FrameType | None) -> None:
        raise SystemExit(128 + signum)

    previous = {sig: signal.signal(sig, stop) for sig in STOP_SIGNALS}
    try:
        yield True
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
