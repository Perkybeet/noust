# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which GitHub this server's integration talks to: github.com, or a GitHub Enterprise host (A-05).

The GitHub App, its installation tokens, deployment statuses, pull request comments and the
repositories it clones all assumed ``github.com``. ``github.host`` names another: a GitHub
Enterprise Server (``ghe.example.com``, API at ``/api/v3``) or a GitHub Enterprise Cloud with data
residency (``acme.ghe.com``, API at ``api.acme.ghe.com``). It is one setting, read here and nowhere
else, so the API root, the web address, the repository a source names and the git credential an
installation clones with cannot disagree.

One server has one GitHub App and so one host. Set it before the App is created: the App lives on
the host that created it, and changing the host afterwards means creating the App again.

The host is the operator's. It is still asked to pass the guard every outbound address an operator
types passes (:func:`noust.core.notifier.require_public_destination`): a GitHub Enterprise Server in
a private network is allowed by listing it under ``notifications.allow_private_hosts``, which the
refusal says.
"""

from __future__ import annotations

import re

from noust.core.config import Config
from noust.core.exceptions import ConfigError

#: The default, and the only host before 4.0.
GITHUB_COM = "github.com"

#: ``Config`` key holding the host; empty means :data:`GITHUB_COM`.
CONFIG_KEY = "github.host"

#: A host name or an address, with an optional port: nothing a URL could carry besides.
_HOST = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"(?::[0-9]{1,5})?$"
)

#: GitHub Enterprise Cloud with data residency: the API is on ``api.`` before the host.
_RESIDENCY_SUFFIX = ".ghe.com"


def validate_host(value: object) -> str:
    """
    Check a GitHub host the operator typed.

    Args:
        value: The candidate: a host name or address, with an optional port.

    Returns:
        The host, lowercased; an empty string for none (github.com).

    Raises:
        ConfigError: It carries a scheme, a path, a user or anything else but a host.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if "://" in text or "/" in text or "@" in text or " " in text or not _HOST.fullmatch(text):
        raise ConfigError(
            f"github.host must be a host name, not {text!r}",
            details="Give the host alone, such as ghe.example.com or acme.ghe.com; the scheme "
            "and the path are derived (https://HOST and https://HOST/api/v3).",
        )
    return text.lower()


def configured_host(config: Config | None = None) -> str:
    """
    Name the GitHub host this server's integration uses.

    Args:
        config: The configuration; the process-wide one when None.

    Returns:
        The host the operator set, or ``github.com``.
    """
    value = (config or Config()).get(CONFIG_KEY, "")
    return (validate_host(value) if value else "") or GITHUB_COM


def is_enterprise(host: str | None = None) -> bool:
    """
    Tell whether a host is not github.com.

    Args:
        host: The host; the configured one when None.

    Returns:
        True for GitHub Enterprise Server or Cloud with data residency.
    """
    return (host or configured_host()) != GITHUB_COM


def web_url(host: str | None = None) -> str:
    """
    Give the address of GitHub's website, where Apps are created and installed.

    Args:
        host: The host; the configured one when None.

    Returns:
        ``https://HOST``.
    """
    return f"https://{host or configured_host()}"


def api_url(host: str | None = None) -> str:
    """
    Give the root of GitHub's REST API on a host.

    Args:
        host: The host; the configured one when None.

    Returns:
        ``https://api.github.com`` for github.com, ``https://api.HOST`` for a Cloud residency
        host, ``https://HOST/api/v3`` for Enterprise Server.
    """
    chosen = host or configured_host()
    if chosen == GITHUB_COM:
        return "https://api.github.com"
    if chosen.split(":", 1)[0].endswith(_RESIDENCY_SUFFIX):
        return f"https://api.{chosen}"
    return f"https://{chosen}/api/v3"


def source_for(full_name: str, host: str | None = None) -> str:
    """
    Spell the source that deploys a repository of the integration's host.

    Args:
        full_name: ``owner/repo``.
        host: The host; the configured one when None.

    Returns:
        ``github:owner/repo`` for github.com, whose shorthand it is; the https clone URL on any
        other host.
    """
    chosen = host or configured_host()
    if chosen == GITHUB_COM:
        return f"github:{full_name}"
    return f"{web_url(chosen)}/{full_name}.git"


def hostname(host: str | None = None) -> str:
    """
    Give the host without its port, which is what a git URL's host is.

    Args:
        host: The host; the configured one when None.

    Returns:
        The host name.
    """
    return (host or configured_host()).split(":", 1)[0]


def repository_hosts(config: Config | None = None) -> tuple[str, ...]:
    """
    Name the hosts whose repositories the GitHub App integration serves.

    Args:
        config: The configuration; the process-wide one when None.

    Returns:
        The configured host's name, with and without its port: a clone URL names the host
        and a webhook payload carries it in ``html_url``.
    """
    host = configured_host(config)
    name = hostname(host)
    return (host, name) if host != name else (name,)


def integration_repository(source: str | None) -> str | None:
    """
    Name the repository of the GitHub the App integration serves, whichever way a source spells it.

    The one answer to "which repository of the integration's host does this source clone"
    for a push delivered to ``/hooks/github``, a deployment status and the installation that
    clones it. A source on another host (a repository of github.com when the App is on a
    GitHub Enterprise Server) is not the integration's.

    Args:
        source: A source as stored or given.

    Returns:
        ``owner/repo``; None when the source is not a repository of the configured host.
    """
    from noust.validators.source import github_repository

    return github_repository(source, hosts=repository_hosts())


def require_reachable(host: str | None = None) -> None:
    """
    Refuse an Enterprise host that resolves inside this machine's own networks.

    Args:
        host: The host; the configured one when None.

    Raises:
        ConfigError: The API address resolves to a private address that
            ``notifications.allow_private_hosts`` does not allow.
    """
    from noust.core.notifier import require_public_destination

    chosen = host or configured_host()
    if not is_enterprise(chosen):
        return
    try:
        require_public_destination(api_url(chosen), "the GitHub Enterprise server")
    except ValueError as exc:
        raise ConfigError(
            str(exc),
            details="A GitHub Enterprise Server in a private network is allowed by listing it: "
            f"noust config set notifications.allow_private_hosts '[{hostname(chosen)}]'.",
        ) from exc


__all__ = [
    "CONFIG_KEY",
    "GITHUB_COM",
    "api_url",
    "configured_host",
    "hostname",
    "integration_repository",
    "is_enterprise",
    "repository_hosts",
    "require_reachable",
    "source_for",
    "validate_host",
    "web_url",
]
