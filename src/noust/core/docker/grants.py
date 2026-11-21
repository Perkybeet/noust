# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the server has given a container: the part of ``docker inspect`` that is not the container's own.

A container does not change after it is made. What it was given of the machine (a directory of the
server mounted into it, a device, a capability, a namespace of the host, its confinement switched off,
another cgroup or runtime, another container's volumes, every privilege at once) is what ``docker
create`` was told, and the only way to give it something else is to make another container, which for a
stack is a deployment and goes through the guard of the compose file. So it is the one record of what a
stack runs with that neither its repository nor its ``.env`` can change from under the guard, and it is
what the guard asks when it has to know what a stack already had
(:mod:`noust.deployers.helpers.compose_running`).

The question names the fields wanted (:data:`GRANTS_FORMAT`) and the environment is not one of them, so
nothing that is read can leak the secrets of an application; the labels are asked by name, as everywhere
else in this package (:mod:`noust.core.docker.reading`).

The template is written from Docker's documentation of the object ``inspect`` prints and proved against
the output of a real engine (``tests/support/docker_grants_samples.py`` holds what one printed); the
harness of real engines is what proves it on the three ages of Docker Noust meets.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from noust.core.docker import reading
from noust.core.docker.errors import DockerUnavailableError
from noust.core.docker.inspect import MAX_IDS
from noust.core.runner import CommandRunner
from noust.core.server_text import say

__all__ = [
    "GRANTS_FORMAT",
    "Bind",
    "Grants",
    "parse_grants",
    "read_grants",
    "read_project_grants",
]

#: Seconds the inspection of a project's containers gets.
GRANTS_TIMEOUT = 60

#: What a container id looks like: the digits Docker shows, or all of them.
_ID = re.compile(r"[0-9a-f]{12,64}")

#: What a Compose project may be called. Anything else cannot be the label of a container Compose made,
#: and it is never put in a command.
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

#: What Docker says when the object asked about is not there (it went away between a listing and this).
_GONE = re.compile(r"no such (?:object|container)", re.IGNORECASE)

#: The fields asked of Docker, and nothing else: ``Config.Env`` is not among them. The labels are
#: asked by name: ``index`` of a map answers the empty string for one that was never set.
GRANTS_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},'
    f'"service":{{{{json (index .Config.Labels "{reading.COMPOSE_SERVICE}")}}}},'
    f'"oneoff":{{{{json (index .Config.Labels "{reading.COMPOSE_ONEOFF}")}}}},'
    '"state":{{json .State.Status}},"mounts":{{json .Mounts}},'
    '"host":{"Privileged":{{json .HostConfig.Privileged}},"CapAdd":{{json .HostConfig.CapAdd}},'
    '"Devices":{{json .HostConfig.Devices}},'
    '"DeviceCgroupRules":{{json .HostConfig.DeviceCgroupRules}},'
    '"PidMode":{{json .HostConfig.PidMode}},"IpcMode":{{json .HostConfig.IpcMode}},'
    '"UTSMode":{{json .HostConfig.UTSMode}},"UsernsMode":{{json .HostConfig.UsernsMode}},'
    '"CgroupnsMode":{{json .HostConfig.CgroupnsMode}},"NetworkMode":{{json .HostConfig.NetworkMode}},'
    '"SecurityOpt":{{json .HostConfig.SecurityOpt}},"CgroupParent":{{json .HostConfig.CgroupParent}},'
    '"Runtime":{{json .HostConfig.Runtime}},"VolumesFrom":{{json .HostConfig.VolumesFrom}}}}'
)


@dataclass(frozen=True)
class Bind:
    """
    A directory or file of the server mounted into a container.

    Attributes:
        source: Where it is on the server, as Docker recorded it.
        destination: Where it is inside the container.
        read_only: The container may not write to it.
    """

    source: str
    destination: str = ""
    read_only: bool = False


