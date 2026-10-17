# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The web server of the machine, watched and started again when it falls.

Every site of every application goes through nginx (or Apache). Its unit was not among the ones
the monitor watched: the day it fell the applications were each "unreachable" one at a time, the
console said nothing about the one thing they had in common, and nothing started it again until
somebody logged in. Coolify starts its proxy when it finds it missing; this does the same, with
the care a root daemon owes a service every site depends on.

What it does on each look, per web server in use:

- **Running**: nothing, and if an episode was open, it closes it (``unit.recovered``).
- **Crashed** (``failed``, or ``inactive`` after a run that did not end in success): the server's
  own configuration test runs first, because restarting a configuration that does not pass takes
  nothing up and hides the cause. If it passes, the unit is restarted and the notification says
  so (``webserver.restarted``); if it does not, the notification carries the test's words
  verbatim and nothing is restarted (``webserver.config_invalid``). A restart that does not hold
  is ``webserver.restart_failed``. At most ``monitor.webserver_restart_limit`` restarts happen in
  an hour (3 by default); past it the limit is said once (``webserver.limit``) and the rest is
  the operator's.
- **Stopped cleanly** (``inactive`` after success, still enabled at boot): a person stopped it.
  It is announced once (``webserver.stopped``) and left alone.
- **Disabled and stopped**: off on purpose, nobody's outage.

``monitor.restart_webserver: false`` keeps the announcement and drops the restart
(``webserver.down``). It is the one check of the monitor that acts, and the one thing it does is
what ``systemctl restart`` would do; it is audited (``monitor.webserver_restart``).
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from noust.core import audit
from noust.core.notifications.composers import compose_unit_recovered
from noust.core.notifications.composers_netobs import compose_webserver_down
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.core.runner import CommandRunner
from noust.monitor.metrics import collect_service_health
from noust.monitor.models import ServiceHealth
from noust.monitor.watches import Watch, WatchServices

log = logging.getLogger(__name__)

#: The scope of what this watch remembers: when each server's open episode began.
SCOPE = "webserver"

DEFAULT_CHECK_SECONDS = 60
DEFAULT_RESTART_LIMIT = 3

#: The window the restart limit counts over.
RESTART_WINDOW_SECONDS = 3600

#: Lines of the journal a failed restart carries.
JOURNAL_LINES = 20

_CONTROL_TIMEOUT = 60
_JOURNAL_TIMEOUT = 15


@dataclass(frozen=True)
class WebServerRef:
    """
    One web server the machine may be running.

    Attributes:
        name: ``nginx`` or ``apache``, as the store records it.
        unit: The systemd unit.
        test: The configuration test.
    """

    name: str
    unit: str
    test: tuple[str, ...]


def _reference(name: str) -> WebServerRef:
    """
    Args:
        name: ``nginx`` or ``apache``.

    Returns:
        The server's unit and test, from the backends that already describe them.
    """
    from noust.managers.webserver import APACHE_BACKEND, NGINX_BACKEND

    backend = APACHE_BACKEND if name == "apache" else NGINX_BACKEND
    return WebServerRef(backend.name, backend.service, backend.config_test_argv)


def servers_in_use(services: WatchServices) -> list[str]:
    """
    Name the web servers this machine depends on.

    nginx when it is installed; Apache when it is installed and something runs on it (a site, or
    an application that records it), the same reading ``noust health`` makes.

    Args:
        services: What the monitor lends.

    Returns:
        ``nginx`` and/or ``apache``.
    """
    from noust.managers.apache_manager import ApacheManager
    from noust.managers.health import apache_in_use
    from noust.managers.nginx_manager import NginxManager

    found: list[str] = []
    if NginxManager(runner=services.runner).is_installed():
        found.append("nginx")
    apache = ApacheManager(runner=services.runner)
    if apache.is_installed() and apache_in_use(apache):
        found.append("apache")
    return found


