# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Files a deployment is given once: a recipe's configuration, a PHP application's ``wp-config.php``.

A recipe can ship a file the application needs and the repository does not
carry (a configuration with the domain and a database password in it). On the
release layout the file is written into ``shared/`` the first time and linked
into every release, so it outlives releases and an edit the operator makes is
never overwritten; in place it is written into the tree. One implementation of
that for every deployer (the PHP one used to have its own).

Nothing is written through a link: a path whose ancestor is a symbolic link or
a file is refused, because the tree and ``shared/`` are places a repository's
contents have been.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from noust.core.exceptions import DeploymentError
from noust.core.fs import FileSystem
from noust.core.logger import Logger
from noust.deployers.releases import first_obstacle, persistent_path

#: What a seeded file is written with unless its recipe says: readable by the
#: application's group, not by everyone.
DEFAULT_MODE = 0o640


def seeded_paths(files: Mapping[str, str]) -> dict[str, str]:
    """
    Validate the paths of the files to seed.

    Args:
        files: Relative path to content.

    Returns:
        The same, with every path in its normal form.

    Raises:
        DeploymentError: A path is empty, absolute or climbs with ``..``.
    """
    return {str(persistent_path(path)): str(content) for path, content in dict(files).items()}


def seed_files(
    root: Path,
    files: Mapping[str, str],
    modes: Mapping[str, int],
    *,
    fs: FileSystem,
    logger: Logger,
    label: str,
) -> list[str]:
    """
    Write each file that is not there yet.

    Args:
        root: The directory the paths are relative to (``shared/``, or the tree).
        files: Relative path to content.
        modes: Relative path to permissions; :data:`DEFAULT_MODE` otherwise.
        fs: The filesystem every change goes through.
        logger: Where progress is reported.
        label: How to name ``root`` in a line of progress (``shared/``).

    Returns:
        The paths written. A file that exists is left exactly as it is.

    Raises:
        DeploymentError: ``root`` is a link, or a parent of a file is a link or
            a file.
    """
    if root.is_symlink():
        raise DeploymentError(
            f"Refusing to write into {root}: it is a symlink",
            details="Replace it with a directory.",
        )
    written: list[str] = []
    for raw, content in files.items():
        relative = PurePosixPath(raw)
        target = root / relative
        if os.path.lexists(target):
            continue
        blocked = first_obstacle(root, relative)
        if blocked is not None:
            raise DeploymentError(
                f"Refusing to create {target}: {blocked} is not a plain directory",
                details="A symlink or a file there would send the write elsewhere. Replace "
                "it with a directory.",
            )
        fs.make_dir(target.parent, parents=True)
        fs.write_text(target, content, mode=modes.get(raw, DEFAULT_MODE))
        logger.substep(f"Wrote {label}{relative}")
        written.append(raw)
    return written
