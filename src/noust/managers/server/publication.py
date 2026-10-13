# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which distributions already serve a Noust version: the barrier before it is announced.

A GitHub release exists the moment its tag is pushed, but the packages behind
``apt``, ``dnf`` and ``zypper`` are built by OBS afterwards, one repository per
distribution, and each is published when its own build ends. 3.3.1 was served by
Debian 12 while Ubuntu 24.04 and 22.04 still offered 3.3.0 (owner items 80 and 81),
so a fleet is in a mixed state for a while after every release. Announcing before
every target serves the version sends operators to an upgrade that does nothing on
some of their servers.

This module reads what the clients read: the published index of each repository of
:data:`noust.managers.server.platforms.TARGETS` (the one table of releases, rule 3),
through the readers the update check already uses
(:mod:`noust.core.package_index`). It reads the product and the transitional package
whose only job is to depend on it, because a repository with one and not the other
makes ``apt upgrade`` hold the machine back. ``scripts/obs_published.py`` runs it as
a release gate and ``release.yml`` waits on it.

A repository of a ``supported`` release is required. One of a ``best-effort``
release is reported but never holds the release back: its build is allowed to lag
or to be retired without the release being incomplete.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from noust.core import package_index
from noust.managers.server import platforms

logger = logging.getLogger(__name__)

#: The packages every repository of a family serves: the product, and the
#: transitional one that depends on it (docs/RENAME.md).
PACKAGES: dict[str, tuple[str, ...]] = {
    "apt": (package_index.DEB_PACKAGE, package_index.LEGACY_DEB_PACKAGE),
    "dnf": (package_index.RPM_PACKAGE, package_index.LEGACY_RPM_PACKAGE),
    "zypper": (package_index.RPM_PACKAGE, package_index.LEGACY_RPM_PACKAGE),
}

#: Statuses of a release whose repository is built for it (not pip-only, not refused).
BUILT_STATUSES = (platforms.SUPPORTED, platforms.BEST_EFFORT)

#: The reader of one package in one repository. Replaced by tests, which have no network.
Reader = Callable[[str, str, str], str | None]


@dataclass(frozen=True)
class RepositoryState:
    """
    What one OBS repository serves of a version.

    Attributes:
        repo: The repository directory (``Debian_12``).
        family: ``apt``, ``dnf`` or ``zypper``.
        releases: The names of the releases that install from it.
        required: Whether a supported release installs from it.
        served: Per package, the version the repository's index lists, or None
            when the package is absent or the index could not be read.
    """

    repo: str
    family: str
    releases: tuple[str, ...]
    required: bool
    served: dict[str, str | None] = field(default_factory=dict)

    def has(self, version: str) -> bool:
        """
        Tell whether every package of the family is served at a version.

        Args:
            version: The upstream version, without the package revision.

        Returns:
            True when each package of :data:`PACKAGES` lists exactly that version.
        """
        return bool(self.served) and all(
            self.served.get(name) == version for name in PACKAGES[self.family]
        )

    def lagging(self, version: str) -> list[str]:
        """
        Name the packages that do not yet serve a version, with what they do serve.

        Args:
            version: The version being waited for.

        Returns:
            One ``name (serves X)`` phrase per package that is not at the version.
        """
        found = []
        for name in PACKAGES[self.family]:
            have = self.served.get(name)
            if have != version:
                found.append(f"{name} ({'serves ' + have if have else 'not listed or unreadable'})")
        return found


