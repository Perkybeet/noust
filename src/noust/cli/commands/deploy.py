# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust deploy cancel`` and ``noust deploy queue``: stop a build, see the line.

``noust deploy`` on its own is still ``noust create`` (it always was: the name
is in scripts and muscle memory); these two verbs are what it also does when
named. They are the terminal's face of what the console's ``/api/jobs/queue``
and ``/api/jobs/{id}/cancel`` do, through the same code
(:mod:`noust.core.cancellation`, :func:`noust.web.jobs.cancel_recorded_job`),
and work whichever process runs the deployment: the console's queue, another
terminal, a webhook.
"""

from __future__ import annotations

import json
import re
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core import cancellation, delivery, delivery_store
from noust.core.audit.actor import cli_actor
from noust.core.delivery_queue import (
    BUILD_JOB_TYPES,
    FINISHED_STATUSES,
    PENDING,
    RUNNING,
    cancel_recorded_job,
    visible_state,
)
from noust.core.exceptions import CancelRefused, DomainError
from noust.core.store import JobRecord, get_store
from noust.validators.domain import validate_domain

#: What a job id looks like: the first eight characters of a uuid.
_JOB_ID = re.compile(r"^[0-9a-f]{1,36}$")

#: Seconds ``cancel`` waits for the build to say it stopped.
DEFAULT_WAIT = 30


@click.group("deploy", cls=NoustGroup)
def cli() -> None:
    """Follow the deploy queue and cancel a build."""


def _unfinished(record: JobRecord) -> bool:
    """
    Args:
        record: A job as the store has it.

    Returns:
        Whether it waits or runs.
    """
    return record.status not in FINISHED_STATUSES


def _waiting_for(record: JobRecord, running: list[JobRecord]) -> str:
    """
    Say what a waiting job waits for, as far as the store can tell.

    Args:
        record: The waiting job.
        running: What runs now.

    Returns:
        One short phrase; empty when it starts next.
    """
    for other in running:
        if record.domain and other.domain == record.domain:
            return f"the {other.type} of {record.domain} (job {other.id})"
    builds = [job for job in running if job.type in BUILD_JOB_TYPES]
    limit = delivery.current(refresh=True).max_concurrent_builds
    if record.type in BUILD_JOB_TYPES and len(builds) >= limit:
        return f"a free build slot ({len(builds)} of {limit} are building)"
    if running and record.type not in BUILD_JOB_TYPES:
        return "the jobs that are running now"
    return ""


@cli.command("queue", read_only=True)
@click.argument("domain", required=False)
@json_option("Print the queue as JSON.")
@pass_context
def queue_command(ctx: Context, domain: str | None) -> None:
    """
    Show what is deploying and what is waiting to.

    Builds of different applications run at the same time, up to
    deploy.max_concurrent_builds; one application builds one thing at a time,
    and what arrives meanwhile (a push, a click on Update) waits here in order.
    With DOMAIN, only that application's.

    Read from the store, so it shows the console's queue and the builds of
    other terminals alike.
    """
    wanted = validate_domain(domain) if domain else None
    store = get_store()
    running = [
        job
        for job in store.list_jobs(limit=500, status=RUNNING, domain=wanted)
        if job.type in BUILD_JOB_TYPES
    ]
    waiting = sorted(
        (
            job
            for job in store.list_jobs(limit=500, status=PENDING, domain=wanted)
            if job.type in BUILD_JOB_TYPES
        ),
        key=lambda job: (job.created_at or "", job.id),
    )
    settings = delivery.current(refresh=True)
    everything_running = store.list_jobs(limit=500, status=RUNNING)

    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "max_concurrent_builds": settings.max_concurrent_builds,
                    "queue_limit": settings.queue_limit,
                    "running": [_payload(job, None) for job in running],
                    "queued": [
                        _payload(job, (position, _waiting_for(job, everything_running)))
                        for position, job in enumerate(waiting, start=1)
                    ],
                }
            )
        )
        return

    logger = ctx.logger
    if not running and not waiting:
        logger.info("Nothing is deploying and nothing is waiting")
        return
    rows: list[list[Any]] = [
        [
            visible_state(job.status),
            job.id,
            job.type,
            job.domain or "-",
            job.started_at or job.created_at or "-",
            "",
        ]
        for job in running
    ]
    rows += [
        [
            f"queued {position}",
            job.id,
            job.type,
            job.domain or "-",
            job.created_at or "-",
            _waiting_for(job, everything_running),
        ]
        for position, job in enumerate(waiting, start=1)
    ]
    logger.table(["State", "Job", "What", "Application", "Since", "Waiting for"], rows)
    logger.blank()
    logger.info(
        f"{len(running)} of {settings.max_concurrent_builds} builds running, "
        f"{len(waiting)} of {settings.queue_limit} waiting"
    )
    logger.info("Stop one with: noust deploy cancel <application or job>")


def _payload(job: JobRecord, queued: tuple[int, str] | None) -> dict[str, Any]:
    """
    Describe a job of the queue as JSON.

    Args:
        job: The job.
        queued: Its position and what it waits for, when it waits.

    Returns:
        The description.
    """
    return {
        "id": job.id,
        "type": job.type,
        "domain": job.domain,
        "state": visible_state(job.status),
        "status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "queue_position": queued[0] if queued else None,
        "waiting_for": (queued[1] or None) if queued else None,
    }


@cli.command("cancel")
@click.argument("target")
@click.option(
    "--all",
    "also_waiting",
    is_flag=True,
    help="With an application: also cancel what waits in the queue for it.",
)
@click.option(
    "--wait",
    "wait_seconds",
    type=click.IntRange(0, 600),
    default=DEFAULT_WAIT,
    show_default=True,
    help="Seconds to wait for the build to stop (0 to ask and return).",
)
@pass_context
def cancel_command(ctx: Context, target: str, also_waiting: bool, wait_seconds: int) -> None:
    """
    Cancel a deployment: a job that waits, or the build that runs.

    TARGET is an application's domain or a job's id ('noust deploy queue'
    lists them). A job that waits never starts. A deploy or an update that is
    building is stopped at once: its build is killed, the release it was
    making is removed, the deployment is recorded as cancelled and what served
    before is never touched. Once its release is being activated it is too
    late, and it finishes or puts back what served by itself.

    Works on a deployment started anywhere: the console, a webhook, another
    terminal.
    """
    actor = cli_actor().label
    if _JOB_ID.match(target) and get_store().get_job(target) is not None:
        _cancel_job(ctx, target, actor, wait_seconds)
        return
    try:
        domain = validate_domain(target)
    except DomainError as exc:
        raise click.UsageError(
            f"{target!r} is neither a job of the queue nor an application's domain"
        ) from exc
    _cancel_application(ctx, domain, actor, wait_seconds, also_waiting)


def _cancel_job(ctx: Context, job_id: str, actor: str, wait_seconds: int) -> None:
    """
    Cancel a job by its id.

    Args:
        ctx: The CLI context.
        job_id: The job.
        actor: Who asks.
        wait_seconds: How long to wait for a running build to stop.
    """
    record = get_store().get_job(job_id)
    outcome = cancel_recorded_job(job_id, actor=actor)
    if outcome.state == "cancelled":
        ctx.logger.success(
            f"Cancelled job {job_id}: it never started. The queue reads it back within a second."
        )
        return
    if record is not None and record.domain and outcome.request is not None:
        _wait(ctx, record.domain, outcome.request, wait_seconds)


def _cancel_application(
    ctx: Context, domain: str, actor: str, wait_seconds: int, also_waiting: bool
) -> None:
    """
    Cancel what runs on an application, and optionally what waits for it.

    Args:
        ctx: The CLI context.
        domain: The application.
        actor: Who asks.
        wait_seconds: How long to wait for the build to stop.
        also_waiting: Cancel the queued jobs of the application too.
    """
    store = get_store()
    waiting = [
        job
        for job in store.list_jobs(limit=500, status=PENDING, domain=domain)
        if job.type in BUILD_JOB_TYPES
    ]
    if also_waiting:
        for job in waiting:
            cancel_recorded_job(job.id, actor=actor)
        if waiting:
            ctx.logger.info(f"Cancelled {len(waiting)} waiting request(s) for {domain}")
    try:
        _follow(ctx, domain, wait_seconds, actor=actor)
    except CancelRefused as refused:
        if refused.reason == "idle" and waiting and not also_waiting:
            raise CancelRefused(
                f"Nothing is building on {domain}, but {len(waiting)} request(s) wait for it",
                "Cancel them with 'noust deploy cancel {job}' (see 'noust deploy queue "
                f"{domain}') or all at once with: noust deploy cancel {domain} --all",
                reason="idle",
            ) from None
        if refused.reason == "idle" and also_waiting and waiting:
            return
        raise


def _follow(ctx: Context, domain: str, wait_seconds: int, *, actor: str | None = None) -> None:
    """
    Ask the operation on an application to stop, and say how it ended.

    Args:
        ctx: The CLI context.
        domain: The application.
        wait_seconds: How long to wait for it to answer.
        actor: Who asks; the operating system identity when None.

    Raises:
        CancelRefused: There is nothing to cancel, or it is too late.
    """
    result = cancellation.request_cancel(domain, by=actor or cli_actor().label)
    where = f" (stopping at {result.phase})" if result.phase else ""
    ctx.logger.info(f"Asked the {result.operation} of {domain} to stop{where}")
    _wait(ctx, domain, result, wait_seconds)


def _wait(ctx: Context, domain: str, result: cancellation.CancelResult, wait_seconds: int) -> None:
    """
    Wait for the operation a request was addressed to, and say how it ended.

    Args:
        ctx: The CLI context.
        domain: The application.
        result: What the request was addressed to.
        wait_seconds: How long to wait for it to answer; 0 does not wait.

    Raises:
        CancelRefused: It was too late, or what runs cannot be interrupted.
    """
    logger = ctx.logger
    if wait_seconds == 0:
        return
    outcome = cancellation.wait_for_outcome(domain, result, timeout=float(wait_seconds))
    if outcome == delivery_store.CANCELLED:
        logger.success(
            f"Cancelled the {result.operation} of {domain}: its build stopped and the release "
            "it was making was removed. What served before was not touched."
        )
    elif outcome == delivery_store.TOO_LATE:
        raise CancelRefused(
            f"The {result.operation} of {domain} reached its activation first",
            "Its release is being switched in behind the health gate: it finishes, or puts "
            "back what served by itself. Roll back with 'noust releases rollback "
            f"{domain}' once it is done if it is not what you want.",
            reason="too_late",
        )
    elif outcome == delivery_store.FINISHED:
        logger.info(f"The {result.operation} of {domain} finished before it could be stopped")
    elif outcome == delivery_store.UNSUPPORTED:
        raise CancelRefused(
            f"The {result.operation} of {domain} cannot be interrupted",
            "It builds in place, in the tree that serves, or inside the Docker daemon: "
            "stopping it halfway would leave the application broken. Wait for it.",
            reason="unsupported",
        )
    else:
        logger.warning(
            f"The {result.operation} of {domain} was asked to stop and still runs: it stops at "
            "the next safe point. Follow it with: noust deploy queue"
        )
