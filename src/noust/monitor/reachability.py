# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whether an application answers, and when to say it stopped (owner item 58).

``unit_failed`` is about systemd giving a unit up. An application whose unit
is active but which no longer answers (a Compose stack whose container died,
a hung process, a port that changed) raised nothing, and showed up only under
"Needs attention" in a console nobody had open. The monitor now asks each
application what the deploy's health gate asks (:class:`HealthCheck`) and
announces an outage with ``app_unreachable``, then its end with
``app_recovered``.

Two things are decided here, apart from the monitor loop that calls them:

- :class:`OutageTracker` is the rule for when a run of failed probes is an
  outage: three in a row, spanning at least a minute (a restart or a deploy
  never lasts that long), announced once, closed by the first success.
- :func:`probe_application` and :func:`busy_reason` are what one scan asks
  about one application: how it answers, and whether something is busy
  changing it, in which case it is not asked.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPException
from typing import TYPE_CHECKING, Any

from noust.core import applock
from noust.deployers.bluegreen import serving_port
from noust.deployers.helpers.health import wait_until_healthy
from noust.deployers.helpers.health_gate import HealthCheck
from noust.deployers.helpers.php_fpm import is_php_fpm

if TYPE_CHECKING:
    from noust.core.runner import CommandRunner
    from noust.core.store import App, NoustStore
    from noust.deployers.docker_compose import HeadlessStackState

#: Failed probes in a row that make an outage.
FAILURES_TO_ANNOUNCE = 3

#: ...spanning at least this many seconds from the first failure to the last,
#: so a restart, a deploy or a slow start is not announced as an outage.
MIN_OUTAGE_SECONDS = 60.0

#: A run of failures the monitor did not see the middle of is not "in a row":
#: when the daemon was stopped for longer than this between two probes, the
#: count starts again. An announced outage is kept whatever the gap, because
#: its end is still worth telling.
MAX_GAP_SECONDS = 600.0

#: How long a lock or a job that says it is running is believed. Deploys and
#: jobs end in minutes; a record older than this was left by a process that
#: died without clearing it, and trusting it would silence an application for
#: good.
BUSY_MAX_AGE_SECONDS = 6 * 3600.0

#: What a failed attempt of :func:`wait_until_healthy` is called, minus the
#: attempt number that means nothing for a single probe.
_ATTEMPT = re.compile(r"^Health check attempt \d+ failed: (.*)$", re.DOTALL)


@dataclass(frozen=True)
class Outage:
    """
    An application that has stopped answering, to be announced.

    Attributes:
        domain: The application.
        since: When the first failed probe of the run happened (epoch seconds).
        probe: The latest failed probe, verbatim.
    """

    domain: str
    since: float
    probe: str


@dataclass(frozen=True)
class Recovery:
    """
    An application announced as not answering that answers again.

    Attributes:
        domain: The application.
        down_for_s: Seconds from the first failed probe to the first answer.
    """

    domain: str
    down_for_s: float


@dataclass
class _Run:
    """One application's run of failed probes."""

    first: float
    last: float
    failures: int = 1
    announced: bool = False
    probe: str = ""


