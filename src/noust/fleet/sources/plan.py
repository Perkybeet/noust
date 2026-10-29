# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which leases a server needs: the repositories its applications deploy, grouped by installation.

Pure: it is given the repositories and a way to find the installation that reaches each, and
answers the leases to hold. A lease is one token, so it is one installation (a token is issued from
one) and at most :data:`~noust.integrations.github.app.MAX_TOKEN_REPOSITORIES` repositories (what
GitHub lets a token be limited to); a server that serves more of one account has several.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from noust.core.github_host import integration_repository
from noust.integrations.github.app import MAX_TOKEN_REPOSITORIES

__all__ = ["LeaseSpec", "Plan", "make_plan", "repositories_of"]


@dataclass(frozen=True)
class LeaseSpec:
    """
    One lease a server should hold.

    Attributes:
        key: Its name on the server: host, installation and part, so a renewal replaces it.
        host: The GitHub host.
        installation_id: The installation the token is issued from.
        repositories: ``owner/repo`` in lower case, sorted.
    """

    key: str
    host: str
    installation_id: int
    repositories: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    """
    What a server needs.

    Attributes:
        specs: The leases, by key.
        uncovered: Repositories no installation of the App reaches (their owner has none).
    """

    specs: tuple[LeaseSpec, ...]
    uncovered: tuple[str, ...]


def repositories_of(applications: Iterable[Mapping[str, Any]]) -> set[str]:
    """
    Name the repositories of the App that some applications are deployed from.

    Args:
        applications: A server's applications as it lists them (``source`` is the field read).

    Returns:
        ``owner/repo`` in lower case; applications from anywhere else are not in it.
    """
    found: set[str] = set()
    for application in applications:
        source = application.get("source")
        repository = integration_repository(source if isinstance(source, str) else None)
        if repository:
            found.add(repository.lower())
    return found


def make_plan(
    repositories: Iterable[str],
    *,
    host: str,
    installation_of: Callable[[str], int | None],
) -> Plan:
    """
    Decide the leases a server needs for some repositories.

    Args:
        repositories: ``owner/repo`` it serves.
        host: The GitHub host of the App.
        installation_of: The installation that reaches a repository, or None.

    Returns:
        The leases, one or more per installation, and the repositories nothing reaches.
    """
    by_installation: dict[int, list[str]] = {}
    uncovered: list[str] = []
    for repository in sorted({r.lower() for r in repositories}):
        installation = installation_of(repository)
        if installation is None:
            uncovered.append(repository)
        else:
            by_installation.setdefault(installation, []).append(repository)
    specs: list[LeaseSpec] = []
    for installation in sorted(by_installation):
        names = by_installation[installation]
        for part, start in enumerate(range(0, len(names), MAX_TOKEN_REPOSITORIES)):
            specs.append(
                LeaseSpec(
                    key=f"{host}:{installation}:{part}",
                    host=host,
                    installation_id=installation,
                    repositories=tuple(names[start : start + MAX_TOKEN_REPOSITORIES]),
                )
            )
    return Plan(specs=tuple(specs), uncovered=tuple(uncovered))
