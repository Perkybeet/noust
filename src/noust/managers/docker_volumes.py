# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The two commands that move a Docker named volume's contents to and from a host archive.

A named volume is only reachable from inside Docker, so a throw-away Alpine
container mounts it and ``tar`` does the work. An application backup that
carries volumes (:mod:`noust.managers.backup_manager`) and a volume backup
policy (:mod:`noust.managers.volume_backups`) both build the argv here, so
there is one spelling of it (rule 3).

The container is the one image :data:`HELPER_IMAGE`, named by tag and by digest like the images of
the database engines (:mod:`noust.managers.database.instance_catalog`): a copy must not depend on
what a registry serves under ``alpine`` that day. The digest is the one of the image index,
read from the registry by ``scripts/database_pins.py resolve alpine:<tag>`` and never typed from
memory; a newer one is a change of this line, made on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

from noust.core.docker.reading import HELPER_LABEL

__all__ = [
    "DOCKER_VOLUME_NAME",
    "HELPER_IMAGE",
    "HELPER_LABEL_ARGS",
    "docker_archive_argv",
    "docker_restore_argv",
]

#: The image every copy and restore of a volume runs in (Alpine 3.24, read on 2026-11-12).
HELPER_IMAGE = "alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf"

#: What a helper container says of itself, so that the gate to Docker knows it is Noust's and not
#: somebody's (:mod:`noust.core.docker.owners`): it ends with the copy and nobody changes it.
HELPER_LABEL_ARGS = ["--label", f"{HELPER_LABEL}=volume-backup"]

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
        *HELPER_LABEL_ARGS,
        HELPER_IMAGE,
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
        *HELPER_LABEL_ARGS,
        HELPER_IMAGE,
        "sh",
        "-c",
        'find /data -mindepth 1 -delete && tar xzf "/backup/$1" -C /data',
        "sh",
        filename,
    ]
