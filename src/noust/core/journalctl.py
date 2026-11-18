# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What ``journalctl`` means by the way it ends, read in one place.

A search that finds nothing is not a failure of the search, but systemd 255 (Ubuntu 24.04, Debian 13)
exits 1 for it when ``--grep`` is given, and writes ``-- No entries --`` where an entry would be. A caller
that takes every non-zero exit for "the journal could not be read" reports an empty week as an unreadable
one (the OOM check of ``noust diagnose`` did: on a server that had never run out of memory it said it could
not read the kernel log). Older versions exit 0 with nothing, and a build without PCRE2 fails with its own
sentence; a failure that says anything other than "no entries" is still a failure.
"""

from __future__ import annotations

from noust.core.runner import CommandResult

__all__ = ["says_nothing_matched"]


def says_nothing_matched(result: CommandResult) -> bool:
    """
    Say whether a ``journalctl`` that exited non-zero did so only because nothing matched.

    Args:
        result: What the command printed and how it ended.

    Returns:
        True when it failed and the first line it said (its error output, else its standard
        output) is the "No entries" notice. False for a success, for a failure that says anything
        else, and for one that says nothing at all: that cannot be told from a journal that is
        not there.
    """
    if result.success:
        return False
    said = (result.stderr or result.stdout).strip().splitlines()
    return bool(said) and "No entries" in said[0]
