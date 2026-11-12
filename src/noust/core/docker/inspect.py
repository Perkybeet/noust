# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A container from the inside: how it ended, what it was told to do, what it touches.

``docker inspect`` of a container carries its whole configuration, its environment included, and the
environment is where the secrets of an application are. The question asked here names the fields
wanted (:data:`INSPECT_FORMAT`) and the environment is not one of them, so nothing that is read can
leak it, and the answer is one object a line like every other look the gate takes.

Docker prints times with nine digits of fraction (``2026-11-12T11:59:20.123456789Z``), which Python
does not read; they are read to the second, and the zero time Docker writes for a thing that never
happened (``0001-01-01T00:00:00Z``) is no time.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from noust.core.runner import CommandRunner

__all__ = [
    "INSPECT_FORMAT",
    "Health",
    "Inspected",
    "MountInfo",
    "RestartPolicy",
    "parse_inspect",
    "parse_time",
    "read_inspect",
]

#: Seconds an inspection gets.
INSPECT_TIMEOUT = 60

#: What a container id looks like: the digits Docker shows, or all of them.
_ID = re.compile(r"[0-9a-f]{12,64}")

_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})")

#: The fields asked of Docker, and nothing else: ``Config.Env`` is not among them.
INSPECT_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},'
    '"image_id":{{json .Image}},"state":{{json .State}},"restart_count":{{.RestartCount}},'
    '"host":{"RestartPolicy":{{json .HostConfig.RestartPolicy}},"Memory":{{json .HostConfig.Memory}},'
    '"NanoCpus":{{json .HostConfig.NanoCpus}},"Privileged":{{json .HostConfig.Privileged}}},'
    '"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}}}'
)

#: Most of a health check's last output that is kept: it is a line or two, not a log.
_HEALTH_OUTPUT = 2000


@dataclass(frozen=True)
class RestartPolicy:
    """
    What Docker does when a container ends.

    Attributes:
        name: ``no``, ``always``, ``unless-stopped`` or ``on-failure``.
        max_retries: How many times ``on-failure`` tries; 0 for no limit.
    """

    name: str = "no"
    max_retries: int = 0

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            ``name`` and ``max_retries``.
        """
        return {"name": self.name, "max_retries": self.max_retries}


@dataclass(frozen=True)
class Health:
    """
    A container's health check.

    Attributes:
        status: ``healthy``, ``unhealthy`` or ``starting``.
        failing_streak: How many probes in a row failed.
        last_output: What the last probe printed, as it printed it.
    """

    status: str
    failing_streak: int = 0
    last_output: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "status": self.status,
            "failing_streak": self.failing_streak,
            "last_output": self.last_output,
        }


@dataclass(frozen=True)
class MountInfo:
    """
    Something a container touches on the host.

    Attributes:
        kind: ``volume``, ``bind`` or ``tmpfs``.
        name: The volume's name, for a volume.
        source: Where it is on the host.
        destination: Where it is inside the container.
        read_only: The container may not write to it.
    """

    kind: str
    name: str = ""
    source: str = ""
    destination: str = ""
    read_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field.
        """
        return {
            "kind": self.kind,
            "name": self.name,
            "source": self.source,
            "destination": self.destination,
            "read_only": self.read_only,
        }


@dataclass(frozen=True)
class Inspected:
    """
    What ``inspect`` says of a container, without its environment.

    Attributes:
        id: The full id.
        name: Its name.
        image: The image it was made from, as it was asked for.
        image_id: The id of that image.
        state: Docker's word for its state.
        oom_killed: Memory ran out in its last run.
        exit_code: How its last run ended.
        error: Docker's own words when it could not start it.
        started_at: When it last started, to the second.
        finished_at: When it last ended, to the second.
        restart_count: How many times Docker restarted it in its life.
        health: Its health check, when it has one.
        restart_policy: What Docker does when it ends.
        memory_limit_bytes: Its memory limit; None for none.
        cpu_limit: Its processor limit in CPUs; None for none.
        privileged: It runs with every privilege of the host.
        mounts: What it touches on the host.
        networks: The networks it is on.
    """

    id: str
    name: str = ""
    image: str = ""
    image_id: str = ""
    state: str = ""
    oom_killed: bool = False
    exit_code: int = 0
    error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    restart_count: int = 0
    health: Health | None = None
    restart_policy: RestartPolicy = field(default_factory=RestartPolicy)
    memory_limit_bytes: int | None = None
    cpu_limit: float | None = None
    privileged: bool = False
    mounts: tuple[MountInfo, ...] = ()
    networks: tuple[str, ...] = ()

    @property
    def restarting(self) -> bool:
        """Whether Docker is restarting it now."""
        return self.state == "restarting"

    @property
    def running(self) -> bool:
        """Whether it runs now."""
        return self.state in {"running", "restarting", "paused"}