@dataclass(frozen=True)
class Publication:
    """
    What every distribution serves of one version.

    Attributes:
        version: The version asked after.
        repositories: One state per repository, in the table's order.
    """

    version: str
    repositories: tuple[RepositoryState, ...]

    @property
    def missing(self) -> tuple[RepositoryState, ...]:
        """The required repositories that do not serve the version yet."""
        return tuple(r for r in self.repositories if r.required and not r.has(self.version))

    @property
    def late(self) -> tuple[RepositoryState, ...]:
        """The best-effort repositories that do not serve the version (reported, not blocking)."""
        return tuple(r for r in self.repositories if not r.required and not r.has(self.version))

    @property
    def ready(self) -> bool:
        """Whether every required repository serves the version."""
        return not self.missing

    def to_markdown(self) -> str:
        """
        Render the state as a table for a workflow's summary.

        Returns:
            Markdown: one row per repository with what it serves and a verdict.
        """
        lines = [
            f"### Noust {self.version} on the distribution repositories",
            "",
            "| Repository | Releases | Serves | State |",
            "|---|---|---|---|",
        ]
        for repo in self.repositories:
            serves = (
                ", ".join(
                    f"{name} {repo.served.get(name) or 'none'}" for name in PACKAGES[repo.family]
                )
                or "unreadable"
            )
            if repo.has(self.version):
                state = "published"
            else:
                state = "waiting" if repo.required else "late (best effort, not blocking)"
            lines.append(f"| `{repo.repo}` | {', '.join(repo.releases)} | {serves} | {state} |")
        lines.append("")
        lines.append(
            "Every required repository serves it: safe to announce."
            if self.ready
            else "Not ready: " + ", ".join(f"`{r.repo}`" for r in self.missing) + " still wait."
        )
        return "\n".join(lines) + "\n"


def repositories(targets: Iterable[platforms.Target] = platforms.TARGETS) -> list[RepositoryState]:
    """
    List the repositories Noust is built into, from the one table of releases.

    Fedora 44 installs from the Fedora 43 repository, so several releases can
    share one; a repository is required when any release that uses it is supported.

    Args:
        targets: The releases; the table by default.

    Returns:
        One empty state per distinct repository, in the table's order.
    """
    order: list[str] = []
    families: dict[str, str] = {}
    releases: dict[str, list[str]] = {}
    required: dict[str, bool] = {}
    for target in targets:
        if target.repo is None or target.status not in BUILT_STATUSES:
            continue
        if target.repo not in families:
            order.append(target.repo)
            families[target.repo] = target.family
            releases[target.repo] = []
            required[target.repo] = False
        releases[target.repo].append(target.name)
        required[target.repo] = required[target.repo] or target.status == platforms.SUPPORTED
    return [
        RepositoryState(
            repo=repo,
            family=families[repo],
            releases=tuple(releases[repo]),
            required=required[repo],
        )
        for repo in order
    ]


def read_package(family: str, repo: str, package: str) -> str | None:
    """
    Read one package's version from a repository's published index.

    Args:
        family: ``apt``, ``dnf`` or ``zypper``.
        repo: The repository directory under the OBS project.
        package: The package name.

    Returns:
        The upstream version it lists, or None when it is absent or the index
        could not be read (an index OBS is still writing is simply not there).
    """
    base = f"{platforms.REPOSITORY_BASE}/{repo}"
    if family != "apt":
        return package_index.rpm_index_latest(base, package)
    for group in package_index.AptSource(base, "./").index_urls("amd64"):
        for url in group:
            try:
                text = package_index.decompress(package_index.fetch(url)).decode("utf-8", "replace")
            except package_index.FETCH_ERRORS as exc:
                logger.debug("Could not read %s: %s", url, exc)
                continue
            return package_index.packages_version(text, package)
    return None


def check(
    version: str,
    *,
    reader: Reader = read_package,
    targets: Iterable[platforms.Target] = platforms.TARGETS,
) -> Publication:
    """
    Read what every repository serves and judge it against a version.

    Args:
        version: The upstream version being published, such as ``3.4.0``.
        reader: Reads one package of one repository; replaced in tests.
        targets: The releases; the table by default.

    Returns:
        The publication state; ``ready`` when every required repository serves
        the product and the transitional package at the version.
    """
    states = []
    for state in repositories(targets):
        served = {name: reader(state.family, state.repo, name) for name in PACKAGES[state.family]}
        states.append(
            RepositoryState(
                repo=state.repo,
                family=state.family,
                releases=state.releases,
                required=state.required,
                served=served,
            )
        )
    return Publication(version=version, repositories=tuple(states))
