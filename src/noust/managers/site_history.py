# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a site's file said before, and whether it still says what the web server runs.

**History.** Every write of a site's file goes through the web server manager
(:meth:`~noust.managers.webserver.WebServerManager.replace_site_config` for an edit, a restore or
the API's ``PUT /config``; ``_write_site`` for what a deployer renders), and each one keeps the
text it wrote. When a deployer is about to overwrite a file that is not what Noust last wrote,
that text is kept first as ``found``: a hand edit made to a generated site is lost to the next
deploy by design, and this is how it is recovered. Restoring a version is the same write as any
edit, so it is tested by the web server before it is installed, and recorded as a version of its
own.

**Drift.** Two questions about a file that nobody tracks:

- *Pending*: it was written after the web server last started its workers (a reload makes new
  ones), so the web server still runs the previous text. Noust reloads right after writing, so
  this is a reload that failed, or a hand edit nobody applied.
- *Edited outside Noust*: its hash is not the one of the newest version Noust kept, so somebody
  changed it with an editor. A site Noust never wrote has no such baseline and is never said to
  have drifted.

Neither needs a daemon: the process table and the file's stat answer the first, and the history
the second.
"""

from __future__ import annotations

import difflib
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from noust.core import audit
from noust.core.runner import CommandRunner
from noust.core.site_history_store import SiteHistoryStore, SiteVersion, sha256_of
from noust.core.store import NoustStore

_log = logging.getLogger(__name__)

#: A file written within this many seconds of the workers starting is the reload's own.
GRACE_SECONDS = 3.0

#: The processes whose start tells when the web server last loaded its configuration.
_PROCESS_NAMES = {"nginx": "nginx", "apache": "apache2"}


@dataclass(frozen=True)
class Drift:
    """
    Whether a site's file and what the web server runs have come apart.

    Attributes:
        pending_reload: The file was written after the web server last loaded its configuration.
        edited_outside: The file is not what Noust last wrote.
    """

    pending_reload: bool = False
    edited_outside: bool = False


def actor_label() -> str | None:
    """
    Returns:
        A label for who is acting (an account, a token, a command line), None for the system.
    """
    actor = audit.current_actor()
    if actor is None or actor.kind == "system":
        return None
    return str(actor.name or actor.id or actor.kind)


def unified_diff(older: str, newer: str, *, older_label: str, newer_label: str) -> str:
    """
    Args:
        older: The text before.
        newer: The text after.
        older_label: What to call the first in the header.
        newer_label: What to call the second.

    Returns:
        A unified diff, empty when the texts are the same.
    """
    return "".join(
        difflib.unified_diff(
            older.splitlines(keepends=True),
            newer.splitlines(keepends=True),
            fromfile=older_label,
            tofile=newer_label,
        )
    )


class SiteHistory:
    """
    Keep and compare the versions of one web server's sites.

    Args:
        webserver: ``nginx`` or ``apache``.
        store: The store; the process-wide one by default.
        runner: Asks the process table when the web server started its workers.
    """

    def __init__(
        self,
        webserver: str,
        *,
        store: NoustStore | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self._webserver = webserver
        self._records = SiteHistoryStore(store)
        self._runner = runner

    # -- history --------------------------------------------------------------

    def about_to_overwrite(self, domain: str, current: str) -> None:
        """
        Keep what a file says before it is replaced, when Noust did not write it.

        Args:
            domain: The site's name.
            current: The file's text now.
        """
        if not current:
            return  # nothing is there yet: the file is about to be created
        try:
            newest = self._records.latest(domain, self._webserver)
            if newest is None or newest.sha256 != sha256_of(current):
                self._records.add(
                    domain, self._webserver, current, source="found", actor=actor_label()
                )
        except sqlite3.Error as exc:
            _log.warning("Could not keep the earlier text of %s: %s", domain, exc)

    def written(self, domain: str, text: str, *, source: str) -> None:
        """
        Keep what a file now says.

        Args:
            domain: The site's name.
            text: The text written.
            source: ``generated``, ``edit`` or ``restore``.
        """
        try:
            self._records.add(domain, self._webserver, text, source=source, actor=actor_label())
        except sqlite3.Error as exc:
            _log.warning("Could not keep the text of %s: %s", domain, exc)

    def versions(self, domain: str) -> list[SiteVersion]:
        """
        Args:
            domain: The site's name.

        Returns:
            Its versions, newest first, without their text.
        """
        return self._records.versions(domain, self._webserver)

    def version(self, domain: str, number: int) -> SiteVersion | None:
        """
        Args:
            domain: The site's name.
            number: The version.

        Returns:
            It with its text; None when it does not exist.
        """
        return self._records.get(domain, self._webserver, number)

    def forget(self, domain: str) -> None:
        """
        Drop the history of a site that is gone.

        Args:
            domain: The site's name.
        """
        try:
            self._records.forget(domain)
        except sqlite3.Error as exc:
            _log.warning("Could not drop the history of %s: %s", domain, exc)

    # -- drift ----------------------------------------------------------------

    def workers_started(self, runner: CommandRunner) -> float | None:
        """
        Find when the web server last started its workers.

        A reload starts new workers, so the newest start is the last time the files were read.

        Args:
            runner: Reads the process table.

        Returns:
            Seconds since the epoch, or None when the web server is not running.
        """
        name = _PROCESS_NAMES.get(self._webserver, self._webserver)
        result = runner.run(["ps", "-C", name, "-o", "etimes="], timeout=10)
        ages = [int(token) for token in result.stdout.split() if token.isdigit()]
        return time.time() - min(ages) if ages else None

    def drift(
        self, files: dict[str, Path], runner: CommandRunner | None = None
    ) -> dict[str, Drift]:
        """
        Say which of these sites have drifted.

        Args:
            files: Site name to its configuration file.
            runner: Reads the process table; the one given at construction by default.

        Returns:
            Site name to its drift; a site that has not drifted is left out.
        """
        active = runner or self._runner
        started = self.workers_started(active) if active is not None else None
        try:
            newest = self._records.newest_by_site(self._webserver)
        except sqlite3.Error as exc:
            _log.warning("Could not read the history of the sites: %s", exc)
            newest = {}
        found: dict[str, Drift] = {}
        for name, path in files.items():
            pending = False
            edited = False
            try:
                if started is not None:
                    pending = path.stat().st_mtime > started + GRACE_SECONDS
                known = newest.get(name)
                if known is not None and known[1] != "found":
                    edited = (
                        sha256_of(path.read_text(encoding="utf-8", errors="replace")) != known[0]
                    )
            except OSError:
                continue
            if pending or edited:
                found[name] = Drift(pending_reload=pending, edited_outside=edited)
        return found
