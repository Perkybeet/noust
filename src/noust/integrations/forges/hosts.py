# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which kind of code host an address is.

An address says it when it says it (``gitlab.example.com``, ``git.example.com`` does not):
a few well-known hosts and the words hosts are named with are recognised, and the operator
tells the rest in ``integrations.forge_hosts`` (``git.example.com=gitea``). One table, read
here by the webhook setup that links to a host's settings page, by the webhook that verifies
what a host signs, and by the client that writes a status back (rule 3).

Gitea, Forgejo and Gogs are one family: Forgejo is a fork of Gitea and Gitea of Gogs, and
they share the API under ``/api/v1`` that comments are written through. They differ in what they
sign a delivery with and in whether they have commit statuses (Gogs has none).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from noust.core.exceptions import ConfigError

GITHUB = "github"
GITLAB = "gitlab"
GITEA = "gitea"
FORGEJO = "forgejo"
GOGS = "gogs"
BITBUCKET = "bitbucket"

#: Every kind a host can be, with GitHub.
KINDS: tuple[str, ...] = (GITHUB, GITLAB, GITEA, FORGEJO, GOGS, BITBUCKET)

#: The kinds an operator may declare for a host (GitHub has its own setting, ``github.host``).
DECLARABLE: tuple[str, ...] = (GITLAB, GITEA, FORGEJO, GOGS, BITBUCKET)

#: The family that speaks Gitea's API.
GITEA_FAMILY: tuple[str, ...] = (GITEA, FORGEJO, GOGS)

#: Hosts known by being what they are.
_KNOWN_HOSTS = {
    "codeberg.org": FORGEJO,
    "gitea.com": GITEA,
    "bitbucket.org": BITBUCKET,
    "gitlab.com": GITLAB,
}

#: Words a host is named with, tried in this order (``gitea`` before ``gitea-forgejo``...).
_WORDS = (
    (GITLAB, GITLAB),
    (FORGEJO, FORGEJO),
    (GITEA, GITEA),
    (GOGS, GOGS),
    (BITBUCKET, BITBUCKET),
)


def normalise_declared(value: Any) -> list[str]:
    """
    Check the hosts an operator declared.

    Args:
        value: A list of ``host=kind`` (or a comma-separated string).

    Returns:
        The entries, host lower-cased, without repeats.

    Raises:
        ConfigError: When an entry is not ``host=kind`` with a host name and a kind
            Noust speaks.
    """
    if value in (None, ""):
        return []
    items: Iterable[Any] = value.split(",") if isinstance(value, str) else value
    if isinstance(items, Mapping) or not isinstance(items, Iterable):
        raise ConfigError(
            "integrations.forge_hosts must be a list of host=kind",
            details="Such as git.example.com=gitea.",
        )
    found: dict[str, str] = {}
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        host, separator, kind = text.partition("=")
        host, kind = host.strip().lower(), kind.strip().lower()
        if (
            not separator
            or not host
            or "/" in host
            or ":" in host
            or " " in host
            or kind not in DECLARABLE
        ):
            raise ConfigError(
                f"integrations.forge_hosts: {text!r} is not a host=kind",
                details=f"Use a bare host name and one of: {', '.join(DECLARABLE)}.",
            )
        found.setdefault(host, kind)
    return [f"{host}={kind}" for host, kind in found.items()]


def declared(config: Any | None = None) -> dict[str, str]:
    """
    Args:
        config: The configuration; the shared one by default.

    Returns:
        The hosts the operator named, host to kind. Entries that are not valid are
        left out (a hand edit must not stop a deployment).
    """
    if config is None:
        from noust.core.config import Config

        config = Config()
    try:
        entries = normalise_declared(config.get("integrations.forge_hosts", []))
    except ConfigError:
        return {}
    return dict(entry.split("=", 1) for entry in entries)


def kind_of_host(host: str | None, config: Any | None = None) -> str | None:
    """
    Say what kind of code host an address is.

    Args:
        host: A host name, as a source spells it.
        config: The configuration, for the hosts the operator declared.

    Returns:
        One of :data:`DECLARABLE`; None for a host nothing says anything about. What the
        operator declared wins over what the name suggests.
    """
    if not host:
        return None
    name = host.strip().lower()
    told = declared(config).get(name)
    if told is not None:
        return told
    if name in _KNOWN_HOSTS:
        return _KNOWN_HOSTS[name]
    for word, kind in _WORDS:
        if word in name:
            return kind
    return None


def api_family(kind: str | None) -> str | None:
    """
    Args:
        kind: A kind of host.

    Returns:
        ``gitea`` for Gitea, Forgejo and Gogs, which share an API and a payload
        format; the kind itself for any other.
    """
    return GITEA if kind in GITEA_FAMILY else kind
