# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Probes: asking an address, from outside the application, whether it answers (checklist G-17).

The monitor knows an application is up from the inside: its unit runs, its health
check passes on the loopback. A visitor asks something else: does the name resolve,
does the certificate hold, does the proxy answer, does the page say what it should.
A probe asks that, on a clock, from where Noust runs. On a server of its own, or on
a central that manages the others, that is a point of view outside the machines it
watches; on the machine itself it still catches the expired certificate and the
firewall rule that only the loopback survives.

- **One ask, one answer** (:func:`ask`): an HTTP request through the notifier's
  guarded opener (the SSRF guard, asked again at every redirect), judged by the
  probe's statuses and, if it names one, a word in the first 64 kB of the page.
  The answer keeps a status, a latency and, when it was bad, one line saying why;
  a body is never stored.
- **A state machine** (:class:`ProbeEngine`): *down* needs ``fail_after`` bad answers
  in a row, so a restart or a deploy is not an outage; one good answer is *up*
  again. Each change is told once, and each stretch down is an incident with its
  start and end.
- **Never blocks the monitor.** Asks run on a small pool, at most one in flight
  per probe, and their answers are applied on the scan after they arrive.
"""

from __future__ import annotations

import logging
import re
import socket
import ssl
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request

from noust import __version__
from noust.core.config import Config
from noust.core.exceptions import ValidationError
from noust.core.notifications.composers_watch import compose_probe
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.core.notifier import guarded_opener, require_public_destination
from noust.core.probe_store import Incident, Probe, ProbeState, ProbeStore, Uptime

log = logging.getLogger(__name__)

#: The most of a page that is read to look for a word.
MAX_BODY = 64 * 1024

#: Redirects a probe follows.
MAX_REDIRECTS = 5

#: Probes asked at once.
MAX_WORKERS = 8

USER_AGENT = f"noust-probe/{__version__}"

#: An address inside an error message.
_ADDRESS = re.compile(r"https?://\S+")

Publish = Callable[[Callable[[NotificationContext], Notification]], None]
Opener = Callable[..., Any]


@dataclass(frozen=True)
class Answer:
    """
    What asking an address got.

    Attributes:
        ok: It answered well: a status the probe accepts and the word it wants.
        status: The HTTP status, if there was one.
        latency_ms: How long the answer took, up to the last byte read.
        error: One line saying what was wrong; None when it was good.
    """

    ok: bool
    status: int | None = None
    latency_ms: int | None = None
    error: str | None = None


def public_address(url: str) -> str:
    """
    Args:
        url: A probe's address.

    Returns:
        It without a query or a fragment: what a notification or a public page
        may show, because the query is where an address keeps its key.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def describe_error(error: BaseException) -> str:
    """
    Say in one line why an ask failed, without quoting the address.

    Args:
        error: What the request raised.

    Returns:
        A short sentence; a connection refused, a name that does not resolve, a
        timeout and a certificate that does not verify each read as such.
    """
    reason: Any = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timed out"
    if isinstance(reason, ssl.SSLCertVerificationError):
        said = getattr(reason, "verify_message", None) or getattr(reason, "reason", None)
        return f"the certificate did not verify: {said or reason}"[:200]
    if isinstance(reason, ssl.SSLError):
        return f"TLS failed: {getattr(reason, 'reason', None) or reason}"[:200]
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    if isinstance(reason, ConnectionResetError):
        return "connection reset"
    if isinstance(reason, socket.gaierror):
        return "the name does not resolve"
    # The SSRF guard, a URL urllib will not send, or whatever else: its own words
    # in one line, and never the address, whose query may be a key.
    text = (str(reason) or type(reason).__name__).splitlines()[0]
    return _ADDRESS.sub("<address>", text)[:200]


