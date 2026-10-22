# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an update will change, read before the operator presses anything (checklist N-24).

A server that is about to update has the notes of the releases it runs, not of the one it is
about to install. The GitHub release of that version carries them, written from the same Debian
changelog (:mod:`noust.core.changelog`), so they are read from there, through the same bounded,
public-HTTPS-only reader the update check uses (:func:`noust.core.package_index.fetch`): a short
deadline, a size limit, and no address inside the machine's own networks.

Nothing here can fail a request. A release that cannot be read - the check is turned off, the
network is down, the release has no such section - is an answer that says so, and the console
shows the link to the release instead.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from noust.core import package_index
from noust.core.changelog import Entry, parse_release_body

logger = logging.getLogger(__name__)

#: The GitHub API address of a release by its tag.
RELEASE_API = "https://api.github.com/repos/Perkybeet/noust/releases/tags/v{version}"

#: A version this module will ask GitHub about: a plain dotted number, nothing a request could smuggle.
VERSION = re.compile(r"^\d+\.\d+\.\d+$")

#: How long an answer is kept, so a console that polls does not ask GitHub every time.
CACHE_SECONDS = 3600

#: The most notes kept, so a release with a huge description cannot fill the response.
MAX_ITEMS = 60


@dataclass(frozen=True)
class Notes:
    """
    The notes of one release, or why there are none.

    Attributes:
        version: The version asked for.
        available: Whether the release's lines could be read.
        entry: The lines, when they could.
        url: The release's page.
        reason: Why they could not be read, in a sentence, when they could not.
    """

    version: str
    available: bool
    entry: Entry | None = None
    url: str = ""
    reason: str = ""


_CACHE: dict[str, tuple[float, Notes]] = {}


def clear_cache() -> None:
    """Forget every answer (the tests, and a console that was told the check changed)."""
    _CACHE.clear()


def release_page(version: str) -> str:
    """
    Args:
        version: ``3.4.0``.

    Returns:
        The address of the release on GitHub.
    """
    return f"https://github.com/Perkybeet/noust/releases/tag/v{version}"


def read(
    version: str,
    *,
    enabled: bool = True,
    fetch: Callable[..., bytes] | None = None,
    now: Callable[[], float] = time.monotonic,
) -> Notes:
    """
    Read the notes of a release from GitHub.

    Args:
        version: ``3.4.0``; anything else is refused before a request is made.
        enabled: Whether the operator left the update check on (``updates.check``); off, nothing
            is requested.
        fetch: The reader (:func:`noust.core.package_index.fetch` by default; replaced in tests).
        now: A monotonic clock, for the cache.

    Returns:
        The notes, or an answer saying why there are none. Cached for an hour.
    """
    if not VERSION.fullmatch(version):
        return Notes(version, False, reason=f"{version!r} is not a release such as 3.4.0")
    if not enabled:
        return Notes(
            version,
            False,
            url=release_page(version),
            reason="Update checks are turned off (updates.check), so nothing is read from GitHub",
        )
    cached = _CACHE.get(version)
    if cached is not None and now() - cached[0] < CACHE_SECONDS:
        return cached[1]
    reader = fetch or package_index.fetch
    try:
        raw = reader(RELEASE_API.format(version=version), accept="application/vnd.github+json")
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError("GitHub's answer is not a release")
        body = str(document.get("body") or "")
        url = str(document.get("html_url") or release_page(version))
    except package_index.FETCH_ERRORS as exc:
        logger.debug("Could not read the notes of %s: %s", version, exc)
        return Notes(
            version,
            False,
            url=release_page(version),
            reason=f"GitHub did not answer for {version}: {exc}",
        )
    entry = parse_release_body(body, version)
    if not url.startswith("https://github.com/"):
        url = release_page(version)
    if not entry.items:
        notes = Notes(
            version, False, url=url, reason=f"The release {version} has no list of changes to show"
        )
    else:
        notes = Notes(version, True, Entry(version, "", entry.items[:MAX_ITEMS]), url)
    _CACHE[version] = (now(), notes)
    return notes