@dataclass(frozen=True)
class Grants:
    """
    What a container was given of the server it runs on.

    Attributes:
        id: The full id.
        name: Its name.
        service: The service of the compose file it is the container of; empty for one Compose did
            not make.
        oneoff: Compose made it for ``run`` (a hook), not ``up``.
        state: Docker's word for its state.
        privileged: It runs with every privilege and every device of the host.
        cap_add: The capabilities added to its default set, as Docker records them.
        devices: The devices of the host it is given, by their path on the host.
        device_rules: The cgroup rules that let it use devices (``c 13:* rwm``).
        pid: Its PID namespace: ``host``, ``container:<x>``, or empty for its own.
        ipc: Its IPC namespace, in the same words (Docker's default names, ``private``, ``shareable``,
            are what it says when it shares nothing with the host).
        uts: Its UTS namespace.
        userns: Its user namespace.
        cgroupns: Its cgroup namespace.
        network: Its network mode: ``host``, ``container:<x>``, ``none``, or the name of a network.
        security_opt: Its confinement options (``seccomp=unconfined``).
        cgroup_parent: The cgroup of the server it runs in; empty for Docker's own.
        runtime: The runtime that runs it.
        volumes_from: The containers whose volumes it was given, by name or id, without the mode.
        binds: The directories and files of the server mounted into it.
    """

    id: str
    name: str = ""
    service: str = ""
    oneoff: bool = False
    state: str = ""
    privileged: bool = False
    cap_add: tuple[str, ...] = ()
    devices: tuple[str, ...] = ()
    device_rules: tuple[str, ...] = ()
    pid: str = ""
    ipc: str = ""
    uts: str = ""
    userns: str = ""
    cgroupns: str = ""
    network: str = ""
    security_opt: tuple[str, ...] = ()
    cgroup_parent: str = ""
    runtime: str = ""
    volumes_from: tuple[str, ...] = ()
    binds: tuple[Bind, ...] = ()


def _mapping(value: Any) -> Mapping[str, Any]:
    """
    Args:
        value: Anything.

    Returns:
        It when it is an object, else an empty one.
    """
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    """
    Args:
        value: What a field held.

    Returns:
        It as text; empty for nothing.
    """
    return value.strip() if isinstance(value, str) else ""


def _texts(value: Any) -> tuple[str, ...]:
    """
    Args:
        value: What a list field held: a list of text, or ``null`` for none.

    Returns:
        The entries that are text, in order.
    """
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _devices(value: Any) -> tuple[str, ...]:
    """
    Args:
        value: ``HostConfig.Devices``: objects with the path on the host and in the container.

    Returns:
        The path on the host of each.
    """
    if not isinstance(value, list):
        return ()
    return tuple(
        path
        for entry in value
        if isinstance(entry, dict) and (path := _text(entry.get("PathOnHost")))
    )


def _binds(value: Any) -> tuple[Bind, ...]:
    """
    Args:
        value: ``Mounts``: what a container touches.

    Returns:
        The directories and files of the server among them (type ``bind``): a volume Docker keeps is
        not the server's, and a ``tmpfs`` is the container's own.
    """
    if not isinstance(value, list):
        return ()
    return tuple(
        Bind(
            source=_text(entry.get("Source")),
            destination=_text(entry.get("Destination")),
            read_only=entry.get("RW") is False,
        )
        for entry in value
        if isinstance(entry, dict) and entry.get("Type") == "bind" and _text(entry.get("Source"))
    )


def _volumes_from(value: Any) -> tuple[str, ...]:
    """
    Args:
        value: ``HostConfig.VolumesFrom``: ``name`` or ``name:mode``.

    Returns:
        The containers named, without the mode.
    """
    return tuple(entry.partition(":")[0] for entry in _texts(value))


