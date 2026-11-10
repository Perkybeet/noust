# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a process holds open against what it may, and what is said when none is left.

A central's console reached its limit of 1024 open files and answered every request, its own pages
included, with "Too many open files": a 500 with no name, from a process nobody had measured
(issue 19, fixed at its cause in 4.2.1). This is the rest of what the issue asked for, in one place
so that the health report, the diagnosis, the API's error boundary and the fleet say it the same
way:

- :func:`read_usage` measures a process from ``/proc`` (the files it has open and its soft limit);
- :func:`is_exhaustion` recognises the error, however many layers it was wrapped in;
- :func:`exhausted_error` says it ("file descriptors exhausted") with what to do;
- :func:`unit_limit` and :func:`has_old_limit` read what a unit file sets, for the doctor.

:data:`SERVICE_LIMIT` is the one number Noust's own units are given (``noust-web`` and
``noust-monitor``): the systemd default soft limit of 1024 was what the console ran out of.
"""

from __future__ import annotations

import errno
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import DescriptorsExhaustedError
from noust.core.server_text import say

__all__ = [
    "ERROR_CODE",
    "SERVICE_LIMIT",
    "UNLIMITED",
    "Usage",
    "exhausted_error",
    "has_old_limit",
    "is_exhaustion",
    "read_usage",
    "unit_limit",
]

#: The ``error`` of the API's answer, which a central reads from a node.
ERROR_CODE = "descriptors_exhausted"

#: ``LimitNOFILE=`` of the units Noust writes for itself.
SERVICE_LIMIT = 65536

#: What a limit of ``infinity`` is, as a number to compare.
UNLIMITED = sys.maxsize

#: From this share of its limit a process is said to be close to it.
NEAR = 0.8

#: From this share requests are already failing for want of a file.
CRITICAL = 0.95

#: The two ways of running out: the process's own limit and the system's.
_EXHAUSTION = (errno.EMFILE, errno.ENFILE)

_MAX_OPEN_FILES = re.compile(r"^Max open files\s+(\S+)\s+(\S+)", re.MULTILINE)

#: How deep a chain of causes is followed: a cycle ends, and so does a very long chain.
_DEPTH = 12


@dataclass(frozen=True)
class Usage:
    """
    The files a process has open against the limit it was given.

    Attributes:
        open: How many descriptors it holds.
        limit: Its soft limit; None when it has none.
    """

    open: int
    limit: int | None

    @property
    def fraction(self) -> float | None:
        """The share of the limit that is in use; None without a limit."""
        return None if not self.limit else self.open / self.limit

    @property
    def near(self) -> bool:
        """Whether it is close to its limit (80 % or more), which is when to say so."""
        return self.fraction is not None and self.fraction >= NEAR

    @property
    def critical(self) -> bool:
        """Whether so little is left that requests are failing."""
        return self.fraction is not None and self.fraction >= CRITICAL


def read_usage(pid: int, *, proc: Path = Path("/proc")) -> Usage | None:
    """
    Measure a process.

    Args:
        pid: The process.
        proc: Where its directory is; ``/proc`` unless a test describes another machine.

    Returns:
        The descriptors it holds and its soft limit; None when the process is gone, is not ours to
        read (only root reads another account's) or its limits do not say.
    """
    try:
        held = sum(1 for _ in (proc / str(pid) / "fd").iterdir())
        limits = (proc / str(pid) / "limits").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = _MAX_OPEN_FILES.search(limits)
    if found is None:
        return None
    soft = found.group(1)
    return Usage(open=held, limit=int(soft) if soft.isdigit() else None)


def is_exhaustion(error: BaseException) -> bool:
    """
    Tell whether an error is the process running out of file descriptors.

    Args:
        error: Anything that was raised.

    Returns:
        True when it, or what it was raised from (or while handling), is an ``OSError`` with
        ``EMFILE`` or ``ENFILE``: the code under a request turns an ``OSError`` into an error of
        its own and the cause is what names it.
    """
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    for _ in range(_DEPTH):
        if not pending:
            break
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno in _EXHAUSTION:
            return True
        pending += [current.__cause__, current.__context__]
    return False


def exhausted_error() -> DescriptorsExhaustedError:
    """
    Make the error a request that ran out of descriptors is answered with.

    Returns:
        The error, carrying its sentence: what happened and what to do, with the command.
    """
    return DescriptorsExhaustedError.from_text(
        say("server.descriptors_exhausted", command="noust web restart")
    )


_LIMIT_LINE = re.compile(r"^LimitNOFILE=(\S*)\s*$", re.MULTILINE)


def unit_limit(unit_text: str) -> int | None:
    """
    Read the soft limit of open files a unit sets.

    Args:
        unit_text: The unit file.

    Returns:
        ``LimitNOFILE=`` as a number (the soft part of ``soft:hard``, :data:`UNLIMITED` for
        ``infinity``); None when the unit sets none (the system's default applies) or sets one
        that is not a number.
    """
    found = _LIMIT_LINE.findall(unit_text)
    if not found:
        return None
    soft = found[-1].split(":", 1)[0]
    if soft == "infinity":
        return UNLIMITED
    return int(soft) if soft.isdigit() else None


def has_old_limit(unit_text: str) -> bool:
    """
    Tell whether a unit of Noust's own still has less than they are given now.

    Args:
        unit_text: The unit file of ``noust-web`` or ``noust-monitor``.

    Returns:
        True when it sets no limit or one below :data:`SERVICE_LIMIT`: written before 4.3, it
        stays at the system's 1024 until it is written again.
    """
    limit = unit_limit(unit_text)
    return limit is None or limit < SERVICE_LIMIT
