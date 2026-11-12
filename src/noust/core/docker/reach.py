# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How Noust reaches what it deployed when it does not share the machine's loopback.

On a server a deployment answers on 127.0.0.1 and the health gate asks it there, on the port the
compose file publishes. In the container of a NAS that address is the container's own: what Noust
deployed is in another container on the machine's Docker, so 127.0.0.1 reaches nothing. The two share
a Docker network instead. Noust joins the network of what it deployed (once: joining is idempotent, and a
race that joined it first is not a failure) and asks the service at its address there, on the port the
service listens on and not the one published on the machine, which is a port of the NAS and may be taken
by something else.

What is read of Docker is the one command that says it (``docker inspect --format``, in a form the oldest
Docker Noust works with prints) and never guessed: a container with no address on any network is an error
that names the container, and so is a central that cannot tell which container it is. A central that
shares the machine's network (``network_mode: host``) is asked nothing and keeps 127.0.0.1, which is the
machine's there.

The network that is picked is the one a person made (the stack's own, or another it names) and, only when
there is no other, the default bridge, on which a central started with Docker's defaults already is.
"""

from __future__ import annotations

import json
import re
import socket
from typing import Any

from noust.core.exceptions import DockerError
from noust.core.runner import CommandRunner

__all__ = [
    "MODE_FORMAT",
    "NETWORKS_FORMAT",
    "PORTS_FORMAT",
    "container_port",
    "machine_name",
    "own_container_id",
    "service_address",
]

#: What a container's networks are printed as: ``{name: {"IPAddress": ...}}``.
NETWORKS_FORMAT = "{{json .NetworkSettings.Networks}}"

#: What a container's ports are printed as: ``{"80/tcp": [{"HostIp": ..., "HostPort": "8081"}]}``.
PORTS_FORMAT = "{{json .NetworkSettings.Ports}}"

#: What a container's network mode is printed as: ``bridge``, ``host``, ``container:<id>``...
MODE_FORMAT = "{{.HostConfig.NetworkMode}}"

#: Seconds a question to Docker gets.
_TIMEOUT = 30

#: Where Docker keeps the files of a container, which a container sees as its own ``/etc/hostname``.
_CONTAINER_FILES = re.compile(r"/containers/([0-9a-f]{64})/")

#: What Docker names a container by default: the first twelve characters of its identifier.
_SHORT_ID = re.compile(r"[0-9a-f]{12}")

#: The networks that are not one a person made.
_DEFAULTS = frozenset({"bridge", "host", "none"})

#: What Docker says when the container is on the network already.
_ALREADY = "already exists in network"


def own_container_id(*, mountinfo: str | None = None, hostname: str | None = None) -> str | None:
    """
    Say which container Noust is, as Docker knows it.

    Args:
        mountinfo: ``/proc/self/mountinfo``, read when not given: a container's ``/etc/hostname`` is a
            file under Docker's folder for that container, which names its full identifier.
        hostname: The host name, read when not given: Docker's default for one is the container's short
            identifier.

    Returns:
        The identifier (full, or short when the host name was all there was); None when this is not a
        container, or its host name was changed to something that is not an identifier.
    """
    if mountinfo is None:
        try:
            with open("/proc/self/mountinfo", encoding="utf-8") as handle:
                mountinfo = handle.read()
        except OSError:
            mountinfo = ""
    found = _CONTAINER_FILES.search(mountinfo)
    if found:
        return found.group(1)
    name = hostname if hostname is not None else socket.gethostname()
    return name if _SHORT_ID.fullmatch(name) else None


def _inspect(runner: CommandRunner, container: str, template: str) -> str:
    """
    Args:
        runner: Asks Docker.
        container: A container's identifier or name.
        template: The Go template to print.

    Returns:
        What it printed, trimmed.

    Raises:
        DockerError: Docker did not answer, with its own words.
    """
    answer = runner.run(
        ["docker", "inspect", "--type", "container", "--format", template, container],
        timeout=_TIMEOUT,
    )
    if not answer.success:
        raise DockerError(
            f"Docker could not inspect the container {container[:12]}",
            answer.stderr or answer.stdout,
        )
    return answer.stdout.strip()


def _networks(runner: CommandRunner, container: str) -> dict[str, str]:
    """
    Args:
        runner: Asks Docker.
        container: A container.

    Returns:
        The address it has on each network, by network name; a network it has none on is left out.

    Raises:
        DockerError: Docker did not answer.
    """
    try:
        listed: Any = json.loads(_inspect(runner, container, NETWORKS_FORMAT) or "{}")
    except ValueError:
        return {}
    if not isinstance(listed, dict):
        return {}
    return {
        str(name): str(entry["IPAddress"])
        for name, entry in listed.items()
        if isinstance(entry, dict) and entry.get("IPAddress")
    }


def service_address(runner: CommandRunner, container: str, *, me: str | None) -> str | None:
    """
    Find the address a deployed container answers on, from Noust's container, and make it reachable.

    Args:
        runner: Asks Docker and joins the network.
        container: The deployed container (a service's, from ``docker compose ps -q``).
        me: The container Noust is (:func:`own_container_id`).

    Returns:
        The container's address on the network Noust shares with it; None when Noust shares the
        machine's network and 127.0.0.1 is already right.

    Raises:
        DockerError: Noust does not know which container it is, the deployed container has no address on
            any network, or the network could not be joined, each with what Docker said.
    """
    if me is None:
        raise DockerError(
            "Noust cannot tell which container it is, so it cannot join the network of what it deployed",
            "Its host name was changed to something that is not Docker's identifier. Start the central's "
            "container without a hostname, so that Docker names it by its identifier.",
        )
    if _inspect(runner, me, MODE_FORMAT) == "host":
        return None
    theirs = _networks(runner, container)
    if not theirs:
        raise DockerError(
            f"The container {container[:12]} has no address on any Docker network",
            "It is stopped, or it runs on the host's network (network_mode: host), where there is "
            "nothing for Noust to join.",
        )
    chosen = next((name for name in theirs if name not in _DEFAULTS), None) or next(iter(theirs))
    if chosen not in _networks(runner, me):
        joined = runner.run(["docker", "network", "connect", chosen, me], timeout=_TIMEOUT)
        if not joined.success and _ALREADY not in (joined.stderr + joined.stdout):
            raise DockerError(
                f"Noust could not join the Docker network {chosen}",
                joined.stderr or joined.stdout,
            )
    return theirs[chosen]


def container_port(runner: CommandRunner, container: str, published: int | None) -> int:
    """
    Find the port a deployed container listens on, from the one it publishes on the machine.

    Args:
        runner: Asks Docker.
        container: The deployed container.
        published: The port on the machine that the application's record says it is published at.

    Returns:
        The port inside the container: the one whose published side is ``published``; failing that the
        only TCP port there is.

    Raises:
        DockerError: Docker did not answer, or there are several ports and none is the published
            one, which Noust will not pick between.
    """
    try:
        shown: Any = json.loads(_inspect(runner, container, PORTS_FORMAT) or "{}")
    except ValueError:
        shown = {}
    tcp: dict[int, list[int]] = {}
    for key, bindings in (shown if isinstance(shown, dict) else {}).items():
        number, _, protocol = str(key).partition("/")
        if protocol == "tcp" and number.isdigit():
            tcp[int(number)] = [
                int(binding["HostPort"])
                for binding in bindings or []
                if isinstance(binding, dict) and str(binding.get("HostPort", "")).isdigit()
            ]
    for listening, hosts in tcp.items():
        if published in hosts:
            return listening
    if len(tcp) == 1:
        return next(iter(tcp))
    raise DockerError(
        f"Noust cannot tell which port of the container {container[:12]} to ask",
        "TCP ports it listens on: "
        + (", ".join(map(str, sorted(tcp))) or "none")
        + ". Publish the "
        "one the application serves on at the port Noust records for it, or give the health check "
        "the port to ask.",
    )


def machine_name(runner: CommandRunner) -> str:
    """
    Ask the machine's Docker what the machine is called: the NAS's own name.

    Args:
        runner: Asks Docker.

    Returns:
        The name, or empty when Docker did not say.
    """
    answered = runner.run(["docker", "info", "--format", "{{.Name}}"], timeout=_TIMEOUT)
    return answered.stdout.strip() if answered.success else ""
