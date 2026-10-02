# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The jobs of ``/api/server``: everything slow that changes the machine.

Each is a thin composition of a manager call, run on the job manager's worker,
that streams what the tool prints into the job's log verbatim and records how it
ended. The updates are the one that is not run here: the job starts the package
manager in its own systemd unit and follows it (see
:mod:`noust.managers.server.updates_unit`), because the ``noust`` package is in
the repository being updated and its post-install script restarts this very
process.

A job that fails raises, so the job manager marks it failed with the error and
the tool's own output, which is what the console shows. Success and failure are
both audited: the endpoint recorded that somebody asked, this records what came
of it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from noust.core.exceptions import NoustError
from noust.managers.server.errors import ServerError
from noust.managers.server.restarts import plan_service_restarts, restart_services
from noust.managers.server.updates import UpdateRecord, new_update_id
from noust.managers.server.updates_unit import start_and_follow
from noust.web.api.server.common import (
    audit_event,
    get_server_context,
    require_job_context,
)
from noust.web.jobs import JobContext

#: The facts an update, a refresh or a change of automatic updates makes stale.
_UPDATE_FACTS = ("updates", "restart", "auto")


def _first_line(exc: NoustError) -> str:
    """
    Summarise an error for the audit log.

    Args:
        exc: The error.

    Returns:
        Its message, one line.
    """
    return " ".join(exc.message.split())


def _audited(
    event: str, target: str, body: Callable[[], dict[str, Any]], **details: Any
) -> dict[str, Any]:
    """
    Run a job's work and record how it ended.

    Args:
        event: The audit event (``server.update``, ``server.storage``...).
        target: What it acts on.
        body: The work.
        **details: What to say about it, beside how it ended.

    Returns:
        What the work returned.

    Raises:
        NoustError: Whatever the work raised, after it was recorded.
    """
    try:
        result = body()
    except NoustError as exc:
        audit_event(
            event,
            target=target,
            outcome="failure",
            stage="finished",
            error=_first_line(exc),
            **details,
        )
        raise
    audit_event(event, target=target, stage="finished", **details)
    return result


