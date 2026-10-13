# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Sending the logs of applications to the destinations of the server (checklist B-73).

Applications log to the journal (their units say ``StandardOutput=journal``), and a
Compose stack's containers log to Docker. The shipper reads both from where each
destination got to and sends what is new, once per cycle:

1. **The journal**, with ``journalctl --after-cursor``: one cursor per destination,
   so a destination that was down receives what it missed when it is back and one
   added later starts from now. Entries are matched to applications by their unit
   (``shop-example-com``, ``shop-example-com@blue``, a process
   ``shop-example-com-worker@2``); the rest of the journal is never read into a line.
2. **A stack's containers**, with ``docker logs --since --until``: one timestamp per
   destination and stack.

Each line is scrubbed of the secrets its application holds before it leaves, a batch
is accepted or sent again (the cursor moves only after a destination accepted it:
delivery is at least once), and what went wrong is on the destination for the
operator to read. A destination that fails does not stop the others.

The shipper is a loop of cycles (:meth:`LogShipper.run`), which ``noust logdrain run``
runs and ``noust-logship.service`` supervises.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core import log_drain_store
from noust.core.exceptions import NoustError
from noust.core.log_drain_store import Drain, secret_name
from noust.core.redact import Scrubber, scrubber_for
from noust.core.runner import CommandRunner, get_runner
from noust.core.secrets import SecretStore
from noust.core.store import App, NoustStore, get_store, is_compose_backed
from noust.core.utils import domain_to_app_name
from noust.managers.log_drains import build_transport
from noust.managers.log_transports import DeliveryError, LogEvent, Transport, batches
from noust.terminal.targets import StackContainer, find_stack_containers

logger = logging.getLogger(__name__)

#: Entries read from the journal for one destination in one read, and the most
#: reads in one cycle: a long outage is caught up over a few cycles.
JOURNAL_ENTRIES = 1000
JOURNAL_READS = 10

#: Seconds between cycles.
CYCLE_SECONDS = 5

#: Deadlines of the commands the shipper runs.
JOURNAL_TIMEOUT = 60
COMPOSE_TIMEOUT = 60

#: The key of the journal's cursor in a destination's cursors.
JOURNAL_KEY = "journal"

#: A ``docker logs --timestamps`` line: ``2026-10-13T10:00:00.123456789Z text``.
_CONTAINER_LINE = re.compile(r"^(?P<ts>\d{4}-\d\d-\d\dT[\d:.]+Z) ?(?P<text>.*)$")


@dataclass
class CycleReport:
    """
    What one cycle did.

    Attributes:
        sent: Lines accepted, per destination.
        failed: Why a destination did not accept, per destination.
        skipped: Destinations not sent to, with why.
    """

    sent: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


class AppIndex:
    """Which application a journal entry or a unit belongs to."""

    def __init__(self, apps: list[App], processes: dict[str, set[str]]) -> None:
        """
        Args:
            apps: The applications that have a unit.
            processes: Domain to the names of its processes.
        """
        self._bases: dict[str, str] = {}
        for app in apps:
            base = domain_to_app_name(app.domain)
            self._bases[base] = app.domain
            self._bases[f"wasm-{base}"] = app.domain
        self._processes = processes

    def lookup(self, unit: str | None) -> tuple[str, str] | None:
        """
        Find the application a unit belongs to.

        Args:
            unit: A systemd unit name, such as ``shop-example-com-worker@2.service``.

        Returns:
            ``(domain, process)`` with ``web`` for the application's own unit, or
            None when it is not an application's.
        """
        if not unit:
            return None
        name = unit.removesuffix(".service").split("@", 1)[0]
        if name in self._bases:
            return self._bases[name], "web"
        for base in sorted(self._bases, key=len, reverse=True):
            if name.startswith(f"{base}-"):
                domain = self._bases[base]
                process = name[len(base) + 1 :]
                if process in self._processes.get(domain, ()):
                    return domain, process
        return None


