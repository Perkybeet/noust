# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Versions, and what a recipe asks of the machine and of Noust itself.

Light on purpose (it imports nothing of the deployers): the catalogue's search,
the update check and the install all read it. Coolify emits ``minversion`` and
``amd_only`` in its catalogue and nothing reads them; here a recipe that needs
a newer Noust, or an architecture this server is not, is refused with the
reason before anything is provisioned.
"""

from __future__ import annotations

import platform
import re

import noust
from noust.recipes.model import Recipe, RecipeError

_NUMBER = re.compile(r"^v?(\d+(?:\.\d+)*)(.*)$")

#: What ``platform.machine()`` says, as Docker spells the architecture.
_DOCKER_ARCHITECTURE = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm",
    "armv6l": "arm",
    "armhf": "arm",
    "i386": "386",
    "i686": "386",
    "riscv64": "riscv64",
}


def version_key(version: str) -> tuple[tuple[int, ...], int, str] | None:
    """
    Make a version comparable.

    Args:
        version: ``1.37.4``, ``v0.31.0``, ``12.2``, ``4.140.0-rc1``.

    Returns:
        The numbers, whether it is a final release (no suffix: a suffix such
        as ``-rc1`` sorts before the release), and the suffix; None for a
        version that does not start with numbers (``sha-1a2b3c4``).
    """
    match = _NUMBER.match(version.strip())
    if match is None:
        return None
    numbers = tuple(int(part) for part in match.group(1).split("."))
    suffix = match.group(2).lstrip("-+._")
    return numbers, 0 if suffix else 1, suffix


def is_newer(version: str, revision: int, *, than_version: str, than_revision: int) -> bool:
    """
    Tell whether a recipe version is newer than the one installed.

    The application's version decides; when it is the same, the recipe's own
    revision does (a fix to the recipe, such as a corrected variable). A
    version that cannot be ordered (a commit tag) counts as newer when it
    differs.

    Args:
        version: The catalogue's version.
        revision: The catalogue's revision of it.
        than_version: The installed version.
        than_revision: The installed revision.

    Returns:
        True when the catalogue's is ahead.
    """
    if version == than_version:
        return revision > than_revision
    ours, theirs = version_key(version), version_key(than_version)
    if ours is None or theirs is None:
        return True
    width = max(len(ours[0]), len(theirs[0]))

    def padded(numbers: tuple[int, ...]) -> tuple[int, ...]:
        return (*numbers, *([0] * (width - len(numbers))))

    return (padded(ours[0]), ours[1], ours[2]) > (padded(theirs[0]), theirs[1], theirs[2])


def host_architecture() -> str:
    """
    Name this machine's architecture the way Docker does.

    Returns:
        ``amd64``, ``arm64``, ``arm``, ``386`` or ``riscv64``; the machine's
        own name when it is none of those.
    """
    machine = platform.machine().lower()
    return _DOCKER_ARCHITECTURE.get(machine, machine)


def require_compatible(recipe: Recipe) -> None:
    """
    Refuse a recipe this Noust or this machine cannot run.

    Args:
        recipe: The recipe.

    Raises:
        RecipeError: It asks for a newer Noust than this one, or for
            architectures this machine is not one of.
    """
    if recipe.min_noust and is_newer(
        recipe.min_noust, 1, than_version=noust.__version__, than_revision=1
    ):
        raise RecipeError(
            f"{recipe.title} needs Noust {recipe.min_noust} or newer; this is Noust {noust.__version__}",
            details="Update Noust first (pip install --upgrade noust, or your system's package "
            "manager).",
        )
    architecture = host_architecture()
    if recipe.architectures and architecture not in recipe.architectures:
        raise RecipeError(
            f"{recipe.title} is not published for this server's architecture ({architecture})",
            details=f"It runs on {', '.join(recipe.architectures)}. Nothing was installed.",
        )
