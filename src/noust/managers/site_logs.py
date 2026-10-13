# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The access and error logs of a site, read for the console and ``noust site logs``.

The site templates write ``<domain>.access.log`` and ``<domain>.error.log`` for
nginx and for Apache. Until 3.4 the first fed the traffic chart of a static site
and the second was read by ``noust diagnose``, but nobody could *look* at either
from Noust: the question "what did that 502 look like in nginx's own words" meant
an SSH session. This is the reader (checklist G-11).

It reads the end of the file and no more: at most :data:`TAIL_BYTES` bytes, from
a file opened without following a symbolic link (the directory is root's, but a
site's name is an operator's input). What it returns goes through the same
scrubber as every other log Noust shows: an access log carries query strings, and
a query string carries tokens.

Rotated logs (``.1``, ``.gz``) are not read; ``logrotate`` has its own rules and
the live file is what an operator is looking at.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from noust.monitor.plan import log_directories

#: A site's name as the log files are named after it: letters, digits, dots and
#: hyphens, never a separator. The guard is here, where the file name is made
#: (CONTRIBUTING rule 4), not in whoever calls.
_SITE_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,252})")

#: The kinds of log a site has.
KINDS: tuple[str, ...] = ("access", "error")

#: The most bytes read from the end of a log.
TAIL_BYTES = 1024 * 1024

#: The most lines returned in one answer.
MAX_LINES = 1000

#: The longest a line may be before it is cut: a log line is not a file.
MAX_LINE_CHARS = 4000


@dataclass(frozen=True)
class SiteLog:
    """
    What a site's log said last.

    Attributes:
        domain: The site.
        kind: ``access`` or ``error``.
        path: The file read, or the first place it was looked for.
        available: Whether there was a log to read.
        lines: The last lines that matched, oldest first.
        size_bytes: The file's size.
        truncated: Earlier lines exist that were not read, or were left out by
            the limit on lines.
        matched: How many lines matched the filter among those read.
    """

    domain: str
    kind: str
    path: str | None
    available: bool
    lines: list[str] = field(default_factory=list)
    size_bytes: int = 0
    truncated: bool = False
    matched: int = 0

    def to_dict(self) -> dict[str, object]:
        """
        Returns:
            The log as the API and the CLI show it.
        """
        return {
            "domain": self.domain,
            "kind": self.kind,
            "path": self.path,
            "available": self.available,
            "lines": list(self.lines),
            "size_bytes": self.size_bytes,
            "truncated": self.truncated,
            "matched": self.matched,
        }


class SiteLogReader:
    """Reads the tail of a site's access or error log."""

    def __init__(
        self,
        webserver: str,
        *,
        scrub: Callable[[str], str] | None = None,
        directories: tuple[Path, ...] | None = None,
    ) -> None:
        """
        Args:
            webserver: ``nginx`` or ``apache``: where the logs are looked for.
            scrub: Masks what must not be shown; applied to every line.
            directories: Where to look, instead of the web server's own. Tests
                point it at a temporary directory.
        """
        self._directories = directories if directories is not None else log_directories(webserver)
        self._scrub = scrub or (lambda text: text)

    def locate(self, domain: str, kind: str) -> Path | None:
        """
        Find a site's log.

        Args:
            domain: The site's domain, already validated as one.
            kind: ``access`` or ``error``.

        Returns:
            The first existing file in the web server's log directories.
        """
        for directory in self._directories:
            candidate = directory / f"{domain}.{kind}.log"
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
        return None

    def read(
        self, domain: str, kind: str, *, lines: int = 200, contains: str | None = None
    ) -> SiteLog:
        """
        Read the last lines of a site's log.

        Args:
            domain: The site's domain, already validated as one.
            kind: ``access`` or ``error``.
            lines: How many lines at most, newest last.
            contains: Keep only lines with this text, compared without regard
                to case. Plain text, never a pattern: a pattern from a request
                is a way to make this process spend its time.

        Returns:
            The log; ``available`` is False, with the place it was looked for,
            when there is none.

        Raises:
            ValueError: When ``kind`` is not a kind of log or ``domain`` is not
                the name of a site (a separator in it would name another file).
        """
        if kind not in KINDS:
            raise ValueError(f"A site has {' and '.join(KINDS)} logs, not {kind!r}")
        if not _SITE_NAME.fullmatch(domain) or ".." in domain:
            raise ValueError(f"{domain!r} is not a site's name")
        wanted = max(1, min(int(lines), MAX_LINES))
        found = self.locate(domain, kind)
        if found is None:
            expected = self._directories[0] / f"{domain}.{kind}.log" if self._directories else None
            return SiteLog(domain, kind, str(expected) if expected else None, available=False)

        try:
            descriptor = os.open(found, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return SiteLog(domain, kind, str(found), available=False)
        with os.fdopen(descriptor, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            start = max(0, size - TAIL_BYTES)
            handle.seek(start)
            data = handle.read(TAIL_BYTES)

        text = data.decode("utf-8", errors="replace")
        rows = text.split("\n")
        if start > 0 and rows:
            rows = rows[1:]  # the first row is the end of a line that began before it
        rows = [row for row in rows if row.strip()]
        needle = contains.lower() if contains else None
        if needle:
            rows = [row for row in rows if needle in row.lower()]
        shown = rows[-wanted:]
        return SiteLog(
            domain=domain,
            kind=kind,
            path=str(found),
            available=True,
            lines=[self._scrub(_cut(row)) for row in shown],
            size_bytes=size,
            truncated=start > 0 or len(rows) > wanted,
            matched=len(rows),
        )


def _cut(line: str) -> str:
    """
    Args:
        line: One line of a log.

    Returns:
        It, shortened with an ellipsis when it is longer than :data:`MAX_LINE_CHARS`.
    """
    if len(line) <= MAX_LINE_CHARS:
        return line
    return line[: MAX_LINE_CHARS - 1] + "…"