def _message(entry: dict[str, Any]) -> str | None:
    """
    Read the text of a journal entry.

    Args:
        entry: One decoded entry.

    Returns:
        The text; entries that carry bytes (a list of numbers) are decoded; None
        when there is nothing to ship.
    """
    value = entry.get("MESSAGE")
    if isinstance(value, list):
        try:
            value = bytes(value).decode("utf-8", errors="replace")
        except (TypeError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.rstrip("\n")
    return text if text.strip() else None


def _priority(entry: dict[str, Any]) -> int:
    """
    Read the severity of a journal entry.

    Args:
        entry: One decoded entry.

    Returns:
        0 to 7; 6 (informational) when the entry says nothing usable.
    """
    try:
        value = int(entry.get("PRIORITY", 6))
    except (TypeError, ValueError):
        return 6
    return value if 0 <= value <= 7 else 6


def _rfc3339(nanoseconds: int) -> str:
    """
    Write nanoseconds since the epoch as the timestamp ``docker logs`` takes.

    Args:
        nanoseconds: Since the epoch.

    Returns:
        ``2026-10-13T10:00:00.123456789Z``.
    """
    seconds, nanos = divmod(nanoseconds, 1_000_000_000)
    moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{nanos:09d}Z"


def _ns_of_rfc3339(text: str) -> int | None:
    """
    Turn an RFC 3339 timestamp with up to nanoseconds into nanoseconds since the epoch.

    Args:
        text: ``2026-10-13T10:00:00.123456789Z``.

    Returns:
        The nanoseconds, or None when it is not one.
    """
    body = text.rstrip("Z")
    whole, _, fraction = body.partition(".")
    try:
        moment = datetime.fromisoformat(whole).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    nanos = int((fraction + "000000000")[:9]) if fraction.isdigit() or fraction == "" else 0
    return int(moment.timestamp()) * 1_000_000_000 + nanos


class LogShipper:
    """
    Reads the logs of applications and sends them to the destinations.

    Attributes:
        host: This server's name, which every line carries.
    """

    def __init__(
        self,
        *,
        store: NoustStore | None = None,
        runner: CommandRunner | None = None,
        secrets: SecretStore | None = None,
        transport_for: Callable[[Drain, str | None], Transport] | None = None,
        scrubber_for_app: Callable[[str], Scrubber] | None = None,
        host: str | None = None,
    ) -> None:
        """
        Initialize the shipper.

        Args:
            store: The store; the process-wide one by default.
            runner: Runs ``journalctl`` and ``docker``; the process-wide one by default.
            secrets: Where destinations keep their tokens.
            transport_for: Makes a destination's transport; the real ones by default.
            scrubber_for_app: Makes the scrubber of an application's secrets.
            host: This server's name.
        """
        self._store = store
        self._runner = runner
        self._secrets = secrets
        self._transport_for = transport_for or build_transport
        self._scrubber_for = scrubber_for_app or scrubber_for
        self.host = host or socket.gethostname()
        self._scrubbers: dict[str, Scrubber] = {}

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    @property
    def runner(self) -> CommandRunner:
        """The command runner."""
        return self._runner or get_runner()

    # -- Cycles ---------------------------------------------------------------------

    def cycle(self) -> CycleReport:
        """
        Send what is new to every enabled destination, once.

        Returns:
            What was sent, what failed and what was left.
        """
        report = CycleReport()
        drains = [d for d in log_drain_store.list_drains(store=self.store) if d.enabled]
        if not drains:
            return report
        self._scrubbers = {}
        index = self._index()
        apps = {app.domain: app for app in self.store.list_apps() if isinstance(app, App)}
        for drain in drains:
            try:
                sent = self._ship(drain, index, apps)
            except DeliveryError as exc:
                message = (
                    f"{exc.message}: {exc.details}".strip(": ") if exc.details else exc.message
                )
                log_drain_store.record_failure(drain.name, message, store=self.store)
                report.failed[drain.name] = message
                logger.warning("Log destination %s: %s", drain.name, message)
                continue
            except NoustError as exc:
                log_drain_store.record_failure(drain.name, exc.message, store=self.store)
                report.failed[drain.name] = exc.message
                logger.warning("Log destination %s: %s", drain.name, exc.message)
                continue
            report.sent[drain.name] = sent
        return report

    def run(self, stop: threading.Event, *, interval: float = CYCLE_SECONDS) -> None:
        """
        Run cycles until told to stop.

        Args:
            stop: Set to end the loop; a cycle in progress finishes first.
            interval: Seconds between cycles.
        """
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.cycle()
            except (NoustError, OSError) as exc:
                # A cycle that cannot even read the store is tried again, never fatal.
                logger.error("A log shipping cycle failed: %s", exc)
            stop.wait(max(0.0, interval - (time.monotonic() - started)))

    # -- One destination ----------------------------------------------------------------

    def _index(self) -> AppIndex:
        """
        Build the map from units to applications.

        Returns:
            The index of the applications that run as units.
        """
        apps = [
            app
            for app in self.store.list_apps()
            if isinstance(app, App) and not is_compose_backed(app.app_type)
        ]
        names = {app.domain: self._process_names(app.domain) for app in apps}
        return AppIndex(apps, names)

    def _process_names(self, domain: str) -> set[str]:
        """
        Name an application's processes.

        Args:
            domain: The application.

        Returns:
            Their names.
        """
        from noust.core import app_process_store

        return {
            record.name for record in app_process_store.list_processes(domain, store=self.store)
        }

    def _ship(self, drain: Drain, index: AppIndex, apps: dict[str, App]) -> int:
        """
        Send one destination what it has not received.

        Args:
            drain: The destination.
            index: The map from units to applications.
            apps: The applications by domain.

        Returns:
            The lines it accepted.

        Raises:
            DeliveryError: It did not accept a batch.
        """
        secret = self._secret(drain)
        transport = self._transport_for(drain, secret)
        shipped = 0
        try:
            shipped += self._ship_journal(drain, index, transport)
            for domain in sorted(self._scope(drain, apps)):
                app = apps[domain]
                if is_compose_backed(app.app_type):
                    shipped += self._ship_stack(drain, app, transport)
        finally:
            transport.close()
        return shipped

    def _secret(self, drain: Drain) -> str | None:
        """
        Read a destination's token.

        Args:
            drain: The destination.

        Returns:
            The token, or None when it has none.
        """
        store = self._secrets or SecretStore()
        return store.read(secret_name(drain.name))

    def _scope(self, drain: Drain, apps: dict[str, App]) -> set[str]:
        """
        Name the applications a destination receives the logs of.

        Args:
            drain: The destination.
            apps: The applications by domain.

        Returns:
            Their domains.
        """
        return set(drain.apps) & set(apps) if drain.apps else set(apps)

    def _scrub(self, domain: str, text: str) -> str:
        """
        Take an application's secrets out of a line.

        Args:
            domain: The application.
            text: The line.

        Returns:
            The line without them.
        """
        scrubber = self._scrubbers.get(domain)
        if scrubber is None:
            scrubber = self._scrubbers[domain] = self._scrubber_for(domain)
        return scrubber.scrub(text)

    def _deliver(self, drain: Drain, transport: Transport, events: list[LogEvent]) -> int:
        """
        Send events in the requests a destination takes.

        Args:
            drain: The destination.
            transport: Its transport.
            events: The events, in order.

        Returns:
            How many were accepted.

        Raises:
            DeliveryError: A batch was not accepted.
        """
        accepted = 0
        for group in batches(events):
            transport.send(group)
            accepted += len(group)
        return accepted

    # -- The journal ------------------------------------------------------------------------

    def _journal(self, *args: str) -> list[dict[str, Any]]:
        """
        Read journal entries as JSON.

        Args:
            *args: Arguments of ``journalctl`` after ``-o json``.

        Returns:
            The entries.

        Raises:
            DeliveryError: ``journalctl`` failed; the shipper tries again.
        """
        result = self.runner.run(
            ["journalctl", "--no-pager", "-o", "json", *args], timeout=JOURNAL_TIMEOUT
        )
        if not result.success:
            raise DeliveryError("The journal could not be read", details=result.stderr.strip())
        entries = []
        for line in result.stdout.splitlines():
            try:
                decoded = json.loads(line)
            except ValueError:
                continue
            if isinstance(decoded, dict):
                entries.append(decoded)
        return entries

    def _ship_journal(self, drain: Drain, index: AppIndex, transport: Transport) -> int:
        """
        Send the application entries the journal has after a destination's cursor.

        Args:
            drain: The destination.
            index: The map from units to applications.
            transport: Its transport.

        Returns:
            The lines accepted.

        Raises:
            DeliveryError: The journal could not be read or a batch was not accepted.
        """
        cursor = drain.cursors.get(JOURNAL_KEY)
        if cursor is None:
            # First contact: from now on. What happened before was never promised.
            latest = self._journal("-n", "1")
            if latest and latest[-1].get("__CURSOR"):
                log_drain_store.record_delivery(
                    drain.name, {JOURNAL_KEY: str(latest[-1]["__CURSOR"])}, 0, store=self.store
                )
            return 0
        accepted = 0
        for _ in range(JOURNAL_READS):
            entries = self._journal(f"--after-cursor={cursor}", "-n", str(JOURNAL_ENTRIES))
            if not entries:
                break
            events = list(self._events(drain, index, entries))
            accepted += self._deliver(drain, transport, events)
            cursor = str(entries[-1].get("__CURSOR") or cursor)
            log_drain_store.record_delivery(
                drain.name, {JOURNAL_KEY: cursor}, len(events), store=self.store
            )
            if len(entries) < JOURNAL_ENTRIES:
                break
        return accepted

    def _events(
        self, drain: Drain, index: AppIndex, entries: list[dict[str, Any]]
    ) -> Iterator[LogEvent]:
        """
        Turn journal entries into the events a destination receives.

        Args:
            drain: The destination, whose applications narrow the entries.
            index: The map from units to applications.
            entries: Decoded journal entries.

        Yields:
            One event for each entry of an application the destination receives.
        """
        wanted = set(drain.apps)
        for entry in entries:
            found = index.lookup(entry.get("_SYSTEMD_UNIT"))
            if found is None:
                continue
            domain, process = found
            if wanted and domain not in wanted:
                continue
            text = _message(entry)
            if text is None:
                continue
            try:
                micros = int(entry.get("__REALTIME_TIMESTAMP", 0))
            except (TypeError, ValueError):
                micros = int(time.time() * 1_000_000)
            yield LogEvent(
                ts_ns=micros * 1000,
                message=self._scrub(domain, text),
                app=domain,
                process=process,
                unit=str(entry.get("_SYSTEMD_UNIT") or ""),
                priority=_priority(entry),
                host=self.host,
            )

    # -- A stack's containers -----------------------------------------------------------------

    def _ship_stack(self, drain: Drain, app: App, transport: Transport) -> int:
        """
        Send the lines a stack's containers wrote after a destination's last.

        The containers are the ones the terminal finds (rule 3: one answer to
        "which containers does this stack have"), each read with ``docker logs``
        up to the moment the cycle began, so a line one container wrote while
        another was read is never behind the cursor.

        Args:
            drain: The destination.
            app: A Compose or container application.
            transport: Its transport.

        Returns:
            The lines accepted.

        Raises:
            DeliveryError: A batch was not accepted.
        """
        key = f"compose:{app.domain}"
        last = drain.cursors.get(key)
        until = time.time_ns()
        if last is None:
            log_drain_store.record_delivery(drain.name, {key: str(until)}, 0, store=self.store)
            return 0
        try:
            containers = find_stack_containers(app, self.runner)
        except NoustError as exc:
            # Docker is not there or does not answer: that is the stack's state,
            # not the destination's, and it says nothing to ship.
            logger.warning("The containers of %s could not be listed: %s", app.domain, exc.message)
            return 0
        events: list[LogEvent] = []
        for container in containers:
            events.extend(self._container_events(drain, app, container, int(last), until))
        events.sort(key=lambda event: event.ts_ns)
        accepted = self._deliver(drain, transport, events)
        if events:
            log_drain_store.record_delivery(
                drain.name, {key: str(events[-1].ts_ns)}, len(events), store=self.store
            )
        return accepted

    def _container_events(
        self, drain: Drain, app: App, container: StackContainer, last: int, until: int
    ) -> list[LogEvent]:
        """
        Read what one container wrote in a stretch.

        Args:
            drain: The destination, whose applications are already narrowed.
            app: The stack's application.
            container: One of its containers.
            last: Nanoseconds of the last line shipped; later lines only.
            until: Nanoseconds the stretch ends at.

        Returns:
            The container's lines in it, scrubbed; none when Docker would not
            read its logs (it was removed since it was listed).
        """
        result = self.runner.run(
            [
                "docker",
                "logs",
                "--timestamps",
                "--since",
                _rfc3339(last),
                "--until",
                _rfc3339(until),
                container.id,
            ],
            timeout=COMPOSE_TIMEOUT,
        )
        if not result.success:
            return []
        events = []
        # A container writes its two streams apart; both are the application's.
        for raw in (*result.stdout.splitlines(), *result.stderr.splitlines()):
            matched = _CONTAINER_LINE.fullmatch(raw)
            if matched is None:
                continue
            stamp = _ns_of_rfc3339(matched["ts"])
            if stamp is None or stamp <= last:
                continue
            events.append(
                LogEvent(
                    ts_ns=stamp,
                    message=self._scrub(app.domain, matched["text"]),
                    app=app.domain,
                    process=container.service,
                    unit=container.id,
                    priority=6,
                    host=self.host,
                )
            )
        return events


# -- The unit -------------------------------------------------------------------------------

#: The unit that runs :meth:`LogShipper.run` for the whole server.
LOGSHIP_UNIT = "noust-logship"


def ensure_service(*, services: Any | None = None) -> str:
    """
    Install the shipper's unit and start it, when a server has systemd.

    Args:
        services: The service manager; built on first use by default.

    Returns:
        ``started`` (installed or already running), or ``no_systemd`` on a
        machine without it (a container, a chroot), where ``noust logdrain run``
        is run by whoever supervises the process.

    Raises:
        ServiceError: The unit could not be written or started.
    """
    from pathlib import Path

    from noust.core.utils import find_noust_executable
    from noust.managers.service_manager import ServiceManager

    if not Path("/run/systemd/system").is_dir():
        return "no_systemd"
    manager = services or ServiceManager()
    noust = find_noust_executable() or "/usr/bin/noust"
    manager.install_unit(LOGSHIP_UNIT, "logship", {"exec_start": f"{noust} logdrain run"})
    manager.enable(LOGSHIP_UNIT)
    manager.start(LOGSHIP_UNIT)
    return "started"


def remove_service(*, services: Any | None = None) -> bool:
    """
    Stop the shipper and remove its unit.

    Args:
        services: The service manager; built on first use by default.

    Returns:
        True when a unit was removed.
    """
    from noust.managers.service_manager import ServiceManager

    manager = services or ServiceManager()
    info = manager.inspect_unit(LOGSHIP_UNIT, serving=False)
    if not info.exists:
        return False
    manager.delete_service(LOGSHIP_UNIT)
    return True


def service_state(*, runner: CommandRunner | None = None) -> dict[str, Any]:
    """
    Say whether the shipper's unit is installed and running.

    Args:
        runner: Asked ``systemctl show``; the process-wide one by default.

    Returns:
        ``installed`` and ``active`` flags and systemd's ``state`` word.
    """
    result = (runner or get_runner()).run(
        ["systemctl", "show", f"{LOGSHIP_UNIT}.service", "--property=LoadState,ActiveState"],
        timeout=30,
    )
    values = dict(line.partition("=")[::2] for line in result.stdout.splitlines() if "=" in line)
    loaded = values.get("LoadState", "not-found") == "loaded"
    state = values.get("ActiveState", "inactive")
    return {"installed": loaded, "active": loaded and state == "active", "state": state}


__all__ = [
    "AppIndex",
    "CycleReport",
    "LogShipper",
    "ensure_service",
    "remove_service",
    "service_state",
]
