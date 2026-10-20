# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The containers of the Compose stacks, watched for restart loops, and Docker's events kept (G-09).

A stack's unit runs ``docker compose up -d`` and finishes, so systemd cannot say that a container
of it restarts every few seconds, and a stack with a web that still answers is judged healthy by
its probe while one of its workers dies in a loop. Two things were missing:

- **The loop.** Every scan this reads the restart counter Docker keeps for each container of a
  stack (``RestartCount``). A container that Docker restarted ``monitor.container_restart_limit``
  times (5; 0 turns the check off) since it was first seen is announced, with the exit code of
  its last run and its last lines; the announcement is closed when it has run five minutes
  without restarting. The count is a baseline, not an absolute: a container that restarted
  forty times last month is not a loop today.
- **The why.** The unit is long finished by the time a container is killed for lack of memory.
  Docker keeps the events (``die``, ``oom``, ``restart``, ``health_status``), and this asks for
  those since the last look and keeps them beside the metrics, where the timeline of an interval
  reads them back.

It asks Docker the way the rest of Noust does: the one container lister of
:mod:`noust.deployers.compose_adopt`, and the identity of a stack
(:func:`~noust.deployers.compose_reclaim.stack_identity`) to say whose a container is.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from noust.core.exceptions import NoustError
from noust.core.notifications.composers_netobs import compose_container_loop
from noust.core.runner import CommandRunner
from noust.core.store import App, get_store, is_compose_backed
from noust.deployers.compose_adopt import StackContainer, list_stack_containers
from noust.deployers.compose_reclaim import stack_identity
from noust.monitor.watches import Watch, WatchServices

log = logging.getLogger(__name__)

#: The scope of what this watch remembers: one entry per container id, and the events cursor.
SCOPE = "containers"
CURSOR_ITEM = "events_since"

DEFAULT_CHECK_SECONDS = 60
DEFAULT_RESTART_LIMIT = 5

#: A container that has run this long without restarting has stopped looping.
STABLE_SECONDS = 300

#: Most events one look keeps, and lines of a container's log a notification carries.
MAX_EVENTS = 100
LOG_LINES = 20

#: What the events the timeline explains are called in the metrics store.
EVENT_KIND = "docker_event"

#: The Docker events asked for. ``kill`` always comes with a ``die``, so it adds nothing.
_EVENT_FILTERS = ("die", "oom", "restart", "health_status")

_DOCKER_TIMEOUT = 30
_INSPECT_FORMAT = (
    "{{.Id}}|{{.Name}}|{{.RestartCount}}|{{.State.Status}}|{{.State.ExitCode}}|{{.State.OOMKilled}}"
)


@dataclass(frozen=True)
class Reading:
    """
    One container of a stack, as Docker reports it now.

    Attributes:
        id: Full container id.
        name: Container name, without the slash Docker prints.
        domain: The application whose stack it belongs to.
        status: ``running``, ``restarting``, ``exited``...
        restarts: Docker's restart counter.
        exit_code: The exit code of its last run.
        oom: Whether its last run was killed for lack of memory.
    """

    id: str
    name: str
    domain: str
    status: str
    restarts: int
    exit_code: int
    oom: bool


def parse_inspect(stdout: str, owners: dict[str, str]) -> list[Reading]:
    """
    Read what ``docker inspect --format`` printed.

    Args:
        stdout: One ``id|name|restarts|status|exit|oom`` line per container.
        owners: Container id to the application it belongs to.

    Returns:
        A reading per line that is well formed and belongs to an application.
    """
    found: list[Reading] = []
    for line in stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 6:
            continue
        identity, name, count, status, code, oom = parts
        domain = owners.get(identity)
        if domain is None:
            continue
        try:
            found.append(
                Reading(
                    id=identity,
                    name=name.lstrip("/"),
                    domain=domain,
                    status=status,
                    restarts=int(count),
                    exit_code=int(code),
                    oom=oom.strip().lower() == "true",
                )
            )
        except ValueError:
            continue
    return found


