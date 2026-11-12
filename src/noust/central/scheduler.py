# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central's own scheduler, for the machines that have no timers.

On a server every schedule of Noust (the backups of an application and of its volumes, the retention sweep)
is a row in the store and a systemd timer written from it, and the timer runs a ``noust`` command. The central
of a NAS has no systemd to keep timers, so the same rows are read here, by a thread of the central's own
process, which runs the same commands when their calendars say. There is **one list of tasks**, the rows;
what differs is the engine that realises it, a timer or this. A source of tasks is a function of the store,
and the schedules another part of Noust adds (the check of images, a cleanup) join the list by registering
theirs in :data:`SOURCES`.

Two things a timer does for free and this must do on purpose:

- **What was missed.** A timer is persistent: after a reboot it runs what came due while the machine was off.
  The central keeps when each task last ran (``scheduler.json`` in its state folder) and, when it is
  started again, runs what came due while it was not running, once however many runs that was (a central that
  was off for a month does not owe a month of backups), and **says it**: the log line names the task, how
  many runs were missed and when the last should have been.
- **A task that cannot be read.** A calendar this module does not understand (:mod:`noust.core.calendar_expr`
  reads systemd's language as far as Noust accepts it) is said and never run at a guess.

The tasks run one after another in the thread, each as a process of the same Python and the same ``noust``
(``python -m noust ...``), through the runner (rule 1) with a deadline, so a task is exactly what its timer's
service is, and its own notifications, audit records and exit code are its own. A failure is kept with its
last words and does not stop the others.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from noust.core import paths
from noust.core.calendar_expr import Calendar, CalendarError
from noust.core.exceptions import NoustError
from noust.core.fs import FileSystem, get_fs
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import NoustStore, get_store

__all__ = [
    "LATE_AFTER",
    "PROGRAM",
    "SOURCES",
    "Scheduler",
    "SchedulerThread",
    "Task",
    "TaskStatus",
    "default_scheduler",
    "listing",
    "rows",
    "start_if_needed",
    "state_of",
]

log = logging.getLogger(__name__)

#: The program every task runs as: the Noust of this very process, whichever way it was installed.
PROGRAM = (sys.executable, "-m", "noust")

#: How long a task may take. A copy of a large volume is long; a task that takes longer is stopped.
TASK_TIMEOUT = 6 * 3600

#: How far behind its time a run may be before it is called a missed one and not just a late tick.
LATE_AFTER = 300

#: What the file the scheduler remembers in says its layout is.
STATE_VERSION = 1

#: The longest a task's last words are kept.
_DETAIL = 400


@dataclass(frozen=True)
class Task:
    """
    One thing that runs at a time: a row of the store, as the engine runs it.

    Attributes:
        key: Its name in the scheduler's memory (``volume_backup:shop.example.com``).
        kind: What it is (``backup``, ``volume_backup``, ``sweep``, ``image_check``...), which the
            console says as a sentence of its own.
        subject: What it is for, usually an application's domain; empty for the machine's.
        unit: The timer's name on a server, so that what asks about a unit asks about this.
        calendar: When, as the row holds it (an alias of Noust's or a systemd calendar expression).
        argv: The ``noust`` command line, without the program.
    """

    key: str
    kind: str
    subject: str
    unit: str
    calendar: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class TaskStatus:
    """
    One task and where it stands.

    Attributes:
        task: The task.
        next_run: When it runs next; None when its calendar is not understood or has nothing left.
        last_run: When it last ran, if it did.
        last_status: ``ok`` or ``failed``; None before the first run.
        last_detail: Its last words when it failed.
        missed: How many runs the last one stood for beyond itself: more than zero means the central
            was not running when they came due.
        problem: Why it cannot be scheduled (a calendar that is not understood).
    """

    task: Task
    next_run: datetime | None
    last_run: datetime | None
    last_status: str | None
    last_detail: str
    missed: int
    problem: str | None


#: Where tasks come from: each a function of the store. A schedule another part adds registers here.
Source = Callable[[NoustStore], list[Task]]
SOURCES: dict[str, Source] = {}


def _volume_policies(store: NoustStore) -> list[Task]:
    """
    Args:
        store: The store.

    Returns:
        A task for each application whose data is backed up on a schedule.
    """
    from noust.managers.backup_scheduler import BackupScheduler
    from noust.managers.volume_backup_records import VolumeRecords

    return [
        Task(
            key=f"volume_backup:{policy.app_domain}",
            kind="volume_backup",
            subject=policy.app_domain,
            unit=BackupScheduler.volume_timer_name(policy.app_domain),
            calendar=policy.schedule,
            argv=(
                "backup",
                "volumes",
                "run",
                policy.app_domain,
                "--reason",
                "Scheduled volume backup policy",
            ),
        )
        for policy in VolumeRecords(store).policies()
        if policy.enabled
    ]


def _application_backups(store: NoustStore) -> list[Task]:
    """
    Args:
        store: The store.

    Returns:
        A task for each application that is backed up on a schedule (a Compose stack's databases are
        dumped in it).
    """
    from noust.core import paths as noust_paths
    from noust.core.utils import domain_to_app_name

    return [
        Task(
            key=f"backup:{record.app_domain}",
            kind="backup",
            subject=record.app_domain,
            unit=f"{noust_paths.BACKUP_UNIT_PREFIX}{domain_to_app_name(record.app_domain)}",
            calendar=record.schedule,
            argv=("backup", "run-schedule", record.app_domain),
        )
        for record in store.list_backup_schedules()
    ]


def _sweep(store: NoustStore) -> list[Task]:
    """
    Args:
        store: The store.

    Returns:
        The retention sweep, when there is any backup schedule for it to be the backstop of: every
        policy brings it along on a server, and so it does here.
    """
    from noust.managers.backup_scheduler import SWEEP_CALENDAR, SWEEP_UNIT
    from noust.managers.volume_backup_records import VolumeRecords

    if not store.list_backup_schedules() and not any(
        policy.enabled for policy in VolumeRecords(store).policies()
    ):
        return []
    return [
        Task(
            key="sweep",
            kind="sweep",
            subject="",
            unit=SWEEP_UNIT,
            calendar=SWEEP_CALENDAR,
            argv=("backup", "sweep", "--reason", "Scheduled retention sweep"),
        )
    ]


SOURCES.update(
    {
        "application_backups": _application_backups,
        "volume_backups": _volume_policies,
        "sweep": _sweep,
    }
)


def _aliases() -> dict[str, str]:
    """
    Returns:
        The names Noust gives a schedule, which mean what Noust writes for them.
    """
    from noust.managers.backup_scheduler import SCHEDULE_ALIASES

    return dict(SCHEDULE_ALIASES)


class Scheduler:
    """
    Run the schedules of the store when their time comes, and what was missed when the central comes back.

    Args:
        runner: Runs each task; the process-wide one by default.
        state_path: Where it remembers when each task ran.
        say: Where it says what it does and what it found (the log, and the console's feed).
        clock: The time, in local time; replaced in a test.
        store: The store the tasks are rows of.
        fs: The filesystem the memory is written through.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        state_path: Path | None = None,
        say: Callable[[str], None] | None = None,
        clock: Callable[[], datetime] = datetime.now,
        store: NoustStore | None = None,
        fs: FileSystem | None = None,
    ) -> None:
        self._runner = runner
        self._state_path = state_path or paths.state_dir() / "scheduler.json"
        self._say = say or log.info
        self._clock = clock
        self._store = store
        self._fs = fs
        self._lock = threading.Lock()

    @property
    def runner(self) -> CommandRunner:
        """The runner the tasks go through."""
        return self._runner or get_runner()

    @property
    def store(self) -> NoustStore:
        """The store the tasks are read from."""
        return self._store or get_store()

    @property
    def fs(self) -> FileSystem:
        """The filesystem the memory is written through."""
        return self._fs or get_fs()

    # -- What is scheduled ----------------------------------------------------

    def tasks(self) -> list[Task]:
        """
        Returns:
            Every task, from every source, by key. A source that cannot be read is said and leaves the
            others as they were.
        """
        found: dict[str, Task] = {}
        for name, source in SOURCES.items():
            try:
                for task in source(self.store):
                    found[task.key] = task
            except (NoustError, sqlite3.Error, OSError) as exc:
                self._say(f"The schedules of {name} could not be read, so none of them ran: {exc}")
        return sorted(found.values(), key=lambda task: task.key)

    # -- What it remembers ----------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        """
        Returns:
            What the file says about each task; empty when there is none or it cannot be read (said:
            the memory starts over, and no task is owed the days before that).
        """
        try:
            document = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            self._say(f"{self._state_path.name} could not be read ({exc}); it is started over")
            return {}
        tasks = document.get("tasks") if isinstance(document, dict) else None
        return tasks if isinstance(tasks, dict) else {}

    def _save(self, tasks: dict[str, dict[str, Any]]) -> None:
        """
        Args:
            tasks: What to remember.
        """
        document = json.dumps({"version": STATE_VERSION, "tasks": tasks}, indent=2, sort_keys=True)
        self.fs.write_text(self._state_path, document + "\n", mode=0o600)

    @staticmethod
    def _moment(text: object) -> datetime | None:
        """
        Args:
            text: An ISO time as remembered.

        Returns:
            The moment, or None when it is not one.
        """
        try:
            return datetime.fromisoformat(str(text))
        except ValueError:
            return None

    # -- Looking and running --------------------------------------------------

    def tick(self) -> None:
        """
        Run what has come due, once each.

        The first look after the central starts is the one that finds what was missed. Only one tick runs
        at a time; a tick that finds another running leaves it be.
        """
        if not self._lock.acquire(blocking=False):
            return
        try:
            now = self._clock()
            remembered = self._load()
            current = {task.key: task for task in self.tasks()}
            for key in set(remembered) - set(current):
                del remembered[key]
            for key, task in current.items():
                entry = remembered.setdefault(key, {})
                if "since" not in entry:
                    entry["since"] = now.isoformat(timespec="seconds")
                    continue
                self._run_if_due(task, entry, now)
            self._save(remembered)
        finally:
            self._lock.release()

    def _run_if_due(self, task: Task, entry: dict[str, Any], now: datetime) -> None:
        """
        Run a task when a run of it has come due since it last ran, and remember it.

        Args:
            task: The task.
            entry: What is remembered of it; updated.
            now: The time.
        """
        try:
            calendar = Calendar.parse(task.calendar, aliases=_aliases())
        except CalendarError as exc:
            if entry.get("problem") != exc.message:
                self._say(f"{self._title(task)} is not run: {exc.message}")
            entry["problem"] = exc.message
            return
        entry.pop("problem", None)
        base = self._moment(entry.get("last_run") or entry["since"])
        due = calendar.between(base, now) if base else []
        if not due:
            return
        late = (now - due[-1]).total_seconds() > LATE_AFTER
        if late:
            when = f"{due[-1]:%Y-%m-%d %H:%M}"
            self._say(
                f"{self._title(task)} was due at {when} while the central was not running: it runs now"
                if len(due) == 1
                else f"{self._title(task)} missed {len(due)} runs while the central was not running, "
                f"the last at {when}: it runs now, once"
            )
        done = self.runner.run([*PROGRAM, *task.argv], timeout=TASK_TIMEOUT)
        entry["last_run"] = now.isoformat(timespec="seconds")
        entry["last_status"] = "ok" if done.success else "failed"
        entry["missed"] = len(due) - 1 if late else 0
        tail = (done.stderr or done.stdout).strip().splitlines()
        entry["last_detail"] = "" if done.success else (tail[-1][:_DETAIL] if tail else "no output")
        if not done.success:
            self._say(f"{self._title(task)} failed: {entry['last_detail']}")

    @staticmethod
    def _title(task: Task) -> str:
        """
        Args:
            task: A task.

        Returns:
            What to call it in a line of the log.
        """
        return f"The {task.kind.replace('_', ' ')} schedule" + (
            f" of {task.subject}" if task.subject else ""
        )

    def status(self) -> list[TaskStatus]:
        """
        Returns:
            Every task with when it runs next and how its last run went.
        """
        now = self._clock()
        remembered = self._load()
        statuses: list[TaskStatus] = []
        for task in self.tasks():
            entry = remembered.get(task.key, {})
            problem: str | None = None
            next_run: datetime | None = None
            try:
                next_run = Calendar.parse(task.calendar, aliases=_aliases()).next_after(now)
            except CalendarError as exc:
                problem = exc.message
            statuses.append(
                TaskStatus(
                    task=task,
                    next_run=next_run,
                    last_run=self._moment(entry["last_run"]) if entry.get("last_run") else None,
                    last_status=entry.get("last_status"),
                    last_detail=str(entry.get("last_detail", "")),
                    missed=int(entry.get("missed", 0)),
                    problem=problem,
                )
            )
        return statuses


class SchedulerThread(threading.Thread):
    """
    A thread that looks at the schedules now and then.

    Attributes:
        interval: Seconds between looks.
    """

    def __init__(self, scheduler: Scheduler, *, interval: float = 30.0) -> None:
        """
        Args:
            scheduler: What to look with.
            interval: Seconds between looks.
        """
        super().__init__(name="noust-scheduler", daemon=True)
        self.scheduler = scheduler
        self.interval = interval
        self._stopped = threading.Event()

    def run(self) -> None:
        """Look at once (that is how what was missed is found), then every interval, until stopped."""
        while True:
            try:
                self.scheduler.tick()
            except (NoustError, sqlite3.Error, OSError) as exc:
                # An error boundary: a fault in one look must not end the central's schedules.
                log.error("The scheduler's look failed and will be tried again: %s", exc)
            if self._stopped.wait(self.interval):
                return

    def stop(self) -> None:
        """Ask it to end and wait for it to."""
        self._stopped.set()
        if self.is_alive():
            self.join(timeout=10)


def default_scheduler() -> Scheduler:
    """
    Returns:
        The scheduler of this central: the process-wide runner and store, and the state folder's memory.
    """
    return Scheduler()


def _shown(moment: datetime | None) -> str | None:
    """
    Args:
        moment: A local time, or None.

    Returns:
        It as a timer's state prints one (``2026-11-12 02:00:00``), or None.
    """
    return moment.isoformat(sep=" ", timespec="seconds") if moment else None


def state_of(unit: str) -> dict[str, Any]:
    """
    Say what a timer's state would say of a task, for the code that asks about units.

    Args:
        unit: The name the task's timer has on a server.

    Returns:
        ``installed`` (there is a task for it), ``next_run`` and ``last_run``.
    """
    for status in default_scheduler().status():
        if status.task.unit == unit:
            return {
                "installed": True,
                "next_run": _shown(status.next_run),
                "last_run": _shown(status.last_run),
            }
    return {"installed": False, "next_run": None, "last_run": None}


def listing() -> list[dict[str, str]]:
    """
    List the backup schedules of the store as the listing of timers lists them.

    Returns:
        One entry for each application's schedule, with the keys :meth:`BackupScheduler.list_schedules`
        gives each timer: the unit, the domain, when it runs next and when it last ran, the calendar and
        what the row says of retention.
    """
    by_key = {status.task.key: status for status in default_scheduler().status()}
    entries: list[dict[str, str]] = []
    for record in get_store().list_backup_schedules():
        status = by_key.get(f"backup:{record.app_domain}")
        unit = status.task.unit if status else record.app_domain
        entries.append(
            {
                "timer": unit,
                "app_name": unit.removeprefix(paths.BACKUP_UNIT_PREFIX),
                "domain": record.app_domain,
                "next_run": (_shown(status.next_run) if status else None) or "pending",
                "last_run": (_shown(status.last_run) if status else None) or "never",
                "schedule": record.schedule,
                "on_calendar": record.schedule,
                "retention_count": ""
                if record.retention_count is None
                else str(record.retention_count),
                "retention_days": ""
                if record.retention_days is None
                else str(record.retention_days),
                "retention_gb": ""
                if record.retention_bytes is None
                else f"{record.retention_bytes / 1024**3:g}",
                "include_databases": "true" if record.include_databases else "false",
                "remote_only": "true" if record.remote_only else "false",
            }
        )
    return entries


def start_if_needed(say: Callable[[str], None]) -> SchedulerThread | None:
    """
    Start the central's own scheduler, when there are no timers for it to be the engine instead of.

    Args:
        say: Where it says what it found and did.

    Returns:
        The running thread, which the caller stops when the central stops; None on a machine whose
        timers are systemd's.
    """
    from noust.managers.supervision import keeps_alive_with_docker

    if not keeps_alive_with_docker():
        return None
    thread = SchedulerThread(Scheduler(say=say))
    thread.start()
    return thread


def rows() -> list[dict[str, Any]]:
    """
    Returns:
        Every task and how it stands, JSON-ready: what ``noust central schedule status`` prints and what
        the console reads.
    """
    return [
        {
            "key": status.task.key,
            "kind": status.task.kind,
            "subject": status.task.subject,
            "calendar": status.task.calendar,
            "next_run": _shown(status.next_run),
            "last_run": _shown(status.last_run),
            "last_status": status.last_status,
            "last_detail": status.last_detail,
            "missed": status.missed,
            "problem": status.problem,
        }
        for status in default_scheduler().status()
    ]
