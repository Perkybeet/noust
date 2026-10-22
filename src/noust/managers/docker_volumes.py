# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The two commands that move a Docker named volume's contents to and from a host archive.

A named volume is only reachable from inside Docker, so a throw-away Alpine
container mounts it and ``tar`` does the work. An application backup that
carries volumes (:mod:`noust.managers.backup_manager`) and a volume backup
policy (:mod:`noust.managers.volume_backups`) both build the argv here, so
there is one spelling of it (rule 3).
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["DOCKER_VOLUME_NAME", "docker_archive_argv", "docker_restore_argv"]

#: A Docker volume name, as Docker accepts one.
DOCKER_VOLUME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")


def docker_archive_argv(volume: str, directory: Path, filename: str) -> list[str]:
    """
    Build the command that tars a Docker volume into a file of a host directory.

    The volume is mounted read-only and the archive is written into the
    directory mounted beside it.

    Args:
        volume: The volume's name.
        directory: The host directory the archive is written in.
        filename: The archive's name there.

    Returns:
        The argv.
    """
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{volume}:/data:ro",
        "-v",
        f"{directory}:/backup",
        "alpine",
        "tar",
        "czf",
        f"/backup/{filename}",
        "-C",
        "/data",
        ".",
    ]


def docker_restore_argv(volume: str, directory: Path, filename: str) -> list[str]:
    """
    Build the command that replaces a Docker volume's contents with an archive.

    Unlike the merge an application backup does, the volume is emptied first
    (everything in it, hidden files included), so what comes back is what the
    backup held and not that plus what was written since. The archive's name is
    a positional parameter of the container's shell, never part of its script.

    Args:
        volume: The volume's name.
        directory: The host directory holding the archive, mounted read-only.
        filename: The archive's name there.

    Returns:
        The argv.
    """
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{volume}:/data",
        "-v",
        f"{directory}:/backup:ro",
        "alpine",
        "sh",
        "-c",
        'find /data -mindepth 1 -delete && tar xzf "/backup/$1" -C /data',
        "sh",
        filename,
    ]