def os_refresh_job(
    actor: str | None = None, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Download fresh package metadata and list what is pending.

    Args:
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        How many updates are pending and how many are security updates.

    Raises:
        ServerError: A repository could not be reached; the error carries the
            package manager's output.
    """
    context = require_job_context(job_context)
    ctx = get_server_context()

    def work() -> dict[str, Any]:
        context.update("Refreshing the package lists", 10)
        ctx.updates.refresh(context.log)
        context.update("Listing the pending updates", 70)
        pending = ctx.updates.pending()
        ctx.cache.put("updates", pending)
        ctx.cache.invalidate("restart")
        context.update("Done", 100)
        return {"pending": pending.pending, "security": pending.security}

    return _audited("server.refresh", "packages", work, action="refresh")


def restart_services_job(
    services: list[str] | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Restart the services an update left running on replaced libraries.

    The list is asked again first: the job may run minutes after the request,
    and only what still runs replaced libraries is restarted. The console's own
    unit goes last and without waiting (see :mod:`noust.managers.server.restarts`).

    Args:
        services: The units chosen; every restartable one when None.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What restarted, what was left as it is and why, and whether the
        console restarts.

    Raises:
        ServerError: A unit did not restart; the error carries systemd's words
            for each.
    """
    from noust.managers.service_manager import ServiceManager

    context = require_job_context(job_context)
    ctx = get_server_context()

    def work() -> dict[str, Any]:
        context.update("Asking which services run replaced libraries", 10)
        probe = ctx.updates.restart_probe()
        plan = plan_service_restarts(probe.services, services)
        context.update("Restarting the services", 30)
        outcome = restart_services(plan, ServiceManager(runner=ctx.runner), on_line=context.log)
        ctx.cache.invalidate("restart")
        if outcome.failed:
            raise ServerError(
                f"{len(outcome.failed)} service(s) did not restart",
                "\n".join(f"{unit}: {words}" for unit, words in outcome.failed.items()),
            )
        context.update("Done", 100)
        return {
            "restarted": list(outcome.restarted),
            "refused": dict(plan.refused),
            "restarts_console": plan.restarts_console,
        }

    return _audited("server.update", "services", work, action="restart_services", services=services)


def os_update_job(
    scope: str,
    full: bool = False,
    allow_removals: bool = False,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Apply updates, in a transient systemd unit, following it to its end.

    Args:
        scope: ``security`` or ``all``.
        full: A full upgrade.
        allow_removals: The removal list was confirmed.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        The run's record: which packages, whether a reboot is now due, which
        services run old libraries, which configuration files were kept.

    Raises:
        ServerError: The update failed; the error carries the last lines the
            package manager printed.
    """
    context = require_job_context(job_context)
    ctx = get_server_context()
    update_id = new_update_id()
    context.set_metadata("update_id", update_id)

    def work() -> dict[str, Any]:
        context.update("Starting the update in its own unit", 5)
        record = start_and_follow(
            ctx.updates,
            ctx.unit,
            update_id,
            context.log,
            scope=scope,
            full=full,
            allow_removals=allow_removals,
            job_id=context.job_id,
            actor=actor,
        )
        ctx.cache.invalidate(*_UPDATE_FACTS)
        return _finish(record, context)

    return _audited("server.update", "packages", work, action="apply", update=update_id)


def os_follow_job(
    update_id: str, actor: str | None = None, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Follow an update that kept running while the console was restarted.

    Args:
        update_id: The run's identifier.
        actor: Who asked for the update in the first place.
        job_context: Injected by the job manager.

    Returns:
        The run's record.

    Raises:
        ServerError: The update failed or ended without a result.
    """
    context = require_job_context(job_context)
    ctx = get_server_context()
    context.set_metadata("update_id", update_id)

    def work() -> dict[str, Any]:
        context.update("Following the update that kept running", 10)
        record = ctx.unit.follow(update_id, context.log)
        ctx.cache.invalidate(*_UPDATE_FACTS)
        return _finish(record, context)

    return _audited(
        "server.update", "packages", work, action="apply", update=update_id, resumed=True
    )


def _finish(record: UpdateRecord, context: JobContext) -> dict[str, Any]:
    """
    Turn a finished run into the job's result, or its failure.

    Args:
        record: What the run recorded.
        context: The job's context.

    Returns:
        The record as data.

    Raises:
        ServerError: The run failed.
    """
    if record.status == "failed":
        raise ServerError(
            record.error or "The update failed",
            "Read the output below. 'noust server updates repair' finishes what dpkg "
            "left half done.",
            output="\n".join(record.tail[-40:]),
        )
    context.update("Update finished", 100)
    return record.to_dict()


def os_repair_job(
    actor: str | None = None, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Finish a half-applied update.

    Args:
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        The commands that ran.

    Raises:
        ServerError: A repair step failed, carrying its output.
    """
    context = require_job_context(job_context)
    ctx = get_server_context()

    def work() -> dict[str, Any]:
        context.update("Repairing the package database", 10)
        ran = ctx.updates.repair(context.log)
        ctx.cache.invalidate(*_UPDATE_FACTS)
        context.update("Done", 100)
        return {"ran": ran}

    return _audited("server.update", "packages", work, action="repair")


def auto_updates_job(
    enabled: bool,
    security_only: bool = True,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Turn the automatic updates on or off.

    Args:
        enabled: The wanted state.
        security_only: Apply only security updates.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What was done, one sentence per step, and the state afterwards.

    Raises:
        ServerError: It cannot be changed here, or a step failed.
    """
    context = require_job_context(job_context)
    ctx = get_server_context()

    def work() -> dict[str, Any]:
        context.update("Changing the automatic updates", 10)
        steps = ctx.updates.backend.set_auto(enabled, security_only, context.log)
        for step in steps:
            context.log(step, "success")
        ctx.cache.invalidate("auto")
        status = ctx.updates.backend.auto_status()
        context.update("Done", 100)
        return {"steps": steps, "enabled": status.enabled, "security_only": status.security_only}

    return _audited("server.update", "automatic-updates", work, action="auto", enabled=enabled)


def analyze_job(actor: str | None = None, job_context: JobContext | None = None) -> dict[str, Any]:
    """
    Measure the places that take disk space.

    Args:
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        The analysis.
    """
    context = require_job_context(job_context)
    ctx = get_server_context()
    steps: list[str] = []

    def work() -> dict[str, Any]:
        def step(text: str) -> None:
            steps.append(text)
            context.update(text, min(90, 10 + 15 * len(steps)))

        analysis = ctx.storage.analyze(step)
        ctx.cache.put("storage.analysis", analysis)
        ctx.cache.invalidate("storage.usage")
        context.update("Done", 100)
        return analysis.to_dict()

    return _audited("server.storage", "storage", work, action="analyze")


def cleanup_job(
    action: str,
    confirm: bool = False,
    params: dict[str, Any] | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Run one cleanup action.

    Args:
        action: One of the closed list of actions.
        confirm: The caller read what it takes.
        params: The action's parameters.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What was done: the commands, the space freed, what was removed.

    Raises:
        ServerError: A command failed, carrying its output.
    """
    context = require_job_context(job_context)
    ctx = get_server_context()
    context.set_metadata("action", action)

    def work() -> dict[str, Any]:
        context.update(f"Cleaning: {action}", 10)
        result = ctx.storage.cleanup(action, confirm=confirm, on_line=context.log, **(params or {}))
        ctx.cache.invalidate("disk", "storage.usage")
        context.update("Done", 100)
        return {
            "action": result.action,
            "commands": result.commands,
            "freed_bytes": result.freed_bytes,
            "removed": result.removed,
        }

    # Named "image", never "target": that is the record's own field (item 53).
    named = {("image" if key == "target" else key): value for key, value in (params or {}).items()}
    return _audited("server.storage", "storage", work, action="cleanup", cleanup=action, **named)


def swap_job(
    action: str,
    size_mb: int | None = None,
    swappiness: int | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Make or remove the swap file.

    Args:
        action: ``create`` or ``remove``.
        size_mb: For ``create``: its size in MiB.
        swappiness: For ``create``: ``vm.swappiness`` to set with it.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What was done, one sentence per step.

    Raises:
        ServerError: A step failed; what had been done is undone.
    """
    context = require_job_context(job_context)
    ctx = get_server_context()
    context.set_metadata("action", action)

    def work() -> dict[str, Any]:
        if action == "create":
            context.update("Making the swap file", 10)
            steps = ctx.swap.create(
                (size_mb or 0) * 1024**2,
                on_line=context.log,
                swappiness=swappiness if swappiness is not None else 10,
            )
        else:
            context.update("Removing the swap file", 10)
            steps = ctx.swap.remove(context.log)
        for step in steps:
            context.log(step, "success")
        ctx.cache.invalidate("swap")
        context.update("Done", 100)
        return {"steps": steps}

    return _audited("server.storage", "swap", work, action=f"swap-{action}")
