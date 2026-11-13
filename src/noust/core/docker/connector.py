# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The connector of a Cloudflare tunnel as a container Noust starts, where a machine has Docker and no systemd.

On a server the connector is a unit (:mod:`noust.managers.tunnels` writes it). The central of a NAS has no
systemd, and a NAS is the machine a tunnel is most wanted on: its ports 80 and 443 are the NAS's own, so a
tunnel that dials out reaches an application by a name without opening anything. The connector is then one
more container, and this is the door to it (rule 3: one implementation of the tunnel, another engine behind
it).

- **The image is pinned by digest**, read from the registry that serves it with ``scripts/database_pins.py``
  and kept in step by its ``check``, never ``latest``: the connector runs beside everything on the NAS.
- **The token is a file, not an environment variable.** An environment variable becomes part of the
  container's configuration, which ``docker inspect`` prints to anyone who can use Docker. The token is
  written by the secret store (private, owned by the central's user) and mounted read-only at
  :data:`TOKEN_TARGET`; the only variable names it (``TUNNEL_TOKEN_FILE``), and carries a path.
- **It runs as the central's own user**, with a read-only file system, no capabilities and no way to gain
  any, because that user is the one that owns the token file; and Docker's restart policy brings it back
  when the NAS restarts, which is what keeps it alive instead of a unit.
- **It reaches what it carries through the machine's gateway**: an application is published on a port of the
  machine, and the container is told the name :data:`HOST_ALIAS` for it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from noust.core.docker import names
from noust.core.exceptions import ServiceError
from noust.core.runner import CommandRunner

__all__ = [
    "CONNECTOR_IMAGE",
    "HOST_ALIAS",
    "LABEL",
    "TOKEN_TARGET",
    "remove",
    "run_argv",
    "start",
    "state",
    "unit_state",
]

#: The connector: Cloudflare's own image, by tag and by digest (an index of amd64 and arm64).
CONNECTOR_IMAGE = (
    "cloudflare/cloudflared:2026.11.0"
    "@sha256:b94b96edf2c9c4360f8879c8580cfaa99077f19c849ec595ca2f711b27d4f344"
)

#: What Noust labels its connector with. A claim, like ``io.noust.helper``: the store decides whose a
#: container is, and a label can only take access away.
LABEL = "io.noust.tunnel"

#: Where the token file is inside the container.
TOKEN_TARGET = "/run/secrets/tunnel_token"  # noqa: S105 - a path, the secret is the file's content

#: What the connector calls the machine it runs on: Docker maps it to the host's gateway.
HOST_ALIAS = "host.docker.internal"

#: Seconds ``docker run`` may take: it pulls the image the first time.
START_TIMEOUT = 300

#: Seconds an ordinary Docker command may take.
ASK_TIMEOUT = 60

#: What a path given to ``--mount`` may be made of: a comma or a quote would end the option and start another.
_MOUNTABLE = re.compile(r"[A-Za-z0-9_./-]+")


def run_argv(name: str, token_file: Path, *, uid: int, gid: int) -> list[str]:
    """
    Build the command that starts the connector of a tunnel.

    Args:
        name: The tunnel's name.
        token_file: The file that holds the token, on the machine (the central sees the same path).
        uid: The user the container runs as: the central's.
        gid: Its group.

    Returns:
        The argument list: no shell, and nothing secret in it, only the path of the file.

    Raises:
        ServiceError: The path cannot be given to ``--mount`` without ending the option.
    """
    if not _MOUNTABLE.fullmatch(str(token_file)):
        raise ServiceError(
            f"The token file of {name} is at a path Docker cannot be given: {token_file}",
            details="Keep Noust's data folder at a path of letters, digits, '.', '_', '-' and '/'.",
        )
    return [
        "docker",
        "run",
        "--detach",
        "--name",
        names.tunnel_container(name),
        "--restart",
        "unless-stopped",
        "--label",
        f"{LABEL}={name}",
        "--user",
        f"{uid}:{gid}",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--add-host",
        f"{HOST_ALIAS}:host-gateway",
        "--mount",
        f"type=bind,source={token_file},target={TOKEN_TARGET},readonly",
        "--env",
        f"TUNNEL_TOKEN_FILE={TOKEN_TARGET}",
        CONNECTOR_IMAGE,
        "tunnel",
        "--no-autoupdate",
        "run",
    ]


def remove(runner: CommandRunner, name: str) -> None:
    """
    Remove the connector's container, running or not. One that is not there is nothing to remove.

    Args:
        runner: Runs Docker.
        name: The tunnel's name.

    Raises:
        ServiceError: Docker refused, with its own words.
    """
    container = names.tunnel_container(name)
    done = runner.run(["docker", "rm", "--force", container], timeout=ASK_TIMEOUT)
    if done.success or "No such container" in (done.stderr or ""):
        return
    raise ServiceError(
        f"Docker could not remove the connector of {name}",
        details=(done.stderr or done.stdout).strip() or f"See: docker logs {container}",
    )


def start(runner: CommandRunner, name: str, token_file: Path) -> None:
    """
    Start the connector of a tunnel, replacing one an interrupted creation left.

    Args:
        runner: Runs Docker.
        name: The tunnel's name.
        token_file: The file that holds the token.

    Raises:
        ServiceError: The connector did not start, with Docker's own words.
    """
    remove(runner, name)
    argv = run_argv(name, token_file, uid=os.getuid(), gid=os.getgid())
    started = runner.run(argv, timeout=START_TIMEOUT)
    if not started.success:
        raise ServiceError(
            f"The connector of {name} did not start",
            details=(started.stderr or started.stdout).strip()
            or f"See: docker logs {names.tunnel_container(name)}",
        )


def state(runner: CommandRunner, name: str) -> str:
    """
    Ask Docker what the connector's container is doing.

    Args:
        runner: Runs Docker.
        name: The tunnel's name.

    Returns:
        Docker's own word (``running``, ``restarting``, ``exited``...), or ``missing`` when there is no
        such container.
    """
    asked = runner.run(
        [
            "docker",
            "inspect",
            "--type",
            "container",
            "--format",
            "{{.State.Status}}",
            names.tunnel_container(name),
        ],
        timeout=ASK_TIMEOUT,
    )
    return asked.stdout.strip() if asked.success and asked.stdout.strip() else "missing"


def unit_state(docker_says: str) -> str:
    """
    Say a container's state in the words a unit's are said in.

    Args:
        docker_says: What :func:`state` returned.

    Returns:
        ``active`` (it runs), ``activating`` (Docker is starting it or restarting it) or ``inactive``.
    """
    if docker_says == "running":
        return "active"
    if docker_says in ("restarting", "created"):
        return "activating"
    return "inactive"
