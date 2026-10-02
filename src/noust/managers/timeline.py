# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Everything Noust knows about a stretch of time, in one ordered timeline.

The owner saw a CPU peak five hours ago, selected it on the chart and wanted to
know what happened. The answer was spread over five places that each had their
own page: the system journal, the audit trail, the deployments, the jobs and
what the monitor noticed. This reads them all for one ``[start, end]`` and
merges them by time, with the processes that used the machine most in each
minute (:mod:`noust.monitor.process_samples`) beside them:

=================  ===================================================  ===================
source             what                                                 permission (API)
=================  ===================================================  ===================
``journal``        the journal of every unit, warnings and worse         ``secrets.reveal``
``audit``          CLI commands, console and central actions, sign-ins  ``audit.read``
``deployments``    deployments that ran in the stretch                  ``apps.read``
``jobs``           background jobs (deploys, backups, cron runs...)     ``apps.read``
``monitor``        processes over a threshold, unit failures and         ``server.read``
                   recoveries, server boots
``processes``      the top five by CPU and by memory, minute by minute  ``server.read``
=================  ===================================================  ===================

**Each source keeps the permission it already has.** This module does not know
about permissions: the caller passes the sources it may not read and the
permission each one lacks, and they come back marked *withheld* with it, never
silently dropped. The same for command lines, which only a caller allowed to
read them gets.

**For an application** the stretch is narrowed to it: the journal of its units
(every priority: its own output is mostly information), the audit events that
name it, its deployments and jobs, the failures of its units, the monitor's
findings about its processes, and its processes.

**Bounded.** Every source has a ceiling and says when it reached it; the process
minutes keep the busiest ones when there are more than fit. A source that cannot
be read says why, with the system's own words, and the rest are still answered.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NoustError, ValidationError

log = logging.getLogger(__name__)

SOURCE_JOURNAL = "journal"
SOURCE_AUDIT = "audit"
SOURCE_DEPLOYMENTS = "deployments"
SOURCE_JOBS = "jobs"
SOURCE_MONITOR = "monitor"
SOURCE_PROCESSES = "processes"

#: Every source, in the order the console lists them.
SOURCES: tuple[str, ...] = (
    SOURCE_JOURNAL,
    SOURCE_AUDIT,
    SOURCE_DEPLOYMENTS,
    SOURCE_JOBS,
    SOURCE_MONITOR,
    SOURCE_PROCESSES,
)

#: What a source's state is in the answer.
STATE_SHOWN = "shown"
STATE_WITHHELD = "withheld"
STATE_FAILED = "failed"
STATE_SKIPPED = "skipped"

#: Widest stretch one timeline covers: a month, the widest chart window that
#: still has minutes worth reading.
MAX_RANGE_SECONDS = 31 * 86_400

#: Most entries each source contributes.
MAX_JOURNAL = 500
MAX_AUDIT = 300
MAX_DEPLOYMENTS = 100
MAX_JOBS = 200
MAX_MONITOR = 300

#: How many rows of the store's histories are read to find the stretch in them.
HISTORY_SCAN = 500

#: Most minutes of process samples in one answer; beyond, the busiest are kept.
MAX_PROCESS_MINUTES = 180

#: Journal priorities at and above which an entry is a warning (``warning`` is 4).
WARNING_PRIORITY = 4

#: Audit events that say nothing about what happened to the server: a console
#: reading its own stream, a credential checked on every call, the log's own
#: bookkeeping.
AUDIT_NOISE = frozenset(
    {
        "auth.ws_ticket",
        "ws.connect",
        "auth.credential",
        "cli.command.start",
        "audit.checkpoint",
        "audit.chain_start",
        "audit.coalesced",
    }
)

