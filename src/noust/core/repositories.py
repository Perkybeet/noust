# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which repository a source names, whichever way it is spelled.

``https://github.com/Owner/Repo.git``, ``git@github.com:owner/repo``,
``ssh://git@github.com:22/owner/repo`` and ``github:owner/repo`` are one
repository, and what a code host's webhook says (``owner/repo``), what a deploy
key is assigned to and what a preview is built from all have to agree on that.
This is the one place that reads it (rule 3): the previews, the deploy keys and
the HTTPS credentials all ask here.
"""

from __future__ import annotations

import re

# Git sources, reduced to the host and the repository path they name.
_GITHUB_SHORTHAND = re.compile(r"^github:(?P<path>[\w.-]+/[\w.-]+?)(?:\.git)?/?$", re.IGNORECASE)
_SCP_LIKE = re.compile(r"^[\w.-]+@(?P<host>[\w.-]+):(?!//)(?P<path>[\w./-]+?)(?:\.git)?/?$")
_URL_LIKE = re.compile(
    r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[\w.-]+)(?::\d+)?/(?P<path>[\w./-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def repository_key(source: str | None) -> tuple[str, str] | None:
    """
    Reduce a git source to the repository it names.

    ``https://github.com/Owner/Repo.git``, ``git@github.com:owner/repo``,
    ``ssh://git@github.com/owner/repo`` and ``github:owner/repo`` all name the
    same repository, and a webhook names it as ``owner/repo``; comparing the
    sources as typed would miss every one of those.

    Args:
        source: A source as the store records it.

    Returns:
        ``(host, path)``, both lowercased, the path without ``.git``; None
        when the source is not a git repository (a local path, an archive).
    """
    if not source:
        return None
    text = source.strip().split("#", 1)[0]
    shorthand = _GITHUB_SHORTHAND.match(text)
    if shorthand:
        return ("github.com", shorthand.group("path").lower())
    match = _URL_LIKE.match(text) or _SCP_LIKE.match(text)
    if not match:
        return None
    path = match.group("path").strip("/")
    if "/" not in path:
        return None
    return (match.group("host").lower(), path.lower())


def repository_identity(source: str | None) -> str | None:
    """
    Name a repository the way a deploy key or a credential is assigned to it.

    Args:
        source: A source as the store records it, or a clone URL.

    Returns:
        ``host/owner/repo`` in lower case, or None when the source is not a
        git repository.
    """
    key = repository_key(source)
    return f"{key[0]}/{key[1]}" if key is not None else None