def _outside_the_unit(argv: list[str]) -> list[str]:
    """
    Make a command run outside the sandbox of the unit the monitor runs in.

    The monitor's unit cannot write /run or /var/log (``ProtectSystem=strict``), and a web server's
    own configuration test opens both: ``nginx -t`` fails there with ``open() "/run/nginx.pid"
    failed (30: Read-only file system)``, so a healthy configuration would read as broken and the
    server would never be restarted. A transient unit starts from systemd, with the filesystem the
    server itself sees, and ``--pipe --wait`` hand back its words and its exit status.

    Args:
        argv: The command.

    Returns:
        The command through ``systemd-run`` when the monitor is a systemd service and the tool is
        there; the command itself in a terminal, where nothing is restricted.
    """
    launcher = shutil.which("systemd-run") if os.environ.get("INVOCATION_ID") else None
    if launcher is None:
        return argv
    return [launcher, "--quiet", "--collect", "--wait", "--pipe", *argv]


def _crashed(health: ServiceHealth) -> bool:
    """
    Args:
        health: The unit's state.

    Returns:
        True when it fell, as opposed to being stopped cleanly.
    """
    return health.active_state == "failed" or (
        health.active_state == "inactive" and health.result not in ("", "success")
    )


class WebServerWatch(Watch):
    """Announces a web server that is down and restarts it when it crashed."""

    name = "webserver"
    default_interval_s = float(DEFAULT_CHECK_SECONDS)

    def __init__(self, servers: Callable[[WatchServices], list[str]] | None = None) -> None:
        """
        Args:
            servers: Names the web servers in use; :func:`servers_in_use` by default. Tests
                inject one so that nothing reads the machine.
        """
        self._servers = servers or servers_in_use

    def interval_s(self, services: WatchServices) -> float:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            ``monitor.webserver_check_seconds``; zero (off) when it says so.
        """
        seconds = services.config.get("monitor.webserver_check_seconds", DEFAULT_CHECK_SECONDS)
        return float(seconds or 0)

    def check(self, services: WatchServices) -> None:
        """
        Look at each web server in use and act on what it finds.

        Args:
            services: What the monitor lends.
        """
        names = self._servers(services)
        refs = [_reference(name) for name in names]
        if not refs:
            return
        health = collect_service_health([ref.unit for ref in refs], runner=services.runner)
        for ref, reading in zip(refs, health, strict=True):
            self._judge(ref, reading, services)

    # -- One server --------------------------------------------------------------------------

    def _judge(self, ref: WebServerRef, health: ServiceHealth, services: WatchServices) -> None:
        """
        Decide what one server's reading calls for.

        Args:
            ref: The server.
            health: What systemd said of its unit.
            services: What the monitor lends.
        """
        episode = services.state.get(SCOPE, ref.name)
        if health.load_state == "not-found" or not health.active_state:
            return
        if health.active_state == "active":
            if episode is not None:
                self._close(ref, services)
                services.publish(partial(compose_unit_recovered, ref.unit))
            return
        if health.active_state in ("activating", "reloading", "deactivating"):
            return
        if _crashed(health):
            self._fell(ref, health, services, first=episode is None)
            return
        # Stopped cleanly: a person did it, so only a server that starts at boot is a worry.
        if not health.enabled:
            return
        if episode is None:
            services.state.put(SCOPE, ref.name, str(services.clock()))
            services.publish(self._tell("stopped", ref))

    def _close(self, ref: WebServerRef, services: WatchServices) -> None:
        """
        Forget an episode: the server is up.

        Args:
            ref: The server.
            services: What the monitor lends.
        """
        services.state.delete(SCOPE, ref.name)
        services.state.delete(SCOPE, f"{ref.name}:limit")

    def _fell(
        self, ref: WebServerRef, health: ServiceHealth, services: WatchServices, *, first: bool
    ) -> None:
        """
        Handle a crashed server: test, restart within the limit, say what happened.

        Args:
            ref: The server.
            health: Its unit's state.
            services: What the monitor lends.
            first: Whether this is the first look since it fell.
        """
        if first:
            services.state.put(SCOPE, ref.name, str(services.clock()))
        if not bool(services.config.get("monitor.restart_webserver", True)):
            if first:
                services.publish(self._tell("down", ref))
            return

        # The limit first: past it nothing is restarted, so the test (a transient unit, audited
        # each time) would only be run to be ignored at every look while the server stays down.
        limit = int(services.config.get("monitor.webserver_restart_limit", DEFAULT_RESTART_LIMIT))
        recent = self._recent_restarts(ref, services)
        if len(recent) >= limit:
            if services.state.get(SCOPE, f"{ref.name}:limit") is None:
                services.state.put(SCOPE, f"{ref.name}:limit", "1")
                services.publish(self._tell("limit", ref, restarts=len(recent)))
            return

        error = self._config_errors(ref, services.runner)
        if error is not None:
            if first:
                services.publish(self._tell("config_invalid", ref, output=error))
            return

        self._remember_restart(ref, recent, services)
        outcome = services.runner.run(["systemctl", "restart", ref.unit], timeout=_CONTROL_TIMEOUT)
        audit.record(
            "monitor.webserver_restart",
            target=f"webserver:{ref.name}",
            outcome="ok" if outcome.success else "failure",
            details={"unit": ref.unit, "state": health.active_state, "result": health.result},
        )
        if outcome.success:
            self._close(ref, services)
            services.publish(self._tell("restarted", ref))
            return
        if first:
            words = "\n".join(
                part
                for part in (outcome.stderr.strip(), self._journal(ref, services.runner))
                if part
            )
            services.publish(self._tell("restart_failed", ref, output=words))

    @staticmethod
    def _tell(
        outcome: str, ref: WebServerRef, *, output: str | None = None, restarts: int = 0
    ) -> Callable[[NotificationContext], Notification]:
        """
        Args:
            outcome: What happened (see :func:`compose_webserver_down`).
            ref: The server.
            output: What the test or the restart said.
            restarts: The restarts in the last hour.

        Returns:
            The composer, ready for the context.
        """
        return partial(
            compose_webserver_down,
            outcome,
            ref.name,
            unit=ref.unit,
            test=" ".join(ref.test),
            output=output,
            restarts=restarts,
        )

    # -- Reading and counting ----------------------------------------------------------------

    @staticmethod
    def _config_errors(ref: WebServerRef, runner: CommandRunner) -> str | None:
        """
        Run the server's own configuration test.

        Args:
            ref: The server.
            runner: The one way a process is run.

        Returns:
            None when it passes; otherwise the test's own output, verbatim.
        """
        result = runner.run(_outside_the_unit(list(ref.test)), timeout=_CONTROL_TIMEOUT)
        text = "\n".join(part for part in (result.stderr, result.stdout) if part.strip())
        # apache2ctl exits non-zero on warnings it then describes as "Syntax OK".
        if result.success or "Syntax OK" in text:
            return None
        return text or f"{' '.join(ref.test)} exited with status {result.exit_code}"

    @staticmethod
    def _journal(ref: WebServerRef, runner: CommandRunner) -> str:
        """
        Args:
            ref: The server.
            runner: The one way a process is run.

        Returns:
            The last lines of its journal; empty when they cannot be read.
        """
        result = runner.run(
            ["journalctl", "-u", ref.unit, "-n", str(JOURNAL_LINES), "--no-pager"],
            timeout=_JOURNAL_TIMEOUT,
        )
        return result.stdout.strip() if result.success else ""

    @staticmethod
    def _recent_restarts(ref: WebServerRef, services: WatchServices) -> list[float]:
        """
        Args:
            ref: The server.
            services: What the monitor lends.

        Returns:
            The moments, within the last hour, the monitor restarted it.
        """
        now = services.clock()
        raw = services.state.get(SCOPE, f"{ref.name}:restarts") or ""
        moments: list[float] = []
        for part in raw.split(","):
            try:
                moment = float(part)
            except ValueError:
                continue
            if now - moment < RESTART_WINDOW_SECONDS:
                moments.append(moment)
        return moments

    @staticmethod
    def _remember_restart(ref: WebServerRef, recent: list[float], services: WatchServices) -> None:
        """
        Args:
            ref: The server.
            recent: The restarts of the last hour.
            services: What the monitor lends.
        """
        moments = [*recent, services.clock()]
        services.state.put(SCOPE, f"{ref.name}:restarts", ",".join(str(m) for m in moments))
        # A new hour is a new chance to say the limit was reached.
        services.state.delete(SCOPE, f"{ref.name}:limit")


__all__ = ["WebServerWatch", "servers_in_use"]