#: Audit categories a timeline shows: who signed in and what was changed or
#: refused. Reads and the host ledger (every command a change ran) are left to
#: the audit page.
AUDIT_CATEGORIES = ("access", "account", "config", "change", "denial", "system")

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"
LEVEL_NOTICE = "notice"
LEVEL_INFO = "info"


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimelineEvent:
    """
    One thing that happened, from any source.

    Attributes:
        at: When, epoch seconds (fractional for the journal).
        source: One of :data:`SOURCES`.
        kind: ``journal``, ``audit``, ``deployment``, ``job``,
            ``observation``, ``unit_failed``, ``unit_recovered`` or ``boot``.
        level: ``error``, ``warning``, ``notice`` or ``info``.
        text: What the source itself says, verbatim: the journal's message,
            the audit event's name, the monitor's sentence, the job's name.
        unit: The unit it concerns, when there is one.
        app: The application it concerns, by domain, when there is one.
        actor: Who did it, as the source names them.
        status: The outcome: a deployment's or job's status, an audit result.
        ref: What to open: a deployment's id, a job's id.
        priority: The journal's priority, 0 to 7.
        details: Small extras the console shows beside it.
    """

    at: float
    source: str
    kind: str
    level: str
    text: str
    unit: str | None = None
    app: str | None = None
    actor: str | None = None
    status: str | None = None
    ref: str | None = None
    priority: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the event as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass(frozen=True)
class SourceStatus:
    """
    What became of one source.

    Attributes:
        source: One of :data:`SOURCES`.
        state: ``shown``, ``withheld`` (the caller may not read it),
            ``failed`` (it could not be read) or ``skipped`` (not asked for).
        count: How many entries it contributed.
        truncated: It had more than its ceiling; the newest were kept.
        permission: The permission it needs, when withheld.
        message: Why it failed, in Noust's words.
        evidence: What the system said, verbatim.
    """

    source: str
    state: str
    count: int = 0
    truncated: bool = False
    permission: str | None = None
    message: str | None = None
    evidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Render the status as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass(frozen=True)
class ProcessMinute:
    """
    The processes of one minute.

    Attributes:
        at: The minute, epoch seconds.
        cpu: The top processes by CPU, the busiest first.
        memory: The top processes by memory, the biggest first.
    """

    at: int
    cpu: list[dict[str, Any]]
    memory: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        """
        Render the minute as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


@dataclass(frozen=True)
class ProcessHistory:
    """
    The process samples of a stretch.

    Attributes:
        minutes: The minutes kept, oldest first.
        total_minutes: How many minutes had samples; more than ``minutes``
            when only the busiest were kept.
        since: The oldest sample there is at all, epoch seconds; None when
            processes have never been sampled. Before it there is nothing.
        commands: Whether command lines are included.
    """

    minutes: list[ProcessMinute] = field(default_factory=list)
    total_minutes: int = 0
    since: int | None = None
    commands: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Render the history as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return {
            "minutes": [minute.to_dict() for minute in self.minutes],
            "total_minutes": self.total_minutes,
            "since": self.since,
            "commands": self.commands,
        }


@dataclass(frozen=True)
class Timeline:
    """
    The timeline of a stretch.

    Attributes:
        start: First moment, epoch seconds.
        end: Last moment.
        app: The application it is narrowed to, or None for the server.
        events: Every event, oldest first.
        processes: The process samples, or None when that source was not shown.
        sources: What became of each source, in :data:`SOURCES` order.
    """

    start: int
    end: int
    app: str | None
    events: list[TimelineEvent]
    processes: ProcessHistory | None
    sources: list[SourceStatus]

    def to_dict(self) -> dict[str, Any]:
        """
        Render the timeline as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return {
            "start": self.start,
            "end": self.end,
            "app": self.app,
            "events": [event.to_dict() for event in self.events],
            "processes": self.processes.to_dict() if self.processes is not None else None,
            "sources": [status.to_dict() for status in self.sources],
        }