def ask(
    probe: Probe,
    *,
    config: Config | None = None,
    opener: Opener | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Answer:
    """
    Ask an address once.

    Args:
        probe: What to ask.
        config: The configuration, for the SSRF guard's allow-list.
        opener: Replacement for the guarded opener; tests inject one so the
            suite never opens a socket.
        clock: Monotonic seconds, for the latency.

    Returns:
        What came back. Never raises for a bad answer: a refused connection, a
        certificate that does not verify and a 503 are all answers.
    """
    cfg = config or Config()
    send = opener or guarded_opener(cfg)
    started = clock()
    deadline = started + probe.timeout_s
    status: int | None = None
    try:
        # Asked before the request and at every redirect (the opener), so a name
        # that resolves to this machine's own network is refused as a notification is.
        require_public_destination(probe.url, "probe address")
        request = Request(
            probe.url,
            method=probe.method,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )
        try:
            response = send(request, timeout=probe.timeout_s)
        except HTTPError as exc:
            response = exc
        with response:
            reported: Any = getattr(response, "status", None) or getattr(response, "code", 0)
            status = int(reported)
            body = b""
            if probe.expect_text and probe.method == "GET":
                body = _read(response, deadline, clock)
            elapsed = int((clock() - started) * 1000)
    except (URLError, OSError, ValueError) as exc:
        return Answer(False, status, int((clock() - started) * 1000), describe_error(exc))
    if not probe.accepts(status):
        return Answer(False, status, elapsed, f"HTTP {status}")
    if probe.expect_text and probe.expect_text.encode("utf-8") not in body:
        return Answer(False, status, elapsed, "the page does not contain the expected text")
    return Answer(True, status, elapsed)


def _read(response: Any, deadline: float, clock: Callable[[], float]) -> bytes:
    """
    Read the start of a page, giving up when the time is up.

    Args:
        response: The open response.
        deadline: Monotonic seconds after which it stops reading.
        clock: Monotonic seconds.

    Returns:
        At most :data:`MAX_BODY` bytes. A server that drips its answer a byte at
        a time cannot hold the probe past its timeout.
    """
    chunks: list[bytes] = []
    total = 0
    while total < MAX_BODY and clock() < deadline:
        chunk = response.read(min(4096, MAX_BODY - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _row(probe: Probe) -> int:
    """
    Args:
        probe: A probe read from the store.

    Returns:
        Its row.

    Raises:
        ValueError: When it was never stored (only stored probes are asked).
    """
    if probe.id is None:
        raise ValueError(f"The probe {probe.name!r} was never stored")
    return probe.id


@dataclass
class Check:
    """
    One probe asked and what it did to its state.

    Attributes:
        probe: The probe.
        answer: What came back.
        state: Where it stands now.
        changed: ``down`` or ``up`` when this answer changed it, else None.
    """

    probe: Probe
    answer: Answer
    state: ProbeState
    changed: str | None = None


class ProbeEngine:
    """
    Asks the probes that are due and keeps what they answered.

    Args:
        store: Where the probes and their state are.
        publish: Composes a notification from the context and delivers it.
        ask_one: Asks one probe; :func:`ask` by default. Tests inject one.
        clock: Epoch seconds.
        executor: Where asks run. None asks inline, one after the other: what
            the command line and the tests want; the monitor gives it a pool.
    """

    def __init__(
        self,
        store: ProbeStore,
        publish: Publish,
        *,
        ask_one: Callable[[Probe], Answer] | None = None,
        clock: Callable[[], float] = time.time,
        executor: Executor | None = None,
    ) -> None:
        self._store = store
        self._publish = publish
        self._ask = ask_one or (lambda probe: ask(probe))
        self._clock = clock
        self._executor = executor
        self._inflight: dict[int, tuple[Probe, Future[Answer], float]] = {}

    def tick(self) -> list[Check]:
        """
        Apply the answers that came in, then ask what is due.

        Returns:
            The checks whose answers were applied now. With an executor those
            are the asks of earlier ticks; without one, this tick's.
        """
        applied = self._harvest()
        now = self._clock()
        states = self._store.states()
        for probe in self._store.probes():
            if probe.id is None or not probe.enabled or probe.id in self._inflight:
                continue
            last = states.get(probe.id)
            if last is not None and last.last_checked is not None:
                if now - last.last_checked < probe.interval_s:
                    continue
            applied.extend(self._start(probe, now))
        return applied

    def check_now(self, probe: Probe) -> Check:
        """
        Ask one probe now, whatever its interval, and apply the answer.

        Args:
            probe: The probe.

        Returns:
            The check.
        """
        answer = self._ask(probe)
        return self._apply(probe, answer, self._clock())

    def _start(self, probe: Probe, now: float) -> list[Check]:
        """
        Ask a probe, inline or on the pool.

        Args:
            probe: The probe.
            now: Epoch seconds.

        Returns:
            The check when it was asked inline; nothing when it was queued.
        """
        if self._executor is None:
            return [self._apply(probe, self._ask(probe), now)]
        self._inflight[_row(probe)] = (probe, self._executor.submit(self._ask, probe), now)
        return []

    def _harvest(self) -> list[Check]:
        """
        Returns:
            The checks of the asks that have finished.
        """
        done: list[Check] = []
        for probe_id, (probe, future, asked_at) in list(self._inflight.items()):
            if not future.done():
                continue
            del self._inflight[probe_id]
            try:
                answer = future.result()
            except (OSError, ValueError, URLError) as exc:
                # ask() judges every ordinary failure itself; this is what it could not.
                answer = Answer(False, None, None, describe_error(exc))
            done.append(self._apply(probe, answer, asked_at))
        return done

    def _apply(self, probe: Probe, answer: Answer, now: float) -> Check:
        """
        Keep an answer and move the probe's state.

        Args:
            probe: The probe.
            answer: What came back.
            now: When it was asked.

        Returns:
            The check.
        """
        probe_id = _row(probe)
        state = self._store.state(probe_id)
        self._store.record_result(
            probe_id,
            ok=answer.ok,
            status=answer.status,
            latency_ms=answer.latency_ms,
            error=answer.error,
            at=now,
        )
        common: dict[str, Any] = {
            "last_checked": now,
            "last_status": answer.status,
            "last_latency_ms": answer.latency_ms,
            "last_error": answer.error,
        }
        changed: str | None = None
        if answer.ok:
            if state.state == "down":
                changed = "up"
                self._store.close_incident(probe_id, now)
                if state.announced:
                    self._tell(probe, recovered_from=state, now=now, latency=answer.latency_ms)
            new = ProbeState(
                probe_id,
                "up",
                now if state.state != "up" else state.since,
                failures=0,
                announced=False,
                failing_since=None,
                **common,
            )
        else:
            failures = state.failures + 1
            failing_since = state.failing_since if state.failures else now
            if state.state == "down":
                new = ProbeState(
                    probe_id,
                    "down",
                    state.since,
                    failures=failures,
                    announced=state.announced,
                    failing_since=state.failing_since,
                    **common,
                )
            elif failures >= probe.fail_after:
                changed = "down"
                began = failing_since if failing_since is not None else now
                self._store.open_incident(probe_id, began, answer.error or "")
                self._tell(probe, failed=answer, since=began)
                new = ProbeState(
                    probe_id,
                    "down",
                    began,
                    failures=failures,
                    announced=True,
                    failing_since=began,
                    **common,
                )
            else:
                new = ProbeState(
                    probe_id,
                    state.state,
                    state.since or now,
                    failures=failures,
                    announced=False,
                    failing_since=failing_since,
                    **common,
                )
        self._store.put_state(new)
        return Check(probe, answer, new, changed)

    def _tell(
        self,
        probe: Probe,
        *,
        failed: Answer | None = None,
        since: float | None = None,
        recovered_from: ProbeState | None = None,
        now: float = 0.0,
        latency: int | None = None,
    ) -> None:
        """
        Tell the channels a probe went down, or came back.

        Args:
            probe: The probe.
            failed: The answer that made it down, for a failure.
            since: When the bad answers began.
            recovered_from: The state it was down in, for a recovery.
            now: When it answered again.
            latency: How long that answer took.
        """
        address = public_address(probe.url)
        if failed is not None:
            self._publish(
                partial(
                    compose_probe,
                    probe.name,
                    True,
                    address=address,
                    reason=failed.error or "",
                    since=datetime.fromtimestamp(since or 0, tz=timezone.utc),
                )
            )
        elif recovered_from is not None:
            began = recovered_from.failing_since or recovered_from.since
            self._publish(
                partial(
                    compose_probe,
                    probe.name,
                    False,
                    address=address,
                    down_for_s=max(0.0, now - began),
                    latency_ms=latency,
                )
            )


@dataclass(frozen=True)
class ProbeView:
    """
    A probe, where it stands and how it has done: what the API and the CLI show.

    Attributes:
        probe: The probe.
        state: Where it stands.
        uptime_24h: How often it answered in the last day.
        uptime_7d: And the last week.
        uptime_30d: And the last month.
        uptime_90d: And the last ninety days.
    """

    probe: Probe
    state: ProbeState
    uptime_24h: Uptime
    uptime_7d: Uptime
    uptime_30d: Uptime
    uptime_90d: Uptime

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The view as the API shows it.
        """

        def uptime(value: Uptime) -> dict[str, Any]:
            return {
                "checks": value.checks,
                "good": value.good,
                "percent": value.percent,
                "mean_ms": value.mean_ms,
                "max_ms": value.max_ms,
            }

        return {
            **self.probe.to_dict(),
            "address": public_address(self.probe.url),
            "status": self.state.to_dict(),
            "uptime": {
                "24h": uptime(self.uptime_24h),
                "7d": uptime(self.uptime_7d),
                "30d": uptime(self.uptime_30d),
                "90d": uptime(self.uptime_90d),
            },
        }


class ProbeManager:
    """
    The probes as an operator manages them: list, save, remove, ask now.

    Args:
        store: The probes' tables; the process-wide store's by default.
        config: The configuration, for the SSRF guard.
        publish: Where a state change is told; the notifier's by default.
        ask_one: Asks one probe (tests).
        clock: Epoch seconds (tests).
    """

    def __init__(
        self,
        store: ProbeStore | None = None,
        *,
        config: Config | None = None,
        publish: Publish | None = None,
        ask_one: Callable[[Probe], Answer] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store or ProbeStore()
        self._config = config
        self._publish = publish
        self._ask_one = ask_one
        self._clock = clock

    @property
    def store(self) -> ProbeStore:
        """The probes' tables."""
        return self._store

    def views(self) -> list[ProbeView]:
        """
        Returns:
            Every probe with where it stands and its uptime.
        """
        states = self._store.states()
        found: list[ProbeView] = []
        for probe in self._store.probes():
            probe_id = _row(probe)
            found.append(
                ProbeView(
                    probe=probe,
                    state=states.get(probe_id, ProbeState(probe_id=probe_id)),
                    uptime_24h=self._store.uptime(probe_id, 1),
                    uptime_7d=self._store.uptime(probe_id, 7),
                    uptime_30d=self._store.uptime(probe_id, 30),
                    uptime_90d=self._store.uptime(probe_id, 90),
                )
            )
        return found

    def view(self, name: str) -> ProbeView | None:
        """
        Args:
            name: A probe's name.

        Returns:
            The probe's view, or None.
        """
        return next((view for view in self.views() if view.probe.name == name), None)

    def incidents(self, name: str, *, limit: int = 20) -> list[Incident]:
        """
        Args:
            name: A probe's name.
            limit: How many at most.

        Returns:
            Its newest incidents first; empty when there is no such probe.
        """
        probe = self._store.get(name)
        if probe is None or probe.id is None:
            return []
        return self._store.incidents(probe.id, limit=limit)

    def save(self, probe: Probe, *, actor: str | None = None) -> Probe:
        """
        Create a probe or replace the one of that name.

        Args:
            probe: The probe.
            actor: Who saves it.

        Returns:
            The probe as stored.

        Raises:
            ValidationError: It is not acceptable, or its address resolves into
                this machine's own network and is not on the allow-list.
        """
        try:
            require_public_destination(probe.url.strip(), "probe address", unresolved_ok=True)
        except ValueError as exc:
            raise ValidationError(
                str(exc).splitlines()[0],
                details="An address on your own network can be asked: list its host under "
                "notifications.allow_private_hosts.",
                field="url",
            ) from exc
        return self._store.save(probe, actor=actor)

    def remove(self, name: str) -> bool:
        """
        Args:
            name: A probe's name.

        Returns:
            Whether there was one.
        """
        return self._store.remove(name)

    def check(self, name: str) -> Check | None:
        """
        Ask a probe now, whatever its interval.

        Args:
            name: A probe's name.

        Returns:
            What it answered; None when there is no such probe.
        """
        probe = self._store.get(name)
        if probe is None:
            return None
        engine = ProbeEngine(
            self._store,
            self._publish or _publisher(),
            ask_one=self._ask_one or (lambda one: ask(one, config=self._config)),
            clock=self._clock,
        )
        return engine.check_now(probe)


def _publisher() -> Publish:
    """
    Returns:
        The notifier's way of composing and delivering a notification.
    """
    from noust.core.notifier import notify_composed

    return notify_composed
