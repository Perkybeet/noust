# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Jobs whose work runs in a transient unit, found again after the console restarted.

A job runs on a thread of the console, and a console that restarts takes its
threads with it: every job it left pending or running is marked "Interrupted by
a panel restart" (:meth:`noust.web.jobs.JobManager._fail_interrupted_jobs`).
That is the truth for work that ran in the console. It is not for work that
runs in a ``systemd-run`` unit of its own, which is the point of such a unit:
an operating system update and Noust updating itself keep going, and the
``noust`` package among the upgrades is precisely what restarts the console.
On 2026-10-02 an ``apt-get upgrade`` finished well three seconds after the
restart it caused, its job was declared interrupted, and the central's fleet
job counted the node as failed and skipped the others.

So a job records the unit its work runs in (:meth:`JobContext.set_unit`, the
``unit`` column of ``jobs``), and at start-up such a job is not failed but
followed again here, the one reconciliation for every job of this kind:

- the unit ended well: the job completes, with what the unit wrote appended to
  its log (the lines the old console had not relayed yet);
- it failed: the job fails with systemd's result and the unit's own words;
- it is still running: the job stays running, its log grows as the unit
  writes, and it ends with the unit, or gives up after the kind's deadline
  saying how to follow it.

What "ended well" means for a kind of job beyond systemd's verdict is that
kind's own record (:class:`UnitJobKind`): an update's record holds the packages
it installed, a self-update's the version now running. A job a console older
than this one started has no unit recorded; the kind finds it in that record.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from noust.core.audit import Actor
from noust.core.audit import record as record_audit
from noust.core.exceptions import NoustError
from noust.core.redact import scrubber_for
from noust.core.runner import CommandRunner, get_runner
from noust.core.store import JobRecord, NoustStore
from noust.managers.transient_unit import (
    JobVerdict,
    JournalEntry,
    UnitJobKind,
    default_verdict,
    ending_of,
    read_journal,
    unit_state,
    with_output,
)

if TYPE_CHECKING:
    from noust.managers.self_update import SelfUpdate
    from noust.managers.server.updates import RecordStore

logger = logging.getLogger(__name__)

#: Seconds between looks at a unit still running.
POLL_SECONDS = 2.0

#: Lines of the job's log compared with the unit's output to find where the
#: old console stopped relaying it.
ALIGN_WINDOW = 20

#: What reconciling a job can fail with: the store, the log file, a command.
_RECONCILE_ERRORS: tuple[type[Exception], ...] = (NoustError, OSError, sqlite3.Error, ValueError)

#: A line of a job's log file: ``[stamp] [LEVEL] message``.
_LOG_LINE = re.compile(r"^\[[^\]]*\] \[[A-Z]+\] ?(.*)$")


@dataclass(frozen=True)
class Followed:
    """
    A job to reconcile, with its unit.

    Attributes:
        job: The job, as the store had it at start-up.
        unit: The unit its work runs in, without ``.service``.
        kind: How its type is reconciled.
    """

    job: JobRecord
    unit: str
    kind: UnitJobKind


def job_kinds(
    *,
    update_records: RecordStore | None = None,
    self_update: Callable[[], SelfUpdate] | None = None,
) -> dict[str, UnitJobKind]:
    """
    Name the types of job whose work runs in a transient unit, and how each is reconciled.

    A type missing here is still reconciled when its job recorded a unit, on
    systemd's verdict alone.

    Args:
        update_records: Where operating system updates are recorded (tests).
        self_update: Builds the self-update manager (tests).

    Returns:
        Each kind, by the job type's value.
    """
    from noust.managers import self_update as self_update_module
    from noust.managers.server import updates_unit

    return {
        "os_update": updates_unit.job_kind(update_records),
        "self_update": self_update_module.job_kind(self_update),
    }


def units_to_reconcile(
    jobs: Iterable[JobRecord], kinds: Mapping[str, UnitJobKind] | None = None
) -> list[Followed]:
    """
    Pick, among the jobs a previous console left unfinished, those whose work has a unit.

    Args:
        jobs: The unfinished jobs.
        kinds: The kinds by job type; :func:`job_kinds` when omitted.

    Returns:
        Each job with its unit. A job with none is not here: it ran in the
        console, died with it, and is marked interrupted as before.
    """
    if kinds is None:
        kinds = job_kinds()
    followed: list[Followed] = []
    for job in jobs:
        kind = kinds.get(job.type, UnitJobKind())
        unit = job.unit
        if not unit and job.status == "running" and kind.find_unit is not None:
            try:
                unit = kind.find_unit(job)
            except _RECONCILE_ERRORS as exc:
                logger.warning("Could not look for the unit of job %s: %s", job.id, exc)
        if unit:
            followed.append(Followed(job=job, unit=unit, kind=kind))
    return followed


