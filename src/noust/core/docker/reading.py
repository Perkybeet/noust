# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Asking Docker what it has, and reading the answer.

Every question is an argument list given to the runner with a deadline (rule 1), and the answers are
Docker's own JSON, one object a line: ``--format`` with a template that writes JSON, so that no
version of Docker has to print what an older one did not. Three rules keep the reading honest.

- **Labels are asked for by name.** The ``Labels`` column of Docker is one string, ``a=b,c=d``, in
  which the value of one label can contain the text of another (``x,com.docker.compose.project=shop``):
  a reader that splits it can be made to see a label nobody set. ``.Label "name"`` is Docker's own
  lookup, so each label this module reads is exactly the one that was set.
- **What cannot be read is skipped, not a crash.** A line that is not JSON, or an object with no id,
  is left out: one odd object of a thousand does not make the others invisible.
- **A size is Docker's, in powers of ten** (``29.98GB`` is 29 980 000 000 bytes).

Nothing here decides whose an object is: that is :mod:`noust.core.docker.owners`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from noust.core.docker.errors import DockerUnavailableError
from noust.core.docker.models import Container, Image, Network, Volume
from noust.core.runner import CommandResult, CommandRunner

__all__ = [
    "COMPOSE_PROJECT",
    "COMPOSE_SERVICE",
    "COMPOSE_WORKING_DIR",
    "DOCKER_TIMEOUT",
    "HELPER_LABEL",
    "INSTANCE_LABEL",
    "disk_usage",
    "list_containers",
    "list_images",
    "list_networks",
    "list_volumes",
    "parse_containers",
    "parse_disk_usage",
    "parse_images",
    "parse_networks",
    "parse_size",
    "parse_volume_sizes",
    "parse_volumes",
    "volume_sizes",
]

#: What Compose writes on everything it makes.
COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
COMPOSE_WORKING_DIR = "com.docker.compose.project.working_dir"
COMPOSE_CONFIG_FILES = "com.docker.compose.project.config_files"
COMPOSE_ONEOFF = "com.docker.compose.oneoff"
COMPOSE_VOLUME = "com.docker.compose.volume"

#: What Noust writes on what it makes: the instance a container belongs to, and what a container is
#: that Noust started for a copy. A claim made by a container about itself: it can only take access
#: away (see :mod:`noust.core.docker.owners`).
INSTANCE_LABEL = "io.noust.db-instance"
HELPER_LABEL = "io.noust.helper"

#: Seconds a listing gets. A machine with thousands of containers is slow, not stuck.
DOCKER_TIMEOUT = 60

#: Seconds the measuring of volumes gets: Docker walks every volume to do it.
SIZE_TIMEOUT = 300


def _label(name: str) -> str:
    """
    Args:
        name: A label's name.

    Returns:
        The template that writes that label's value as a JSON string, asked of Docker by name.
    """
    return '{{json (.Label "' + name + '")}}'


CONTAINER_FORMAT = (
    '{"id":{{json .ID}},"name":{{json .Names}},"image":{{json .Image}},"state":{{json .State}},'
    '"status":{{json .Status}},"created":{{json .CreatedAt}},"ports":{{json .Ports}},'
    '"mounts":{{json .Mounts}},"networks":{{json .Networks}},'
    f'"project":{_label(COMPOSE_PROJECT)},"service":{_label(COMPOSE_SERVICE)},'
    f'"working_dir":{_label(COMPOSE_WORKING_DIR)},"config_files":{_label(COMPOSE_CONFIG_FILES)},'
    f'"oneoff":{_label(COMPOSE_ONEOFF)},"instance":{_label(INSTANCE_LABEL)},'
    f'"helper":{_label(HELPER_LABEL)}}}'
)

IMAGE_FORMAT = "{{json .}}"

