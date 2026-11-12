# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Proving, from the machine's side, that a folder is the one Noust sees: the witness container.

A stack's ``./data`` is resolved by the Docker daemon on the machine, not by the container Noust runs in,
so a folder the container sees under another path is a folder a stack mounts empty, and nothing fails. The
proof is a file Noust has written in the folder and a throw-away container, started by the daemon with the
folder mounted from the machine's side, that has to read it back. The two commands are the door's
(rule 3: nothing outside this package writes ``docker`` argument lists), and :mod:`noust.central.deploy_check`
asks them.

``--mount`` and not ``-v``, on purpose: ``-v`` makes a folder that is missing, empty and owned by root, on
the NAS, and a check must not leave that behind; ``--mount`` refuses.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from noust.core.docker.reading import HELPER_LABEL
from noust.core.runner import CommandResult, CommandRunner

__all__ = ["MOUNT_POINT", "NO_SOURCE", "mount_option", "own_image", "read_back"]

#: Where the folder is seen from inside the witness container.
MOUNT_POINT = "/witness"

#: What Docker says when a ``--mount`` names a folder the machine does not have.
NO_SOURCE = "bind source path does not exist"

#: Seconds a question to Docker gets, and the witness, which may have to pull an image first.
_ASK_TIMEOUT = 30
_WITNESS_TIMEOUT = 180


def mount_option(folder: Path) -> str:
    """
    Write the ``--mount`` option that binds a folder of the machine, read-only.

    The option is a CSV record, so a folder whose name holds a comma or a quote is quoted as CSV is
    rather than split in two.

    Args:
        folder: The folder, as the container sees it.

    Returns:
        The option's value.
    """
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(
        ["type=bind", f"source={folder}", f"target={MOUNT_POINT}", "readonly"]
    )
    return buffer.getvalue()


def own_image(runner: CommandRunner, name: str, fallback: str) -> str:
    """
    Pick the image the witness container starts from.

    The central's own, by its identifier: it is on the machine for certain, because it is running there,
    so the check does not need the network. Docker names a container by its host name, which is what a
    container's host name is unless somebody changed it.

    Args:
        runner: Asks Docker.
        name: This container's host name.
        fallback: The image to use when that fails (it is not a container, or it was renamed).

    Returns:
        An image reference.
    """
    answer = runner.run(
        ["docker", "inspect", "--type", "container", "--format", "{{.Image}}", name],
        timeout=_ASK_TIMEOUT,
    )
    identifier = answer.stdout.strip()
    if answer.success and identifier.startswith("sha256:"):
        return identifier
    return fallback


def read_back(runner: CommandRunner, folder: Path, filename: str, image: str) -> CommandResult:
    """
    Start the witness container and read a file of the folder through the machine's own mount.

    Args:
        runner: Runs Docker.
        folder: The folder, as the container sees it.
        filename: The witness file in it.
        image: The image to start from.

    Returns:
        What the container printed, or what Docker said when it could not start (``NO_SOURCE`` when the
        machine does not have the folder at that path).
    """
    return runner.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--label",
            f"{HELPER_LABEL}=deploy-here-check",
            "--mount",
            mount_option(folder),
            "--entrypoint",
            "cat",
            image,
            f"{MOUNT_POINT}/{filename}",
        ],
        timeout=_WITNESS_TIMEOUT,
    )