def parse_grants(text: str) -> dict[str, Grants]:
    """
    Read what ``docker inspect --format`` wrote with :data:`GRANTS_FORMAT`.

    Args:
        text: The output, one object a line.

    Returns:
        The containers by full id. A line that is not an object, or has no id, is left out.
    """
    found: dict[str, Grants] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not row.get("id"):
            continue
        host = _mapping(row.get("host"))
        ident = str(row["id"])
        found[ident] = Grants(
            id=ident,
            name=_text(row.get("name")).lstrip("/"),
            service=_text(row.get("service")),
            oneoff=_text(row.get("oneoff")).lower() == "true",
            state=_text(row.get("state")),
            privileged=host.get("Privileged") is True,
            cap_add=_texts(host.get("CapAdd")),
            devices=_devices(host.get("Devices")),
            device_rules=_texts(host.get("DeviceCgroupRules")),
            pid=_text(host.get("PidMode")),
            ipc=_text(host.get("IpcMode")),
            uts=_text(host.get("UTSMode")),
            userns=_text(host.get("UsernsMode")),
            cgroupns=_text(host.get("CgroupnsMode")),
            network=_text(host.get("NetworkMode")),
            security_opt=_texts(host.get("SecurityOpt")),
            cgroup_parent=_text(host.get("CgroupParent")),
            runtime=_text(host.get("Runtime")),
            volumes_from=_volumes_from(host.get("VolumesFrom")),
            binds=_binds(row.get("mounts")),
        )
    return found


def read_grants(runner: CommandRunner, ids: Sequence[str]) -> dict[str, Grants]:
    """
    Inspect containers for what they were given, in as few processes as the system takes.

    Args:
        runner: Runs it.
        ids: Container ids as Docker listed them. Anything that is not an id is dropped: what a
            person typed never reaches a command.

    Returns:
        The containers by full id; one that went away between the listing and this is not in it.

    Raises:
        DockerUnavailableError: Docker answered with an error and with nothing that could be read, and
            the error was not a container that is no longer there. Docker's own words are in
            ``output``.
    """
    wanted = [ident for ident in ids if _ID.fullmatch(ident)]
    found: dict[str, Grants] = {}
    for start in range(0, len(wanted), MAX_IDS):
        result = runner.run(
            [
                "docker",
                "inspect",
                "--type",
                "container",
                "--format",
                GRANTS_FORMAT,
                *wanted[start : start + MAX_IDS],
            ],
            timeout=GRANTS_TIMEOUT,
        )
        read = parse_grants(result.stdout)
        found.update(read)
        said = (result.stderr or "").strip()
        if not result.success and not read and not _GONE.search(said):
            raise DockerUnavailableError.from_text(
                say("docker.grants_unreadable"), output=said or f"exit code {result.exit_code}"
            )
    return found


def _runs_within(directory: str, tree: Path) -> bool:
    """
    Tell whether a container was made from inside an application's tree.

    Args:
        directory: Its ``com.docker.compose.project.working_dir`` label: where Compose ran.
        tree: The application's directory.

    Returns:
        True when it is the tree or below it, as written or after the links of either are followed
        (the label is what Compose saw, and an application's directory can be a link). A container that
        does not say where it ran is not proven to be another stack's, so it counts.
    """
    if not directory:
        return True
    place = PurePosixPath(posixpath.normpath(directory))
    roots = {PurePosixPath(posixpath.normpath(str(tree))), PurePosixPath(os.path.realpath(tree))}
    forms = {place, PurePosixPath(os.path.realpath(directory))}
    return any(form == root or root in form.parents for form in forms for root in roots)


def read_project_grants(
    runner: CommandRunner, project: str, *, within: Path | None = None
) -> tuple[Grants, ...]:
    """
    Read what every container of a Compose project was given, the stopped ones included.

    The project is the label Compose writes on what it makes, asked of Docker by name. A container made
    for ``run`` (a hook that exits and is removed) is not what the stack runs and is left out. Two
    applications can end up with one project name, and then the label alone says nothing of whose a
    container is: ``within`` keeps the ones whose working directory is the application's tree.

    Args:
        runner: Runs the questions.
        project: The Compose project the application runs under.
        within: The application's directory: only the containers that were made from inside it are
            read. None reads every container of the project.

    Returns:
        What each container was given, by name; none when the project has no container yet (a first
        deployment) or the name cannot be a project's.

    Raises:
        DockerUnavailableError: Docker is not installed, does not answer, or answered a listing and then
            could not be read.
    """
    if _PROJECT.fullmatch(project) is None:
        return ()
    listed = reading.list_containers(runner, project=project)
    ids = [
        container.id
        for container in listed
        if container.project == project
        and not container.oneoff
        and (within is None or _runs_within(container.working_dir, within))
    ]
    return tuple(sorted(read_grants(runner, ids).values(), key=lambda grants: grants.name))