VOLUME_FORMAT = (
    '{"name":{{json .Name}},"driver":{{json .Driver}},"scope":{{json .Scope}},'
    '"mountpoint":{{json .Mountpoint}},'
    f'"project":{_label(COMPOSE_PROJECT)},"volume":{_label(COMPOSE_VOLUME)},'
    f'"instance":{_label(INSTANCE_LABEL)}}}'
)

NETWORK_FORMAT = (
    '{"id":{{json .ID}},"name":{{json .Name}},"driver":{{json .Driver}},"scope":{{json .Scope}},'
    '"internal":{{json .Internal}},"created":{{json .CreatedAt}},'
    f'"project":{_label(COMPOSE_PROJECT)}}}'
)

_SIZE = re.compile(r"\s*([\d.]+)\s*([kKMGTP]?B)\b")
_UNITS = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4, "PB": 1000**5}
_HEALTH = re.compile(r"\((?:health:\s*)?(healthy|unhealthy|starting)\)")


def parse_size(text: str) -> int | None:
    """
    Read a size the way ``docker`` prints it.

    Args:
        text: ``29.98GB (63%)``, ``310.2MB``, ``0B`` or ``N/A``. Docker counts in powers of ten.

    Returns:
        The size in bytes, or None when it is not a size.
    """
    match = _SIZE.match(str(text))
    if not match:
        return None
    return round(float(match.group(1)) * _UNITS[match.group(2).upper()])


