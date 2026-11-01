# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which repository a source names, whichever way it is spelled.

``https://github.com/Owner/Repo.git``, ``git@github.com:owner/repo``,
``ssh://git@github.com:22/owner/repo`` and ``github:owner/repo`` are one
repository, and what a code host's webhook says (``owner/repo``), what a deploy
key is assigned to and what a preview is built from all have to agree on that.
This is the one place that reads it (rule 3): the previews, the deploy keys and
the HTTPS credentials all ask here.

A port is not part of which repository it is: the forge that serves it over https on 8443 also
serves it over ssh on 2222, and a webhook says ``owner/repo``. It is part of which *server* an
address reaches, and :func:`repository_port` answers that for what is sent to the server, such as
a credential.
"""

from __future__ import annotations

import re

# Git sources, reduced to the host and the repository path they name.
_GITHUB_SHORTHAND = re.compile(r"^github:(?P<path>[\w.-]+/[\w.-]+?)(?:\.git)?/?$", re.IGNORECASE)
_SCP_LIKE = re.compile(r"^[\w.-]+@(?P<host>[\w.-]+):(?!//)(?P<path>[\w./-]+?)(?:\.git)?/?$")
_URL_LIKE = re.compile(
    r"^(?P<scheme>https?|ssh|git)://(?:[^@/]+@)?(?P<host>[\w.-]+)(?::(?P<port>[0-9]{1,5}))?"
    r"/(?P<path>[\w./-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)

#: The port each scheme answers on when an address names none.
_DEFAULT_PORTS = {"http": 80, "https": 443, "git": 9418}


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
    shorthand = _GITHUB_SHORTHAND.fullmatch(text)
    if shorthand:
        return ("github.com", shorthand.group("path").lower())
    match = _URL_LIKE.fullmatch(text) or _SCP_LIKE.fullmatch(text)
    if not match:
        return None
    path = match.group("path").strip("/")
    if "/" not in path:
        return None
    return (match.group("host").lower(), path.lower())


def repository_port(source: str | None) -> int | None:
    """
    Name the port a source asks its server on, when it is not the scheme's own.

    What a credential is kept for: the token of a repository on ``git.example.com`` is not for the
    other service a tenant of the same machine runs on ``git.example.com:6666``. The default port
    written out (``https://host:443/x``) is the same server as none.

    Args:
        source: A source as the store records it, or a clone URL.

    Returns:
        The port of an http, https or ``git://`` address that names one other than its scheme's
        default; None for any other address (an ssh port is another service's, and the ``user@host:path``
        form has none) or a source that is not a git repository.
    """
    if not source:
        return None
    match = _URL_LIKE.fullmatch(source.strip().split("#", 1)[0])
    if match is None or match.group("port") is None:
        return None
    scheme = match.group("scheme").lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    port = int(match.group("port"))
    return None if port == _DEFAULT_PORTS[scheme] else port


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