class OutageTracker:
    """
    Decide from a stream of probes when an application is down, and when it is not.

    Pure bookkeeping with the clock passed in: it asks nothing of the machine,
    so the rule is tested without one. Its state is plain data
    (:meth:`to_dict`) because the daemon runs for months and restarts on
    every upgrade: an outage must not be announced twice because of one.
    """

    def __init__(self) -> None:
        self._runs: dict[str, _Run] = {}

    def fail(self, domain: str, probe: str, *, now: float) -> Outage | None:
        """
        Record a probe that failed.

        Args:
            domain: The application.
            probe: What the probe said, verbatim.
            now: Epoch seconds.

        Returns:
            The outage, the one time the run of failures becomes one; None
            before that, and for every failure after it.
        """
        run = self._runs.get(domain)
        if run is not None and not run.announced and now - run.last > MAX_GAP_SECONDS:
            run = None
        if run is None:
            self._runs[domain] = _Run(first=now, last=now, probe=probe)
            return None
        run.last = now
        run.failures += 1
        run.probe = probe
        if run.announced:
            return None
        if run.failures >= FAILURES_TO_ANNOUNCE and now - run.first >= MIN_OUTAGE_SECONDS:
            run.announced = True
            return Outage(domain, since=run.first, probe=probe)
        return None

    def succeed(self, domain: str, *, now: float) -> Recovery | None:
        """
        Record a probe that passed.

        Args:
            domain: The application.
            now: Epoch seconds.

        Returns:
            The recovery when an announced outage ends; None otherwise (a
            run of failures that never became one is forgotten silently).
        """
        run = self._runs.pop(domain, None)
        if run is None or not run.announced:
            return None
        return Recovery(domain, down_for_s=max(0.0, now - run.first))

    def hold(self, domain: str) -> None:
        """
        Note that the application was not probed because something is changing it.

        A deploy or a job cuts a run of failures: what failed before it says
        nothing about what it leaves behind. An outage already announced
        stays open, so its first success after the change still closes it.

        Args:
            domain: The application.
        """
        run = self._runs.get(domain)
        if run is not None and not run.announced:
            del self._runs[domain]

    def forget(self, domain: str) -> None:
        """
        Drop everything known about an application, announcing nothing.

        For an application the operator stopped on purpose: nothing is
        unreachable about it, and an "answering again" weeks later would be
        noise.

        Args:
            domain: The application.
        """
        self._runs.pop(domain, None)

    def keep_only(self, domains: set[str]) -> None:
        """
        Drop what is known about applications that no longer exist.

        Args:
            domains: The applications there are.
        """
        for domain in set(self._runs) - domains:
            del self._runs[domain]

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The state as JSON-ready data.
        """
        return {
            domain: {
                "first": run.first,
                "last": run.last,
                "failures": run.failures,
                "announced": run.announced,
                "probe": run.probe,
            }
            for domain, run in sorted(self._runs.items())
        }

    @classmethod
    def from_dict(cls, data: object) -> OutageTracker:
        """
        Read the state back.

        Args:
            data: What :meth:`to_dict` returned, as read from disk. Anything
                else, and any entry that is not well formed, is ignored: a
                corrupt sidecar means "start counting again", never "stop
                watching".

        Returns:
            The tracker.
        """
        tracker = cls()
        if not isinstance(data, dict):
            return tracker
        for domain, entry in data.items():
            try:
                tracker._runs[str(domain)] = _Run(
                    first=float(entry["first"]),
                    last=float(entry["last"]),
                    failures=int(entry["failures"]),
                    announced=bool(entry["announced"]),
                    probe=str(entry["probe"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return tracker


# -- what one scan asks about one application ---------------------------------


@dataclass(frozen=True)
class AppProbe:
    """
    What one probe of an application found.

    Attributes:
        ok: Whether it answered, or its containers run.
        detail: Empty when it is fine; otherwise the probe verbatim: the
            request and what came back, or one line per container that is
            not fine.
        containers: Whether it was judged by its containers (a Compose stack
            with no published port) rather than by an HTTP request.
    """

    ok: bool
    detail: str
    containers: bool = False


def probe_application(
    app: App,
    *,
    runner: CommandRunner | None = None,
    http: Callable[..., bool] = wait_until_healthy,
    stack_state: Callable[[App], HeadlessStackState | None] | None = None,
) -> AppProbe | None:
    """
    Ask an application whether it answers, the way the health gate does.

    The request, its path and which statuses count come from the
    application's own :class:`HealthCheck`, and it goes to the port that
    serves now, so this never calls healthy what a deploy would roll back or
    the reverse. A Compose stack that publishes no port has nothing to
    request: its containers say how it is (:func:`headless_stack_state`, the
    one place that judges them).

    Args:
        app: The application row.
        runner: What ``docker compose`` goes through, for a stack.
        http: The gate's HTTP probe; injectable for tests.
        stack_state: Judges a Compose stack by its containers, None for the
            real one.

    Returns:
        What it found, or None when there is nothing to ask: a static site
        or a PHP pool (no process to request), or no port.

    Raises:
        NoustError: A Compose stack's containers could not be listed for a
            reason that says nothing about the stack (an unreadable file).
        OSError: Reading it failed.
    """
    if app.is_static or is_php_fpm(app):
        return None

    if app.app_type == "docker-compose":
        if stack_state is None:
            from noust.deployers.docker_compose import headless_stack_state

            def stack_state(row: App) -> HeadlessStackState | None:
                return headless_stack_state(row, runner=runner)

        state = stack_state(app)
        if state is not None:
            if state.healthy:
                return AppProbe(ok=True, detail="", containers=True)
            lines = list(state.problems) or [state.summary]
            if state.output and not state.problems:
                lines.append(state.output)
            return AppProbe(ok=False, detail="\n".join(lines), containers=True)

    port = serving_port(app)
    if not port:
        return None
    check = HealthCheck.for_app(app)
    url = check.url(port)
    causes: list[str] = []
    try:
        if http(url, retries=1, accept=check.accepts, on_attempt=causes.append):
            return AppProbe(ok=True, detail="")
    except HTTPException as exc:
        # urllib lets http.client's own errors through (a server that sends
        # something that is not HTTP, or hangs up mid-answer). That is a
        # server that does not answer, which is what is being asked.
        return AppProbe(ok=False, detail=f"GET {url} -> {type(exc).__name__}: {exc}")
    cause = _ATTEMPT.sub(r"\1", causes[-1]) if causes else "no answer"
    return AppProbe(ok=False, detail=f"GET {url} -> {cause}")


def busy_reason(domain: str, store: NoustStore, *, now: float) -> str | None:
    """
    Say whether something is changing an application, in which case it is not asked.

    A deploy, an update, a rollback or a restore restarts the application on
    purpose, and a job queued from the console may do the same; a probe
    during either measures the operation, not the application. The lock every
    such operation takes is the one definition of "a deploy is running"; jobs
    are read from the store because some do not take it.

    Args:
        domain: The application.
        store: Where jobs are kept.
        now: Epoch seconds.

    Returns:
        A phrase naming what is running, or None when nothing is. A record
        older than :data:`BUSY_MAX_AGE_SECONDS` is a leftover, not an
        operation.
    """
    holder = applock.holder_of(domain)
    if holder is not None and now - _epoch(holder.started_at) < BUSY_MAX_AGE_SECONDS:
        return f"{holder.operation} started at {holder.started_at}"
    for status in ("running", "pending"):
        for record in store.list_jobs(limit=10, status=status, domain=domain):
            since = record.started_at or record.created_at
            if since is not None and now - _epoch(since) < BUSY_MAX_AGE_SECONDS:
                return f"job {record.type} ({record.status})"
    return None


def _epoch(moment: str) -> float:
    """
    Read a time a record kept.

    Args:
        moment: ISO 8601, as the lock and the jobs table write it.

    Returns:
        Epoch seconds; 0 for one that cannot be read, which is as old as
        a record can be, so it is not believed.
    """
    try:
        return datetime.fromisoformat(moment).timestamp()
    except ValueError:
        return 0.0