def describe_event(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """
    Turn one line of ``docker events --format '{{json .}}'`` into what the timeline says.

    Args:
        event: The decoded line.

    Returns:
        ``(container name, action, sentence)``, or None when it is not worth keeping (a
        healthy check, a container that is not a Compose one).
    """
    actor = event.get("Actor") or {}
    attributes = actor.get("Attributes") or {}
    name = str(attributes.get("name") or "")
    action = str(event.get("Action") or event.get("status") or "")
    if not name or not action:
        return None
    if action.startswith("health_status"):
        state = action.partition(":")[2].strip()
        if state != "unhealthy":
            return None
        return name, "health_status", f"{name} became unhealthy."
    if action == "die":
        code = str(attributes.get("exitCode") or "?")
        return name, "die", f"{name} exited with code {code}."
    if action == "oom":
        return name, "oom", f"{name} was killed for lack of memory."
    if action == "restart":
        return name, "restart", f"{name} was restarted by Docker."
    return None


class ContainerWatch(Watch):
    """Announces a container in a restart loop and keeps Docker's events of the stacks."""

    name = "containers"
    default_interval_s = float(DEFAULT_CHECK_SECONDS)

    def __init__(
        self,
        apps: Callable[[], list[App]] | None = None,
        metrics: Callable[[], Any] | None = None,
    ) -> None:
        """
        Args:
            apps: Lists the applications whose stacks are watched; the store's Compose-backed
                ones by default.
            metrics: Opens the metrics store the events are kept in; the default one by
                default. Tests inject one.
        """
        self._apps = apps or _compose_apps
        self._metrics = metrics

    def interval_s(self, services: WatchServices) -> float:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            ``monitor.container_check_seconds``; zero (off) when it says so.
        """
        seconds = services.config.get("monitor.container_check_seconds", DEFAULT_CHECK_SECONDS)
        return float(seconds or 0)

    def check(self, services: WatchServices) -> None:
        """
        Read the stacks' containers, announce loops, keep Docker's events.

        Args:
            services: What the monitor lends.
        """
        runner = services.runner
        if not runner.exists("docker"):
            return
        apps = self._apps()
        if not apps:
            return
        try:
            listed = list_stack_containers(runner=runner)
        except NoustError as exc:
            log.debug("The containers were not listed: %s", exc)
            return
        owners = _owners(apps, listed)
        if not owners:
            services.state.keep_only(SCOPE, {CURSOR_ITEM})
            return
        self._loops(services, owners)
        self._events(services, listed, owners)

    # -- Restart loops -----------------------------------------------------------------------

    def _loops(self, services: WatchServices, owners: dict[str, str]) -> None:
        """
        Compare each container's restart counter with what was seen before.

        Args:
            services: What the monitor lends.
            owners: Container id to the application it belongs to.
        """
        limit = int(
            services.config.get("monitor.container_restart_limit", DEFAULT_RESTART_LIMIT) or 0
        )
        if limit <= 0:
            return
        result = services.runner.run(
            ["docker", "inspect", "--format", _INSPECT_FORMAT, *owners],
            timeout=_DOCKER_TIMEOUT,
        )
        if not result.success:
            # Not knowing is not a change: what was announced stays announced.
            return
        now = services.clock()
        seen: set[str] = {CURSOR_ITEM}
        for reading in parse_inspect(result.stdout, owners):
            seen.add(reading.id)
            self._judge(reading, limit, now, services)
        services.state.keep_only(SCOPE, seen)

    def _judge(self, reading: Reading, limit: int, now: float, services: WatchServices) -> None:
        """
        Decide whether one container is in a loop, and say so once.

        Args:
            reading: The container now.
            limit: ``monitor.container_restart_limit``.
            now: The moment, epoch seconds.
            services: What the monitor lends.
        """
        memory = services.state.get_json(SCOPE, reading.id)
        if not isinstance(memory, dict):
            memory = {
                "baseline": reading.restarts,
                "last": reading.restarts,
                "announced": False,
                "stable_since": now,
            }
        if reading.restarts < int(memory["last"]):
            # The container was recreated: its counter started again.
            memory.update(baseline=reading.restarts, last=reading.restarts, announced=False)
        grew = reading.restarts - int(memory["last"])
        if grew > 0:
            memory["stable_since"] = now
        memory["last"] = reading.restarts
        since_seen = reading.restarts - int(memory["baseline"])
        if since_seen >= limit and not memory["announced"]:
            memory["announced"] = True
            services.publish(
                partial(
                    compose_container_loop,
                    "restart_loop",
                    reading.name,
                    domain=reading.domain,
                    restarts=since_seen,
                    limit=limit,
                    exit_code=reading.exit_code,
                    output=self._last_lines(reading, services.runner),
                )
            )
        elif (
            memory["announced"]
            and reading.status == "running"
            and now - float(memory["stable_since"]) >= STABLE_SECONDS
        ):
            memory.update(announced=False, baseline=reading.restarts)
            services.publish(
                partial(
                    compose_container_loop,
                    "recovered",
                    reading.name,
                    domain=reading.domain,
                    stable_minutes=int((now - float(memory["stable_since"])) // 60),
                )
            )
        services.state.put_json(SCOPE, reading.id, memory)

    @staticmethod
    def _last_lines(reading: Reading, runner: CommandRunner) -> str:
        """
        Args:
            reading: The container.
            runner: The one way a process is run.

        Returns:
            Its last log lines; empty when Docker cannot say.
        """
        result = runner.run(
            ["docker", "logs", "--tail", str(LOG_LINES), reading.id], timeout=_DOCKER_TIMEOUT
        )
        # A container writes to both streams, and Docker keeps them apart.
        return "\n".join(part for part in (result.stdout, result.stderr) if part.strip())

    # -- Docker's events -----------------------------------------------------------------------

    def _events(
        self, services: WatchServices, listed: list[StackContainer], owners: dict[str, str]
    ) -> None:
        """
        Keep what Docker did to the stacks' containers since the last look.

        Args:
            services: What the monitor lends.
            listed: Every Compose container on the host.
            owners: Container id to the application it belongs to.
        """
        now = services.clock()
        raw = services.state.get(SCOPE, CURSOR_ITEM)
        try:
            since = int(float(raw)) if raw is not None else int(now) - DEFAULT_CHECK_SECONDS
        except ValueError:
            since = int(now) - DEFAULT_CHECK_SECONDS
        until = int(now)
        if until <= since:
            return
        argv = ["docker", "events", "--since", str(since), "--until", str(until)]
        argv += ["--filter", "type=container"]
        for action in _EVENT_FILTERS:
            argv += ["--filter", f"event={action}"]
        argv += ["--format", "{{json .}}"]
        result = services.runner.run(argv, timeout=_DOCKER_TIMEOUT)
        if not result.success:
            return
        services.state.put(SCOPE, CURSOR_ITEM, str(until))
        by_name = {c.name: owners[c.id] for c in listed if c.id in owners}
        kept = 0
        store = None
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            described = describe_event(event) if isinstance(event, dict) else None
            if described is None:
                continue
            name, action, sentence = described
            domain = by_name.get(name)
            if domain is None:
                continue
            if store is None:
                store = self._open_metrics()
                if store is None:
                    return
            stamp = int(event.get("time") or until)
            try:
                store.record_monitor_event(
                    EVENT_KIND, name, detail=sentence, reason=action, ts=stamp, app=domain
                )
            except (sqlite3.Error, OSError) as exc:
                log.warning("Could not keep a Docker event of %s: %s", name, exc)
                return
            kept += 1
            if kept >= MAX_EVENTS:
                return

    def _open_metrics(self) -> Any | None:
        """
        Returns:
            The metrics store the events are kept in, or None when it cannot be opened.
        """
        try:
            if self._metrics is not None:
                return self._metrics()
            from noust.monitor.collector import open_store

            return open_store()
        except (sqlite3.Error, OSError, NoustError) as exc:
            log.warning("The Docker events were not kept: %s", exc)
            return None


def _compose_apps() -> list[App]:
    """
    Returns:
        The applications whose containers Compose made.
    """
    return [app for app in get_store().list_apps() if is_compose_backed(app.app_type)]


def _owners(apps: list[App], listed: list[StackContainer]) -> dict[str, str]:
    """
    Say whose each container is.

    Args:
        apps: The applications of the Compose-backed types.
        listed: Every Compose container on the host.

    Returns:
        Container id to the domain of the application whose stack it belongs to; containers
        of stacks Noust does not manage are left out.
    """
    owners: dict[str, str] = {}
    identities = [(app.domain, stack_identity(app)) for app in apps]
    for container in listed:
        for domain, identity in identities:
            if identity.matches(container):
                owners[container.id] = domain
                break
    return owners


__all__ = ["ContainerWatch", "Reading", "describe_event", "parse_inspect"]
