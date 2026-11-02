# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A process that has been running a while: every low descriptor number is taken.

The console holds a descriptor for every socket, file and database connection it
has open, so what it opens next is numbered past ``FD_SETSIZE`` (1024), where
``select.select`` raises. A test of anything that waits on a descriptor of its own
runs inside :func:`descriptors_taken_up_to` to be given such a number.
"""

from __future__ import annotations

import os
import resource
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

#: Room kept above the last descriptor held, for what the test itself opens.
HEADROOM = 64


@contextmanager
def descriptors_taken_up_to(number: int) -> Iterator[None]:
    """
    Open descriptors until every number up to ``number`` is in use, then hand control back.

    The lowest free number is what the next open gets, so whatever the body opens
    is numbered above ``number``. The descriptors are closed, and the process's
    limit put back, when the body ends.

    Args:
        number: The highest number to occupy.

    Yields:
        Nothing.
    """
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    needed = number + HEADROOM
    if hard != resource.RLIM_INFINITY and hard < needed:
        pytest.skip(f"the hard limit of descriptors ({hard}) is below what this needs ({needed})")
    if soft != resource.RLIM_INFINITY and soft < needed:
        resource.setrlimit(resource.RLIMIT_NOFILE, (needed, hard))
    held: list[int] = []
    try:
        while not held or held[-1] < number:
            held.append(os.open(os.devnull, os.O_RDONLY))
        yield
    finally:
        for descriptor in held:
            os.close(descriptor)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