def unlogged(logged: Sequence[str], output: Sequence[str]) -> list[str]:
    """
    Find what a unit wrote that the job's log does not have yet.

    The old console relayed the unit's output until it was restarted, so its
    log ends with a stretch of that output; the rest is what came after.

    Args:
        logged: The messages of the job's log, in order.
        output: Everything the unit wrote, in order.

    Returns:
        The tail of ``output`` after the last stretch the log ends with; all
        of it when the log ends with none.
    """
    if not logged:
        return list(output)
    for end in range(len(output), 0, -1):
        size = min(end, ALIGN_WINDOW, len(logged))
        if list(logged[-size:]) == list(output[end - size : end]):
            return list(output[end:])
    return list(output)


def _logged(path: Path | None) -> list[str]:
    """
    Read back the messages of a job's log file.

    Args:
        path: The file.

    Returns:
        Each line's message, without its stamp and level.
    """
    if path is None or not path.is_file():
        return []
    messages = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LOG_LINE.match(line)
        messages.append(match.group(1) if match else line)
    return messages


def _append(path: Path | None, lines: Iterable[str], level: str = "info") -> None:
    """
    Add lines to a job's log file, in the job manager's format.

    Args:
        path: The file; nothing is written without one.
        lines: The messages.
        level: Their level.
    """
    if path is None:
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(f"[{stamp}] [{level.upper()}] {line}\n")


def _messages(entries: Iterable[JournalEntry], scrub: Callable[[str], str]) -> list[str]:
    """
    Turn journal entries into log lines, as the job manager splits and scrubs them.

    Args:
        entries: The entries.
        scrub: Removes the job's secrets.

    Returns:
        One line per line of every message.
    """
    return [
        piece for entry in entries for piece in scrub(entry.message).replace("\r", "").split("\n")
    ]


def reconcile_job(
    store: NoustStore,
    followed: Followed,
    *,
    runner: CommandRunner | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    poll: float = POLL_SECONDS,
    publish: Callable[[str], None] | None = None,
) -> str:
    """
    Follow a job's unit to its end and record how the job ended.

    Args:
        store: The store.
        followed: The job and its unit.
        runner: Command runner; the process-wide one when omitted.
        sleep: Waits between looks.
        clock: Monotonic clock, for the deadline.
        poll: Seconds between looks.
        publish: Told the job's id once it ended, for whoever listens.

    Returns:
        The job's final status, ``completed`` or ``failed``.
    """
    runner = runner or get_runner()
    job, unit = followed.job, followed.unit
    path = Path(job.log_path) if job.log_path else None
    scrub = scrubber_for(job.domain).scrub
    entries = read_journal(runner, unit)
    missing = unlogged(_logged(path), _messages(entries, scrub))
    _append(
        path,
        [f"The console restarted while this job ran; {unit} kept going on its own."],
        "warning",
    )
    _append(path, missing)
    cursor = entries[-1].cursor if entries else None
    deadline = clock() + followed.kind.max_seconds

    while True:
        state = unit_state(runner, unit)
        if state.known and not state.active:
            # The last lines, systemd's verdict among them, land as the state
            # flips: one more look, or the ending is what goes missing.
            more = read_journal(runner, unit, after_cursor=cursor)
            entries += more
            _append(path, _messages(more, scrub))
            break
        if clock() > deadline:
            verdict = JobVerdict(
                False,
                error=with_output(
                    f"Gave up waiting for {unit}: it is still running. It keeps going on its "
                    f"own; 'journalctl -fu {unit}' follows it.",
                    _messages(entries, scrub),
                ),
            )
            return _finish(store, followed, verdict, path, scrub, publish)
        sleep(poll)
        more = read_journal(runner, unit, after_cursor=cursor)
        if more:
            cursor = more[-1].cursor
            entries += more
            _append(path, _messages(more, scrub))

    ending = ending_of(unit, state, entries)
    lines = _messages(entries, scrub)
    decide = followed.kind.verdict
    verdict = decide(job, unit, ending, lines) if decide else default_verdict(unit, ending, lines)
    return _finish(store, followed, verdict, path, scrub, publish)