class _Unreadable(Exception):
    """A source could not be read; carries what to say and what the system said."""

    def __init__(self, message: str, evidence: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.evidence = evidence


#: What reading a source can raise and still let the others be answered.
SOURCE_ERRORS: tuple[type[Exception], ...] = (NoustError, OSError, sqlite3.Error, ValueError)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def epoch_of(value: str | None) -> float | None:
    """
    Read a stored timestamp as epoch seconds.

    Args:
        value: ISO 8601, naive (this machine's local time, as the store writes
            it) or with an offset.

    Returns:
        Seconds, or None when it is not a timestamp.
    """
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.timestamp()


def _utc_iso(seconds: float) -> str:
    """The audit log's own timestamp form, in UTC."""
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _local_iso(seconds: float) -> str:
    """The observation store's own timestamp form: naive local time."""
    return datetime.fromtimestamp(seconds).isoformat()


def parse_boot_time(text: str) -> float | None:
    """
    Read a moment as ``journalctl --list-boots`` prints it.

    Args:
        text: ``Thu 2026-10-01 10:00:00 UTC``; the zone is UTC or the
            machine's own, which is how journalctl prints it.

    Returns:
        Epoch seconds, or None when it is not that shape.
    """
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        moment = datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    zone = parts[3] if len(parts) > 3 else ""
    if zone in ("UTC", "GMT", "Z"):
        return moment.replace(tzinfo=timezone.utc).timestamp()
    return moment.astimezone().timestamp()


def check_range(start: int, end: int, now: float) -> None:
    """
    Refuse a stretch that cannot be answered.

    Args:
        start: First moment, epoch seconds.
        end: Last moment.
        now: The current time.

    Raises:
        ValidationError: The stretch is empty, inverted, wider than
            :data:`MAX_RANGE_SECONDS` or in the future.
    """
    if end <= start:
        raise ValidationError(
            "The end of the stretch must come after its start",
            f"Got start={start} and end={end}.",
            field="end",
        )
    if end - start > MAX_RANGE_SECONDS:
        raise ValidationError(
            "That stretch is longer than a timeline covers",
            f"Pick at most {MAX_RANGE_SECONDS // 86_400} days.",
            field="start",
        )
    if start > now + 60:
        raise ValidationError(
            "That stretch has not happened yet", "Pick a stretch in the past.", field="start"
        )


# ---------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------


@dataclass
class TimelineReaders:
    """
    Where each source is read from; every one replaceable in tests.

    Attributes:
        journal: A :class:`~noust.managers.server.journal.JournalReader`.
        audit: The audit log (:func:`noust.core.audit.get_log`), or None when
            auditing is off.
        store: The Noust store: applications, deployments, jobs.
        metrics: The metrics database: process samples and unit events.
        observations: The monitor's observation store.
        units_of: Names the units of an application.
        clock: The current time.
    """

    journal: Any
    audit: Any
    store: Any
    metrics: Any
    observations: Any
    units_of: Callable[[Any], list[str]]
    clock: Callable[[], float]


class TimelineBuilder:
    """Reads every source for a stretch and merges what it may show."""

    def __init__(self, readers: TimelineReaders) -> None:
        """
        Args:
            readers: Where each source is read from.
        """
        self.readers = readers

    def build(
        self,
        start: int,
        end: int,
        *,
        app: str | None = None,
        sources: Collection[str] | None = None,
        withheld: Mapping[str, str] | None = None,
        show_commands: bool = False,
    ) -> Timeline:
        """
        Build the timeline of a stretch.

        Args:
            start: First moment, epoch seconds.
            end: Last moment.
            app: Narrow it to this application, by domain.
            sources: Only these sources; every one when None.
            withheld: Sources the caller may not read, each with the
                permission it lacks.
            show_commands: Include command lines.

        Returns:
            The timeline, events oldest first.

        Raises:
            ValidationError: The stretch, a source or the application is not
                valid.
        """
        check_range(start, end, self.readers.clock())
        wanted = set(SOURCES if sources is None else sources)
        unknown = wanted - set(SOURCES)
        if unknown:
            raise ValidationError(
                f"Not a timeline source: {', '.join(sorted(unknown))}",
                f"Use any of {', '.join(SOURCES)}.",
                field="sources",
            )
        app_row = None
        if app is not None:
            app_row = self.readers.store.get_app(app)
            if app_row is None:
                raise ValidationError(
                    f"No application {app}",
                    "Use the domain of a deployed application.",
                    field="app",
                )
        hidden = dict(withheld or {})

        readers: dict[str, Callable[[], tuple[list[TimelineEvent], bool]]] = {
            SOURCE_JOURNAL: lambda: self._journal(start, end, app_row),
            SOURCE_AUDIT: lambda: self._audit(start, end, app),
            SOURCE_DEPLOYMENTS: lambda: self._deployments(start, end, app),
            SOURCE_JOBS: lambda: self._jobs(start, end, app),
            SOURCE_MONITOR: lambda: self._monitor(start, end, app_row, show_commands),
        }

        events: list[TimelineEvent] = []
        statuses: list[SourceStatus] = []
        processes: ProcessHistory | None = None
        for source in SOURCES:
            if source not in wanted:
                statuses.append(SourceStatus(source, STATE_SKIPPED))
                continue
            if source in hidden:
                statuses.append(SourceStatus(source, STATE_WITHHELD, permission=hidden[source]))
                continue
            try:
                if source == SOURCE_PROCESSES:
                    processes = self._processes(start, end, app, show_commands)
                    statuses.append(
                        SourceStatus(
                            source,
                            STATE_SHOWN,
                            count=len(processes.minutes),
                            truncated=processes.total_minutes > len(processes.minutes),
                        )
                    )
                    continue
                found, truncated = readers[source]()
            except _Unreadable as exc:
                statuses.append(
                    SourceStatus(source, STATE_FAILED, message=exc.message, evidence=exc.evidence)
                )
                continue
            except SOURCE_ERRORS as exc:
                log.warning("timeline source %s could not be read", source, exc_info=True)
                statuses.append(
                    SourceStatus(
                        source,
                        STATE_FAILED,
                        message=getattr(exc, "message", None) or str(exc),
                        evidence=getattr(exc, "output", None),
                    )
                )
                continue
            events.extend(found)
            statuses.append(
                SourceStatus(source, STATE_SHOWN, count=len(found), truncated=truncated)
            )

        events.sort(key=lambda event: (event.at, SOURCES.index(event.source)))
        return Timeline(
            start=start,
            end=end,
            app=app,
            events=events,
            processes=processes,
            sources=statuses,
        )

    # ------------------------------------------------------------- journal

    def _journal(self, start: int, end: int, app: Any | None) -> tuple[list[TimelineEvent], bool]:
        """
        Read the journal of the stretch.

        Args:
            start: First moment.
            end: Last moment.
            app: The application row to narrow to, or None.

        Returns:
            The entries and whether there were more than :data:`MAX_JOURNAL`.
        """
        window = {"since": f"@{int(start)}", "until": f"@{int(end)}", "lines": MAX_JOURNAL}
        pages = []
        if app is None:
            pages.append(self.readers.journal.read(priority=WARNING_PRIORITY, **window))
        else:
            for unit in self.readers.units_of(app):
                pages.append(self.readers.journal.read(unit=unit, **window))
        entries = sorted(
            (entry for page in pages for entry in page.entries), key=lambda entry: entry.timestamp
        )
        truncated = any(page.truncated for page in pages) or len(entries) > MAX_JOURNAL
        entries = entries[-MAX_JOURNAL:]
        domain = app.domain if app is not None else None
        events = []
        for entry in entries:
            at = epoch_of(entry.timestamp)
            if at is None:
                continue
            events.append(
                TimelineEvent(
                    at=at,
                    source=SOURCE_JOURNAL,
                    kind="journal",
                    level=_journal_level(entry.priority),
                    text=entry.message,
                    unit=entry.unit or None,
                    app=domain,
                    priority=entry.priority,
                    details={"pid": entry.pid} if entry.pid is not None else {},
                )
            )
        return events, truncated

    # --------------------------------------------------------------- audit

    def _audit(self, start: int, end: int, app: str | None) -> tuple[list[TimelineEvent], bool]:
        """
        Read the audit trail of the stretch.

        Args:
            start: First moment.
            end: Last moment.
            app: Only events that name this application.

        Returns:
            The events and whether there were more than :data:`MAX_AUDIT`.
        """
        audit = self.readers.audit
        if audit is None or not getattr(audit, "enabled", True):
            raise _Unreadable("Auditing is turned off on this server, so there is no trail.")
        matched: list[dict[str, Any]] = []
        truncated = False
        since, before = _utc_iso(start), _utc_iso(end + 1)
        for entry in _audit_entries(audit, since, before):
            if str(entry.get("action", "")) in AUDIT_NOISE:
                continue
            if app is not None and not _names_app(entry, app):
                continue
            if len(matched) >= MAX_AUDIT:
                truncated = True
                break
            matched.append(entry)
        events = []
        for entry in matched:
            at = epoch_of(str(entry.get("ts", "")))
            if at is None:
                continue
            details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
            events.append(
                TimelineEvent(
                    at=at,
                    source=SOURCE_AUDIT,
                    kind="audit",
                    level=_severity_level(entry.get("sev")),
                    text=str(entry.get("action", "")),
                    app=app,
                    actor=str(entry.get("actor") or "") or None,
                    status=str(entry.get("result") or "") or None,
                    ref=str(entry.get("corr") or "") or None,
                    details={
                        key: value
                        for key, value in (
                            ("target", entry.get("resource")),
                            ("detail", entry.get("detail")),
                            ("ip", entry.get("ip")),
                            ("category", entry.get("cat")),
                            ("details", details or None),
                        )
                        if value
                    },
                )
            )
        return events, truncated

    # --------------------------------------------------------- deployments

    def _deployments(
        self, start: int, end: int, app: str | None
    ) -> tuple[list[TimelineEvent], bool]:
        """
        Read the deployments that ran in the stretch.

        A deployment that started before the stretch and was still running in
        it is part of it.

        Args:
            start: First moment.
            end: Last moment.
            app: Only this application's.

        Returns:
            The deployments and whether the history read did not reach back to
            the start of the stretch.
        """
        rows = self.readers.store.list_deployments(domain=app, limit=HISTORY_SCAN)
        reached = _history_reaches(rows, start, lambda row: row.started_at)
        events = []
        now = self.readers.clock()
        for row in rows:
            began = epoch_of(row.started_at)
            if began is None:
                continue
            ended = epoch_of(row.finished_at) or now
            if began > end or ended < start:
                continue
            events.append(
                TimelineEvent(
                    at=began,
                    source=SOURCE_DEPLOYMENTS,
                    kind="deployment",
                    level=_status_level(row.status),
                    text=row.commit_message or row.git_commit or "",
                    app=row.domain,
                    actor=row.triggered_by,
                    status=row.status,
                    ref=str(row.id) if row.id is not None else None,
                    details={
                        key: value
                        for key, value in (
                            ("finished_at", epoch_of(row.finished_at)),
                            ("duration_s", row.duration_s),
                            ("commit", row.git_commit),
                            ("branch", row.git_branch),
                            ("release", row.release_id),
                            ("error", row.error),
                            ("job", row.job_id),
                        )
                        if value is not None
                    },
                )
            )
        events.sort(key=lambda event: event.at, reverse=True)
        truncated = len(events) > MAX_DEPLOYMENTS or not reached
        return events[:MAX_DEPLOYMENTS], truncated

    # ---------------------------------------------------------------- jobs

    def _jobs(self, start: int, end: int, app: str | None) -> tuple[list[TimelineEvent], bool]:
        """
        Read the background jobs that ran in the stretch.

        Args:
            start: First moment.
            end: Last moment.
            app: Only this application's.

        Returns:
            The jobs and whether the history read did not reach back to the
            start of the stretch.
        """
        rows = self.readers.store.list_jobs(limit=HISTORY_SCAN, domain=app)
        reached = _history_reaches(rows, start, lambda row: row.created_at)
        events = []
        now = self.readers.clock()
        for row in rows:
            began = epoch_of(row.started_at) or epoch_of(row.created_at)
            if began is None:
                continue
            ended = epoch_of(row.finished_at) or now
            if began > end or ended < start:
                continue
            events.append(
                TimelineEvent(
                    at=began,
                    source=SOURCE_JOBS,
                    kind="job",
                    level=_status_level(row.status),
                    text=row.name,
                    app=row.domain,
                    actor=row.actor,
                    status=row.status,
                    ref=row.id,
                    details={
                        key: value
                        for key, value in (
                            ("type", row.type),
                            ("finished_at", epoch_of(row.finished_at)),
                            ("error", row.error),
                        )
                        if value
                    },
                )
            )
        events.sort(key=lambda event: event.at, reverse=True)
        truncated = len(events) > MAX_JOBS or not reached
        return events[:MAX_JOBS], truncated

    # ------------------------------------------------------------- monitor

    def _monitor(
        self, start: int, end: int, app: Any | None, show_commands: bool
    ) -> tuple[list[TimelineEvent], bool]:
        """
        Read what the monitor saw: findings, unit failures and recoveries, boots.

        Args:
            start: First moment.
            end: Last moment.
            app: The application row to narrow to, or None.
            show_commands: Include the command lines of the findings.

        Returns:
            The events and whether there were more than :data:`MAX_MONITOR`.
        """
        domain = app.domain if app is not None else None
        units = set(self.readers.units_of(app)) if app is not None else None
        app_pids = (
            {
                sample.pid
                for sample in self.readers.metrics.process_samples(start - 60, end, app=domain)
            }
            if domain is not None
            else None
        )
        events: list[TimelineEvent] = []

        rows = self.readers.observations.between(
            _local_iso(start), _local_iso(end + 1), limit=MAX_MONITOR + 1
        )
        for row in rows:
            at = epoch_of(row.get("observed_at"))
            if at is None or (app_pids is not None and row.get("pid") not in app_pids):
                continue
            events.append(
                TimelineEvent(
                    at=at,
                    source=SOURCE_MONITOR,
                    kind="observation",
                    level=LEVEL_WARNING if row.get("severity") == "warning" else LEVEL_NOTICE,
                    text=str(row.get("detail") or ""),
                    app=domain,
                    details={
                        key: value
                        for key, value in (
                            ("pid", row.get("pid")),
                            ("process", row.get("process_name")),
                            ("user", row.get("user")),
                            ("signal", row.get("signal")),
                            ("cpu_percent", row.get("cpu_percent")),
                            ("memory_percent", row.get("memory_percent")),
                            ("command", row.get("command") if show_commands else None),
                        )
                        if value is not None
                    },
                )
            )

        for event in self.readers.metrics.monitor_events(start, end, limit=MAX_MONITOR + 1):
            if units is not None and _bare_unit(event.subject) not in {
                _bare_unit(u) for u in units
            }:
                continue
            failed = event.kind == "unit_failed"
            events.append(
                TimelineEvent(
                    at=float(event.ts),
                    source=SOURCE_MONITOR,
                    kind=event.kind,
                    level=LEVEL_ERROR if failed else LEVEL_INFO,
                    text=event.detail or "",
                    unit=event.subject,
                    app=domain,
                    details={"reason": event.reason} if event.reason else {},
                )
            )

        for boot in self.readers.journal.boots():
            at = parse_boot_time(boot.first)
            if at is None or not start <= at <= end:
                continue
            events.append(
                TimelineEvent(
                    at=at,
                    source=SOURCE_MONITOR,
                    kind="boot",
                    level=LEVEL_NOTICE,
                    text=boot.first,
                    details={"boot_id": boot.boot_id},
                )
            )

        events.sort(key=lambda event: event.at)
        truncated = len(rows) > MAX_MONITOR or len(events) > MAX_MONITOR
        return events[-MAX_MONITOR:], truncated

    # ----------------------------------------------------------- processes

    def _processes(
        self, start: int, end: int, app: str | None, show_commands: bool
    ) -> ProcessHistory:
        """
        Read the process samples of the stretch.

        Args:
            start: First moment.
            end: Last moment.
            app: Only this application's processes.
            show_commands: Include command lines.

        Returns:
            The minutes, the busiest :data:`MAX_PROCESS_MINUTES` when there are
            more, and since when processes have been sampled at all.
        """
        metrics = self.readers.metrics
        # A minute is stored under its start: the one that began just before
        # the stretch overlaps it.
        samples = metrics.process_samples(start - 59, end, app=app)
        minutes: dict[int, dict[str, list[dict[str, Any]]]] = {}
        for sample in samples:
            row = sample.to_dict()
            row.pop("ts", None)
            row.pop("rank", None)
            if not show_commands:
                row["command"] = None
            minutes.setdefault(sample.ts, {"cpu": [], "memory": []})[sample.rank].append(row)
        ordered = [
            ProcessMinute(at=ts, cpu=ranks["cpu"], memory=ranks["memory"])
            for ts, ranks in sorted(minutes.items())
        ]
        kept = ordered
        if len(ordered) > MAX_PROCESS_MINUTES:
            busiest = sorted(ordered, key=_minute_load, reverse=True)[:MAX_PROCESS_MINUTES]
            kept = sorted(busiest, key=lambda minute: minute.at)
        return ProcessHistory(
            minutes=kept,
            total_minutes=len(ordered),
            since=metrics.first_process_sample_at(),
            commands=show_commands,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _audit_entries(audit: Any, since: str, before: str) -> Iterable[dict[str, Any]]:
    """
    Read the audit events of a stretch, newest first.

    The log's own read keeps a request's generic line out when the request
    recorded an event of its own, which is what a person wants to read.

    Args:
        audit: The audit log.
        since: The stretch's start, as the log writes timestamps.
        before: Just after its end.

    Returns:
        The events.
    """
    return list(
        audit.read(
            limit=MAX_AUDIT * 4,
            since=since,
            before=before,
            categories=AUDIT_CATEGORIES,
        )
    )


def _names_app(entry: Mapping[str, Any], domain: str) -> bool:
    """Whether an audit event is about an application: its target names it."""
    resource = str(entry.get("resource") or "")
    return domain in resource


def _bare_unit(name: str) -> str:
    """A unit's name without ``.service``, the way the store and the monitor write it."""
    return name.removesuffix(".service")


def _history_reaches(rows: Sequence[Any], start: int, stamp: Callable[[Any], str | None]) -> bool:
    """
    Say whether a newest-first history read reached back to a moment.

    Args:
        rows: The rows read, newest first.
        start: The moment.
        stamp: Reads a row's timestamp.

    Returns:
        True when fewer rows than asked for came back (the whole history was
        read) or the oldest one is from before the moment.
    """
    if len(rows) < HISTORY_SCAN or not rows:
        return True
    oldest = epoch_of(stamp(rows[-1]))
    return oldest is not None and oldest <= start


def _minute_load(minute: ProcessMinute) -> float:
    """How busy a minute was: the CPU of its busiest process."""
    return max((float(row.get("cpu_percent") or 0.0) for row in minute.cpu), default=0.0)


def _journal_level(priority: int) -> str:
    """The level of a journal priority."""
    if priority <= 3:
        return LEVEL_ERROR
    if priority == 4:
        return LEVEL_WARNING
    if priority == 5:
        return LEVEL_NOTICE
    return LEVEL_INFO


def _severity_level(severity: object) -> str:
    """The level of a syslog severity, as an audit event carries it."""
    return _journal_level(severity) if isinstance(severity, int) else LEVEL_INFO


def _status_level(status: str | None) -> str:
    """The level of a deployment's or a job's outcome."""
    if status in ("failed", "rolled_back", "interrupted"):
        return LEVEL_ERROR
    if status in ("running", "queued", "pending"):
        return LEVEL_NOTICE
    return LEVEL_INFO


def default_readers(metrics: Any, *, runner: Any = None) -> TimelineReaders:
    """
    Build the readers of a running Noust.

    Args:
        metrics: The metrics database of this process (the console's is
            :func:`noust.web.metrics_collector.get_metrics_store`).
        runner: The command runner for journalctl and systemctl; the
            process-wide one when None.

    Returns:
        Readers over the real journal, audit log, store, metrics database and
        observation store.
    """
    import time

    from noust.core.audit import get_log
    from noust.core.store import get_store
    from noust.managers.server.journal import JournalReader
    from noust.managers.service_manager import ServiceManager
    from noust.monitor.observation_store import ObservationStore

    store = get_store()
    services = ServiceManager(runner=runner) if runner is not None else ServiceManager()
    return TimelineReaders(
        journal=JournalReader(runner=runner),
        audit=get_log(),
        store=store,
        metrics=metrics,
        observations=ObservationStore(verbose=False),
        units_of=lambda app: list(services.app_units(app)),
        clock=time.time,
    )


__all__ = [
    "MAX_PROCESS_MINUTES",
    "MAX_RANGE_SECONDS",
    "SOURCES",
    "SOURCE_AUDIT",
    "SOURCE_DEPLOYMENTS",
    "SOURCE_JOBS",
    "SOURCE_JOURNAL",
    "SOURCE_MONITOR",
    "SOURCE_PROCESSES",
    "STATE_FAILED",
    "STATE_SHOWN",
    "STATE_SKIPPED",
    "STATE_WITHHELD",
    "ProcessHistory",
    "ProcessMinute",
    "SourceStatus",
    "Timeline",
    "TimelineBuilder",
    "TimelineEvent",
    "TimelineReaders",
    "check_range",
    "default_readers",
    "epoch_of",
    "parse_boot_time",
]
