# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Read other platforms' configuration from a repository.

One module per platform, each with ``PLATFORM``, ``FILES``, ``detect(root)``
and ``read(root) -> Proposal``. This package is the one place that knows
which platforms there are: ``noust import --from`` and the new-app wizard's
inspection (:mod:`noust.deployers.inspect`, which also adds :data:`PLATFORM_FILES`
to its sparse checkout) both go through :func:`propose`.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from noust.core.exceptions import ValidationError
from noust.deployers.importers import coolify, dokploy, heroku, railway, render, vercel
from noust.deployers.importers.base import Proposal, ProposedEnv

__all__ = [
    "PLATFORMS",
    "PLATFORM_FILES",
    "REMOTE_PLATFORMS",
    "UNSUPPORTED_PLATFORMS",
    "Proposal",
    "ProposedEnv",
    "detect_platforms",
    "propose",
    "read_platform",
]

#: Every platform, in the order a repository carrying several is read.
_MODULES: tuple[ModuleType, ...] = (vercel, railway, render, heroku)

#: Platform names ``--from`` accepts for a repository's files.
PLATFORMS: tuple[str, ...] = tuple(module.PLATFORM for module in _MODULES)

#: Platforms that keep an application's configuration in their own database and
#: are read through their API (:mod:`noust.deployers.importers.remote`), with a
#: server's address and a read-only token instead of a repository.
REMOTE_PLATFORMS: dict[str, ModuleType] = {
    coolify.PLATFORM: coolify,
    dokploy.PLATFORM: dokploy,
}

#: Every file an importer reads, relative to the repository.
PLATFORM_FILES: tuple[str, ...] = tuple(name for module in _MODULES for name in module.FILES)

#: Platforms asked for often enough to deserve an answer, and why there is
#: nothing to read.
UNSUPPORTED_PLATFORMS: dict[str, str] = {}


def _module(platform: str) -> ModuleType:
    """
    Find a platform's importer.

    Args:
        platform: Its name.

    Returns:
        The module.

    Raises:
        ValidationError: No importer by that name; for a known platform with
            nothing to read, the reason.
    """
    for module in _MODULES:
        if module.PLATFORM == platform:
            return module
    if platform in REMOTE_PLATFORMS:
        raise ValidationError(
            f"{platform.capitalize()} is read through its API, not from a repository",
            details=f"Give the server's address and a read-only token: noust import --from "
            f"{platform} --url https://{platform}.example.com --token-file FILE.",
        )
    if platform in UNSUPPORTED_PLATFORMS:
        raise ValidationError(
            f"Noust cannot import from {platform.capitalize()}",
            details=UNSUPPORTED_PLATFORMS[platform],
        )
    raise ValidationError(
        f"Unknown platform: {platform}", details=f"Choose one of: {', '.join(PLATFORMS)}."
    )


def detect_platforms(root: Path) -> list[str]:
    """
    Name every platform whose configuration a repository carries.

    Args:
        root: The repository.

    Returns:
        The platforms, in :data:`PLATFORMS` order.
    """
    return [module.PLATFORM for module in _MODULES if module.detect(root)]


def read_platform(platform: str, root: Path) -> Proposal:
    """
    Read one platform's configuration from a repository.

    Args:
        platform: One of :data:`PLATFORMS`.
        root: The repository.

    Returns:
        The proposal.

    Raises:
        ValidationError: The platform is unknown or has nothing to read, the
            repository carries none of its files, or one cannot be read.
    """
    module = _module(platform)
    if not root.is_dir():
        raise ValidationError(
            f"{root} is not a directory",
            details="Give the directory the repository is checked out in.",
        )
    if not module.detect(root):
        raise ValidationError(
            f"No {platform} configuration in {root}",
            details=f"Noust reads {', '.join(module.FILES)} at the root of the repository.",
        )
    proposal: Proposal = module.read(root)
    return proposal


def propose(root: Path) -> Proposal | None:
    """
    Read the first platform configuration a repository carries.

    What the wizard's inspection shows. A file that cannot be read does not
    fail the inspection: the proposal comes back empty, with why in its
    warnings.

    Args:
        root: The repository.

    Returns:
        The proposal, or None when the repository carries none.
    """
    found = detect_platforms(root)
    if not found:
        return None
    platform = found[0]
    try:
        proposal = read_platform(platform, root)
    except ValidationError as exc:
        proposal = Proposal(platform=platform)
        proposal.warn(f"{exc.message}. {exc.details}".strip())
    for other in found[1:]:
        proposal.warn(
            f"The repository also has {other} configuration; read it with "
            f"'noust import --from {other}'."
        )
    return proposal