def parse_time(text: str | None) -> datetime | None:
    """
    Read a time the way Docker prints it.

    Args:
        text: ``2026-11-12T11:59:20.123456789Z`` and the like.

    Returns:
        The time to the second, in UTC; None when it is empty, is not a time, or is the zero time
        Docker writes for what never happened.
    """
    found = _TIME.fullmatch((text or "").strip())
    if found is None:
        return None
    year, month, day, hour, minute, second, zone = found.groups()
    if int(year) <= 1:
        return None
    try:
        moment = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    if zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        moment -= sign * timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6]))
    return moment


def _mapping(value: Any) -> Mapping[str, Any]:
    """
    Args:
        value: Anything.

    Returns:
        It when it is an object, else an empty one.
    """
    return value if isinstance(value, dict) else {}


def _int(value: Any, default: int = 0) -> int:
    """
    Args:
        value: Anything a number may be written as.
        default: What to answer when it is not one.

    Returns:
        The whole number.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _health(state: Mapping[str, Any]) -> Health | None:
    """
    Args:
        state: A container's ``State``.

    Returns:
        Its health check, or None when it has none.
    """
    raw = state.get("Health")
    if not isinstance(raw, dict) or not raw.get("Status"):
        return None
    last = ""
    log = raw.get("Log")
    if isinstance(log, list) and log and isinstance(log[-1], dict):
        last = str(log[-1].get("Output") or "").strip()[:_HEALTH_OUTPUT]
    return Health(str(raw["Status"]), _int(raw.get("FailingStreak")), last)


def _mounts(raw: Any) -> tuple[MountInfo, ...]:
    """
    Args:
        raw: A container's ``Mounts``.

    Returns:
        What it touches.
    """
    found = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("Type") or "")
        found.append(
            MountInfo(
                kind=kind,
                name=str(entry.get("Name") or "") if kind == "volume" else "",
                source=str(entry.get("Source") or ""),
                destination=str(entry.get("Destination") or ""),
                read_only=entry.get("RW") is False,
            )
        )
    return tuple(found)


def parse_inspect(text: str) -> dict[str, Inspected]:
    """
    Read what ``docker inspect --format`` wrote with :data:`INSPECT_FORMAT`.

    Args:
        text: The output, one object a line.

    Returns:
        The containers by full id. A line that is not an object, or has no id, is left out.
    """
    found: dict[str, Inspected] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not row.get("id"):
            continue
        state = _mapping(row.get("state"))
        host = _mapping(row.get("host"))
        policy = _mapping(host.get("RestartPolicy"))
        memory = _int(host.get("Memory"))
        nano = _int(host.get("NanoCpus"))
        ident = str(row["id"])
        found[ident] = Inspected(
            id=ident,
            name=str(row.get("name") or "").lstrip("/"),
            image=str(row.get("image") or ""),
            image_id=str(row.get("image_id") or ""),
            state=str(state.get("Status") or ""),
            oom_killed=state.get("OOMKilled") is True,
            exit_code=_int(state.get("ExitCode")),
            error=str(state.get("Error") or ""),
            started_at=parse_time(str(state.get("StartedAt") or "")),
            finished_at=parse_time(str(state.get("FinishedAt") or "")),
            restart_count=_int(row.get("restart_count")),
            health=_health(state),
            restart_policy=RestartPolicy(
                str(policy.get("Name") or "no") or "no", _int(policy.get("MaximumRetryCount"))
            ),
            memory_limit_bytes=memory if memory > 0 else None,
            cpu_limit=nano / 1_000_000_000 if nano > 0 else None,
            privileged=host.get("Privileged") is True,
            mounts=_mounts(row.get("mounts")),
            networks=tuple(sorted(_mapping(row.get("networks")))),
        )
    return found


def read_inspect(runner: CommandRunner, ids: Sequence[str]) -> dict[str, Inspected]:
    """
    Inspect containers in one process.

    Args:
        runner: Runs it.
        ids: Container ids as Docker listed them. Anything that is not an id is dropped: what a
            person typed never reaches a command.

    Returns:
        The containers by full id; empty when there is nobody to ask about or Docker said nothing
        (a container that went away between the listing and this is not an error). Docker prints
        the ones it found even when it complains of another, and those are kept.
    """
    wanted = [ident for ident in ids if _ID.fullmatch(ident)]
    if not wanted:
        return {}
    result = runner.run(
        ["docker", "inspect", "--type", "container", "--format", INSPECT_FORMAT, *wanted],
        timeout=INSPECT_TIMEOUT,
    )
    return parse_inspect(result.stdout)
