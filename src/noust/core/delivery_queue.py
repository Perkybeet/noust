# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The words of the deploy queue, and cancelling a job from outside the console.

The queue itself lives in the console's job manager (:mod:`noust.web.jobs`):
it is the process that runs the jobs. What a terminal needs of it - which jobs
build, which states a job is shown in, how to cancel one - cannot come from
there: the CLI works on a machine without the console's dependencies, and
nothing outside the console imports it. They live here instead, in terms of
what the store records, and the console's manager takes them from here, so
"which jobs are builds" has one answer.

A job that waits is cancelled by writing ``cancelled`` in the store, which the
console's queue reads back within a second and never starts. A job that builds
is asked to stop through the application's cancellation
(:mod:`noust.core.cancellation`), which the process running it finds in the
store as well.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from noust.core import cancellation
from noust.core.audit import Actor
from noust.core.audit import record as record_audit
from noust.core.exceptions import CancelRefused
from noust.core.store import NoustStore, get_store

#: The statuses a job row holds (the store's CHECK, and the console's enum).
PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

#: The statuses after which a job does nothing more.
FINISHED_STATUSES = frozenset({COMPLETED, FAILED, CANCELLED})

#: What an operator reads: the five states of a deployment's life. A job's
#: ``status`` keeps the words clients already depend on (``pending`` for what
#: waits, ``completed`` for what worked); this is the vocabulary of the queue.
VISIBLE_STATES = {
    PENDING: "queued",
    RUNNING: "running",
    COMPLETED: "succeeded",
    FAILED: "failed",
    CANCELLED: "cancelled",
}

#: Job types that build an application, by the value the store records. They
#: are the ones that may overlap, up to ``deploy.max_concurrent_builds`` (and
#: never two on one application); every other job runs alone, as every job did
#: before 3.4, because a restore, an operating system update or a certificate
#: round trip is not something to run beside a build.
BUILD_JOB_TYPES = frozenset({"deploy", "update"})

#: Job types that only call the API to have other jobs queued and then wait for
#: them (applying a manifest). They take no slot and wait for nothing: held in
#: the line like a restore, one would wait for the very builds it queued.
ORCHESTRATION_JOB_TYPES = frozenset({"manifest"})


def visible_state(status: str) -> str:
    """
    Name a job's status the way the queue shows it.

    Args:
        status: A job's status.

    Returns:
        ``queued``, ``running``, ``succeeded``, ``failed`` or ``cancelled``.

    Raises:
        KeyError: Not a status a job has.
    """
    return VISIBLE_STATES[status]


@dataclass(frozen=True)
class CancelOutcome:
    """
    What cancelling a job did.

    Attributes:
        job_id: The job.
        state: ``cancelled`` when it had not started and never will, or
            ``cancelling`` when it runs and was asked to stop at the next safe
            point (it ends as cancelled, or finishes if it passed that point).
        request: For a job that runs, what the request was addressed to, which
            is what whoever asked follows to learn how it ended.
    """

    job_id: str
    state: str
    request: cancellation.CancelResult | None = None


def cancel_recorded_job(
    job_id: str, *, actor: str | None = None, store: NoustStore | None = None
) -> CancelOutcome:
    """
    Cancel a job from a process that is not the one running the queue.

    ``noust deploy cancel`` runs in a terminal; the console's queue lives in
    another process. A job that waits is marked cancelled in the store; one
    that builds is asked to stop. The console's own endpoint cancels in memory
    (:meth:`noust.web.jobs.JobManager.cancel`); both end in the same states.

    Args:
        job_id: The job.
        actor: Who asked, as the audit trail names them.
        store: The store; defaults to the process-wide one.

    Returns:
        ``cancelled`` for a job that had not started, ``cancelling`` for one
        that was asked to stop.

    Raises:
        CancelRefused: The job is unknown, has ended, cannot be interrupted, or
            its release is already being activated.
    """
    target = store if store is not None else get_store()
    record = target.get_job(job_id)
    if record is None:
        raise CancelRefused(
            f"Job not found: {job_id}",
            "It may have finished long ago and been cleaned up: 'noust deploy queue' lists "
            "what is running and waiting.",
            reason="unknown",
        )
    if record.status in FINISHED_STATUSES:
        raise CancelRefused(
            f"The job already ended as {visible_state(record.status)}",
            "There is nothing to cancel.",
            reason="finished",
        )
    who = Actor.from_label(actor) if actor else None
    if record.status == PENDING:
        target.update_job(job_id, status=CANCELLED, finished_at=datetime.now().isoformat())
        record_audit(
            "job.cancel",
            actor=who,
            target=f"job:{job_id}",
            details={"type": record.type, "state": "queued"},
        )
        return CancelOutcome(job_id, "cancelled")
    if record.type not in BUILD_JOB_TYPES or not record.domain:
        raise CancelRefused(
            f"The {record.type} job is running and cannot be stopped safely",
            "Only a deploy or an update can be cancelled while it runs: the others are "
            "halfway through changing the system. Wait for it to finish.",
            reason="unsupported",
        )
    request = cancellation.request_cancel(record.domain, by=actor, store=target)
    record_audit(
        "job.cancel",
        actor=who,
        target=f"job:{job_id}",
        details={"type": record.type, "state": "running", "domain": record.domain},
    )
    return CancelOutcome(job_id, "cancelling", request)