def _objects(text: str) -> Iterable[Mapping[str, Any]]:
    """
    Read what Docker printed: an object a line.

    Args:
        text: The output.

    Yields:
        Each line that is a JSON object; the rest is left out.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            yield row


def _text(row: Mapping[str, Any], key: str) -> str:
    """
    Args:
        row: One object.
        key: A field of it.

    Returns:
        The field as text; empty when it is missing or empty.
    """
    value = row.get(key)
    return "" if value is None else str(value)


def _list(value: str) -> tuple[str, ...]:
    """
    Args:
        value: A comma-separated column of Docker's.

    Returns:
        Its entries, without the empty ones.
    """
    return tuple(part.strip() for part in value.split(",") if part.strip())


def parse_containers(text: str) -> list[Container]:
    """
    Read the output of ``docker ps --all`` written with :data:`CONTAINER_FORMAT`.

    Args:
        text: The output.

    Returns:
        The containers, with nobody as their owner: that is decided afterwards.
    """
    found = []
    for row in _objects(text):
        container_id = _text(row, "id")
        if not container_id:
            continue
        status = _text(row, "status")
        health = _HEALTH.search(status)
        found.append(
            Container(
                id=container_id,
                name=_text(row, "name").split(",", 1)[0].lstrip("/"),
                image=_text(row, "image"),
                state=_text(row, "state").lower(),
                status=status,
                health=health.group(1) if health else "",
                created=_text(row, "created"),
                ports=_text(row, "ports"),
                mounts=_list(_text(row, "mounts")),
                networks=_list(_text(row, "networks")),
                project=_text(row, "project"),
                service=_text(row, "service"),
                working_dir=_text(row, "working_dir"),
                config_files=_list(_text(row, "config_files")),
                oneoff=_text(row, "oneoff").lower() == "true",
                instance=_text(row, "instance"),
                helper=_text(row, "helper"),
            )
        )
    return found


def parse_images(text: str) -> list[Image]:
    """
    Read the output of ``docker image ls`` written with :data:`IMAGE_FORMAT`.

    Docker lists an image once for each name it has (and a store by digest lists it so too): the
    rows of one id are one image with every name.

    Args:
        text: The output.

    Returns:
        The images, in the order Docker listed them.
    """
    order: list[str] = []
    tags: dict[str, list[str]] = {}
    facts: dict[str, tuple[str, int | None]] = {}
    for row in _objects(text):
        image_id = _text(row, "ID")
        if not image_id:
            continue
        if image_id not in tags:
            order.append(image_id)
            tags[image_id] = []
            facts[image_id] = (_text(row, "CreatedAt"), parse_size(_text(row, "Size")))
        repository, tag = _text(row, "Repository"), _text(row, "Tag")
        if repository and repository != "<none>" and tag and tag != "<none>":
            tags[image_id].append(f"{repository}:{tag}")
    return [
        Image(id=image_id, tags=tuple(tags[image_id]), created=created, size_bytes=size)
        for image_id in order
        for created, size in [facts[image_id]]
    ]


def parse_volumes(text: str) -> list[Volume]:
    """
    Read the output of ``docker volume ls`` written with :data:`VOLUME_FORMAT`.

    Args:
        text: The output.

    Returns:
        The volumes, with nobody as their owner.
    """
    return [
        Volume(
            name=_text(row, "name"),
            driver=_text(row, "driver") or "local",
            scope=_text(row, "scope") or "local",
            mountpoint=_text(row, "mountpoint"),
            project=_text(row, "project"),
            key=_text(row, "volume"),
            instance=_text(row, "instance"),
        )
        for row in _objects(text)
        if _text(row, "name")
    ]


def parse_networks(text: str) -> list[Network]:
    """
    Read the output of ``docker network ls`` written with :data:`NETWORK_FORMAT`.

    Args:
        text: The output.

    Returns:
        The networks, with nobody as their owner.
    """
    return [
        Network(
            id=_text(row, "id"),
            name=_text(row, "name"),
            driver=_text(row, "driver"),
            scope=_text(row, "scope"),
            internal=_text(row, "internal").lower() == "true",
            project=_text(row, "project"),
            created=_text(row, "created"),
        )
        for row in _objects(text)
        if _text(row, "id") and _text(row, "name")
    ]


def parse_volume_sizes(text: str) -> dict[str, int]:
    """
    Read the volumes' sizes out of ``docker system df -v``.

    Docker has printed this as one object with a ``Volumes`` list and, in other versions, as one
    object a line; both are read. What is not a size is left out: not knowing is not an error.

    Args:
        text: The output.

    Returns:
        The size in bytes of each volume that had one.
    """
    entries: list[Mapping[str, Any]] = []
    try:
        whole = json.loads(text)
    except ValueError:
        whole = None
    if isinstance(whole, dict) and isinstance(whole.get("Volumes"), list):
        entries = [entry for entry in whole["Volumes"] if isinstance(entry, dict)]
    elif isinstance(whole, list):
        entries = [entry for entry in whole if isinstance(entry, dict)]
    else:
        entries = list(_objects(text))
    sizes: dict[str, int] = {}
    for entry in entries:
        name = _text(entry, "Name")
        size = parse_size(_text(entry, "Size"))
        if name and size is not None:
            sizes[name] = size
    return sizes


def _ask(runner: CommandRunner, argv: list[str], *, timeout: int = DOCKER_TIMEOUT) -> CommandResult:
    """
    Run one question through the runner and say why when Docker cannot answer.

    Args:
        runner: Runs it.
        argv: The command.
        timeout: Seconds Docker has.

    Returns:
        The successful result.

    Raises:
        DockerUnavailableError: Docker is not installed, or its daemon did not answer; Docker's own
            words are in ``output``.
    """
    if not runner.exists("docker"):
        raise DockerUnavailableError(
            "Docker is not installed on this server",
            details="Install it from Server > Software, or with: noust setup init",
        )
    result = runner.run(argv, timeout=timeout)
    if not result.success:
        raise DockerUnavailableError(
            "Docker did not answer",
            details="Check that the daemon runs: systemctl status docker",
            output=(result.stderr or result.stdout).strip() or f"exit code {result.exit_code}",
        )
    return result


def list_containers(runner: CommandRunner, *, timeout: int = DOCKER_TIMEOUT) -> list[Container]:
    """
    List every container on this machine, running or not, in one call.

    Args:
        runner: Runs it.
        timeout: Seconds Docker has.

    Returns:
        The containers, owned by nobody yet.

    Raises:
        DockerUnavailableError: Docker cannot be asked.
    """
    result = _ask(
        runner,
        ["docker", "ps", "--all", "--no-trunc", "--format", CONTAINER_FORMAT],
        timeout=timeout,
    )
    return parse_containers(result.stdout)


def list_images(runner: CommandRunner, *, timeout: int = DOCKER_TIMEOUT) -> list[Image]:
    """
    List every image on this machine in one call.

    Args:
        runner: Runs it.
        timeout: Seconds Docker has.

    Returns:
        The images, owned by nobody yet.

    Raises:
        DockerUnavailableError: Docker cannot be asked.
    """
    result = _ask(
        runner,
        ["docker", "image", "ls", "--no-trunc", "--format", IMAGE_FORMAT],
        timeout=timeout,
    )
    return parse_images(result.stdout)


def list_volumes(runner: CommandRunner, *, timeout: int = DOCKER_TIMEOUT) -> list[Volume]:
    """
    List every named volume on this machine in one call, without measuring any.

    Args:
        runner: Runs it.
        timeout: Seconds Docker has.

    Returns:
        The volumes, owned by nobody yet.

    Raises:
        DockerUnavailableError: Docker cannot be asked.
    """
    result = _ask(runner, ["docker", "volume", "ls", "--format", VOLUME_FORMAT], timeout=timeout)
    return parse_volumes(result.stdout)


def list_networks(runner: CommandRunner, *, timeout: int = DOCKER_TIMEOUT) -> list[Network]:
    """
    List every network on this machine in one call.

    Args:
        runner: Runs it.
        timeout: Seconds Docker has.

    Returns:
        The networks, owned by nobody yet.

    Raises:
        DockerUnavailableError: Docker cannot be asked.
    """
    result = _ask(
        runner,
        ["docker", "network", "ls", "--no-trunc", "--format", NETWORK_FORMAT],
        timeout=timeout,
    )
    return parse_networks(result.stdout)


def volume_sizes(runner: CommandRunner, *, timeout: int = SIZE_TIMEOUT) -> dict[str, int]:
    """
    Measure the volumes: what a listing does not do, because Docker walks every one of them.

    Args:
        runner: Runs it.
        timeout: Seconds Docker has.

    Returns:
        The size in bytes of each volume Docker measured; empty when this Docker does not say.

    Raises:
        DockerUnavailableError: Docker is not installed.
    """
    if not runner.exists("docker"):
        raise DockerUnavailableError("Docker is not installed on this server")
    result = runner.run(["docker", "system", "df", "-v", "--format", "{{json .}}"], timeout=timeout)
    return parse_volume_sizes(result.stdout) if result.success else {}


def parse_disk_usage(text: str) -> dict[str, dict[str, Any]]:
    """
    Read ``docker system df`` written with ``--format '{{json .}}'``, one object a line.

    Args:
        text: The output.

    Returns:
        For each ``Type`` (``Images``, ``Containers``, ``Local Volumes``, ``Build Cache``): ``size``
        and ``reclaimable`` in bytes and ``count``.
    """
    found: dict[str, dict[str, Any]] = {}
    for row in _objects(text):
        if "Type" not in row:
            continue
        count = _text(row, "TotalCount")
        found[str(row["Type"])] = {
            "size": parse_size(_text(row, "Size")),
            "reclaimable": parse_size(_text(row, "Reclaimable")),
            "count": int(count) if count.isdigit() else None,
        }
    return found


def disk_usage(runner: CommandRunner) -> dict[str, dict[str, Any]] | None:
    """
    Ask Docker what it holds, by kind, without walking any volume.

    Args:
        runner: Runs it.

    Returns:
        The table of :func:`parse_disk_usage`, or None when Docker is not here or its daemon does
        not answer.
    """
    if not runner.exists("docker"):
        return None
    result = runner.run(
        ["docker", "system", "df", "--format", "{{json .}}"], timeout=DOCKER_TIMEOUT
    )
    return parse_disk_usage(result.stdout) if result.success else None