def _audit(followed: Followed, succeeded: bool, error: str | None) -> None:
    """
    Record how a reconciled job ended, as its own function would have.

    The job's function audits its end (``server.update``, stage finished);
    its thread died with the old console, so without this a job finished from
    its unit left no line in the trail.

    Args:
        followed: The job, its unit and its kind.
        succeeded: How it ended.
        error: Why it failed, scrubbed; its first line is recorded.
    """
    kind, job = followed.kind, followed.job
    if kind.audit_event is None:
        return
    details: dict[str, Any] = {"stage": "finished", "job": job.id, "reconciled": True}
    if kind.audit_details is not None:
        details.update(kind.audit_details(job, followed.unit))
    if not succeeded:
        details["error"] = " ".join((error or "The job failed").splitlines()[0].split())
    record_audit(
        kind.audit_event,
        actor=Actor.from_label(job.actor) if job.actor else Actor.system("jobs"),
        target=kind.audit_target,
        outcome="ok" if succeeded else "failure",
        details=details,
        correlation_id=f"job-{job.id}",
    )


def _finish(
    store: NoustStore,
    followed: Followed,
    verdict: JobVerdict,
    path: Path | None,
    scrub: Callable[[str], str],
    publish: Callable[[str], None] | None,
) -> str:
    """
    Write how a reconciled job ended, in its record, its log and the audit trail.

    Args:
        store: The store.
        followed: The job as it was at start-up, with its unit and kind.
        verdict: How it ended.
        path: Its log file.
        scrub: Removes its secrets.
        publish: Told its id afterwards.

    Returns:
        The job's final status.
    """
    job = followed.job
    status = "completed" if verdict.succeeded else "failed"
    error = scrub(verdict.error or "The job failed") if not verdict.succeeded else None
    if verdict.succeeded:
        _append(path, ["Job completed successfully"], "success")
    else:
        _append(path, [f"Job failed: {(error or '').splitlines()[0]}"], "error")
    store.save_job(
        replace(
            job,
            status=status,
            progress=job.total_steps if verdict.succeeded else job.progress,
            error=error,
            result_json=json.dumps(verdict.result) if verdict.result is not None else None,
            finished_at=datetime.now().isoformat(),
        )
    )
    logger.info("Job %s, reconciled with its unit, %s", job.id, status)
    _audit(followed, verdict.succeeded, error)
    if publish is not None:
        publish(job.id)
    return status


def _follow_safely(
    store: NoustStore, followed: Followed, publish: Callable[[str], None] | None
) -> None:
    """
    Reconcile one job on its own thread, never leaving it running for good.

    Args:
        store: The store.
        followed: The job and its unit.
        publish: Told the job's id once it ended.
    """
    try:
        reconcile_job(store, followed, publish=publish)
    # The thread's error boundary: whatever went wrong, the job must not stay
    # "running" forever, and the console must hear why.
    except Exception as exc:
        logger.exception("Could not follow job %s in %s again", followed.job.id, followed.unit)
        error = (
            f"The console restarted and could not follow {followed.unit} again: {exc}. "
            f"'journalctl -u {followed.unit}' says how it ended."
        )
        try:
            store.save_job(
                replace(
                    followed.job,
                    status="failed",
                    error=error,
                    finished_at=datetime.now().isoformat(),
                )
            )
        except _RECONCILE_ERRORS as recording:
            logger.warning("Could not record job %s: %s", followed.job.id, recording)
        _audit(followed, False, error)


def _in_thread(target: Callable[[], None]) -> None:
    """
    Run a reconciliation on a daemon thread.

    Args:
        target: The work.
    """
    threading.Thread(target=target, daemon=True, name="job-reconcile").start()


def reattach(
    store: NoustStore,
    jobs: Sequence[Followed],
    *,
    publish: Callable[[str], None] | None = None,
    start: Callable[[Callable[[], None]], None] = _in_thread,
) -> None:
    """
    Follow each job's unit again, each on its own thread.

    Args:
        store: The store.
        jobs: The jobs and their units.
        publish: Told a job's id once it ended.
        start: Runs each follow; a daemon thread by default.
    """
    for followed in jobs:
        logger.info("Job %s runs in %s: following it again", followed.job.id, followed.unit)
        start(functools.partial(_follow_safely, store, followed, publish))
