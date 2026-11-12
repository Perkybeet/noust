# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What this machine has, as far as what Noust offers goes.

The table of functions (:mod:`noust.central.features`) says what each needs of the machine:
systemd, a web server, Docker, root, the journal. This module answers, and it detects nothing a
second time (rule 3): each fact is asked of the code that already knows it.

- **systemd**: the platform's detection (:func:`noust.managers.server.host.detect_platform`), which
  the Server area has always read.
- **a web server**: the managers of nginx and of Apache, which know their own binary.
- **Docker**: the door to it (:mod:`noust.core.docker`). A machine "has Docker" when its engine's
  doctor says Noust can use it, and when it cannot, the sentence the doctor says is what this
  reports, so one problem is said in the same words everywhere.
- **root**: whether Noust runs as root.
- **the journal**: ``journalctl`` on the path.

Docker is the one slow fact: asking the engine takes four processes, and a daemon that hangs makes
each of them last as long as its deadline. It is kept for :data:`TTL_DOCKER` seconds, refreshed in
the background when it is old, and only the first look of a process waits for it (a session that
said "no Docker" because the engine had not been asked yet would hide what is there until the next
load). Everything else costs a look at the path or at a directory, so the console's session can
carry all of it without a second request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from noust.core.runner import CommandRunner, get_runner
from noust.core.server_text import ServerText
from noust.core.utils import check_root
from noust.managers.server.facts import FactCache
from noust.managers.server.host import HostPaths, detect_platform

if TYPE_CHECKING:
    from noust.core.docker.engine import EngineReport

__all__ = [
    "TTL_DOCKER",
    "MachineFacts",
    "engine_report",
    "is_root",
    "read_machine_facts",
    "reset_machine_facts",
]

#: How long what the engine said stays fresh, in seconds.
TTL_DOCKER = 30.0

#: Where the engine's answer is kept; replaced in tests.
_cache = FactCache()

_DOCKER = "docker"


@dataclass(frozen=True)
class MachineFacts:
    """
    What the machine has.

    Attributes:
        systemd: It was booted with systemd, so services and timers can be kept.
        web_server: nginx or Apache is installed.
        docker: Noust can use the engine (the engine's doctor says so).
        root: Noust runs as root, which changing the system takes.
        journal: ``journalctl`` is there to read the system's log with.
        docker_problem: When ``docker`` is false, the doctor's sentence for why (no engine, no
            answer, too old, no Compose v2); None otherwise.
    """

    systemd: bool
    web_server: bool
    docker: bool
    root: bool
    journal: bool
    docker_problem: ServerText | None = None


def is_root() -> bool:
    """
    Returns:
        Whether Noust runs as root. A function of its own so that a modelled machine (the console
        server of the end-to-end suite) can say what its Noust is without changing who runs it.
    """
    return check_root()


def reset_machine_facts() -> None:
    """Forget what the engine said, so the next look asks it again."""
    _cache.invalidate()


def _web_server(runner: CommandRunner) -> bool:
    """
    Args:
        runner: Asks the machine.

    Returns:
        Whether nginx or Apache is installed, as their own managers say it.
    """
    from noust.managers.apache_manager import ApacheManager
    from noust.managers.nginx_manager import NginxManager

    return NginxManager(runner=runner).is_installed() or ApacheManager(runner=runner).is_installed()


def _ask_docker(runner: CommandRunner) -> EngineReport:
    """
    Ask the engine's doctor what Docker there is.

    Args:
        runner: Runs the doctor's questions.

    Returns:
        The doctor's report: what is, what does not fit and, from it, the first thing that stops
        Noust using this Docker.
    """
    from noust.core.docker import engine

    return engine.diagnose(engine.read_engine(runner))


def engine_report(runner: CommandRunner | None = None, *, fresh: bool = False) -> EngineReport:
    """
    The engine's doctor, from what it said not long ago.

    The one reading of the engine that anything that is not the Engine page of the Docker zone asks
    for: the session's functions and the zone's routes share it, so the engine is asked once in
    :data:`TTL_DOCKER` seconds and the first look of a process is the only one that waits.

    Args:
        runner: Runs the doctor's questions when it has to; the process-wide one by default.
        fresh: Ask the engine now whatever it said a moment ago, and keep the answer (the page that
            is the doctor itself, and a person who has just installed Docker, want the truth).

    Returns:
        The report. A question that failed is an engine that does not answer, with the reason it
        gave.
    """
    from noust.core.docker import engine

    active = runner or get_runner()
    if fresh:
        report = _ask_docker(active)
        _cache.put(_DOCKER, report)
        return report
    known = _cache.peek(_DOCKER, TTL_DOCKER) is not None
    fact = _cache.get(_DOCKER, lambda: _ask_docker(active), TTL_DOCKER, wait=not known)
    if fact is not None and fact.value is not None:
        found: EngineReport = fact.value
        return found
    reason = (fact.error if fact is not None else None) or "it did not say why"
    return engine.diagnose(engine.EngineInfo(installed=True, reachable=False, error=reason))


def _docker(runner: CommandRunner) -> tuple[bool, ServerText | None]:
    """
    Read what the engine said from the cache, asking it when it is not there.

    Args:
        runner: Runs the doctor's questions when it has to.

    Returns:
        Whether Noust can use this Docker and, when it cannot, the doctor's sentence for the first
        thing in the way: both read from :func:`engine_report`, which is where they are decided.
    """
    report = engine_report(runner)
    return report.ok, report.problem


def read_machine_facts(
    runner: CommandRunner | None = None, *, host: HostPaths | None = None
) -> MachineFacts:
    """
    Say what this machine has.

    Args:
        runner: Asks the machine; the process-wide one by default.
        host: Where the system files are; the machine's own by default.

    Returns:
        The facts. Nothing here raises for a machine that lacks something: that is the answer.
    """
    active = runner or get_runner()
    usable, problem = _docker(active)
    return MachineFacts(
        systemd=detect_platform(active, host).systemd,
        web_server=_web_server(active),
        docker=usable,
        root=is_root(),
        journal=active.exists("journalctl"),
        docker_problem=problem,
    )
