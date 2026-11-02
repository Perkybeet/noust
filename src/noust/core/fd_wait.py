# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Waiting on a file descriptor for something to read, whatever its number.

``select.select`` keeps its descriptors in a fixed-size bit set and raises
``ValueError`` for a number from ``FD_SETSIZE`` (1024) up. The console holds a
descriptor for every socket, file and database connection it has open, so a
descriptor it opens after it has been running a while is past that line, and a
terminal whose master side was one of them showed nothing while its program ran:
the read took the error for the session's end. ``poll`` has no such ceiling.
Everything that waits on a descriptor of its own asks here, so there is one place
that cannot hit it.
"""

from __future__ import annotations

import math
import select


def wait_readable(descriptor: int, timeout: float) -> bool:
    """
    Wait until a descriptor has something to read, or has been hung up.

    Args:
        descriptor: Any open descriptor, whatever its number.
        timeout: Seconds to wait at most.

    Returns:
        True when a read will not block (data, or the other side closed, which
        a read reports as an error); False when ``timeout`` passed.

    Raises:
        OSError: The system could not wait.
    """
    poller = select.poll()
    poller.register(descriptor, select.POLLIN)
    # Rounded up: a wait of a few hundred microseconds must not become a busy loop.
    return bool(poller.poll(math.ceil(timeout * 1000)))
