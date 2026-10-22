# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Applying a manifest from the console (J-15): a job that makes the calls of its author.

``noust manifest apply`` runs on the machine of whoever applies, as a client of
the API. The console does the same on the server for a person who pasted the
manifest into it: the engine (:mod:`noust.manifest`) is unchanged, its
:class:`~noust.manifest.plan.Transport` is the server's own API called back with
the credentials of the person who clicked (:mod:`noust.web.gateway_client`).
Nothing in the job decides what is allowed: every call is checked by the route
it reaches, against the role, the team, sudo mode and the approvals of its
author, and audited as its author. A person who may not set variables gets the
server's refusal at that step, and the run stops there.

The job waits for the deployments it queues, because what follows an
application's creation (its domains, its databases, its backups) needs the
application to exist. It therefore runs outside the line
(:data:`noust.core.delivery_queue.ORCHESTRATION_JOB_TYPES`): inside it, it
would wait for the very builds it queued.

The credentials live in the job's arguments, which are in memory for the length
of the job and are never stored; the manifest's text is not kept either, and
the values of its secret variables are scrubbed from the log.
"""

from __future__ import annotations

from typing import Any

from noust.client.jobs import DEFAULT_WAIT
from noust.core.exceptions import DeploymentError
from noust.manifest import Applier, RefusingResolver, Transport, parse, plan, restart_changed
from noust.manifest.plan import Action, Plan
from noust.web.gateway_client import ForwardedCaller, GatewayTransport
from noust.web.jobs import JobContext


def transport_for(caller: ForwardedCaller) -> Transport:
    """
    Args:
        caller: Whose credentials the calls carry.

    Returns:
        The server's API, called as the caller, able to change things.
    """
    return GatewayTransport(caller, writable=True)


def apply_manifest_job(
    manifest: str,
    caller: ForwardedCaller,
    restart: bool = False,
    timeout: float = DEFAULT_WAIT,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Make the server match a manifest, one call of its author at a time.

    Args:
        manifest: The manifest's text, already checked once by the route.
        caller: Whose credentials the calls carry.
        restart: Restart the applications whose variables changed.
        timeout: Seconds to wait for each deployment it queues.
        job_context: Injected by the job manager.

    Returns:
        What each change came to, as ``noust manifest apply --json`` shows it.

    Raises:
        DeploymentError: A change failed. The result is published first, and
            the message names the change and what the server answered.
    """
    parsed = parse(manifest)
    if job_context is not None:
        # Literal values that look like secrets must not reach this job's log.
        for app in parsed.applications:
            job_context.add_secret_env(
                {k: v for k, v in (app.env or {}).items() if isinstance(v, str)}
            )

    def say(line: str, level: str = "info") -> None:
        if job_context is not None:
            job_context.log(line, level)

    transport = transport_for(caller)
    # Compared as the author: the server answers the variables' comparison without ever
    # returning a value, and only to someone allowed to confirm sudo mode.
    pending: Plan = plan(parsed, transport, RefusingResolver(), compare_env=True)
    if pending.empty:
        say("The server already matches the manifest.", "success")
        result: dict[str, Any] = {"ok": True, "outcomes": [], "restart": []}
        if job_context is not None:
            job_context.set_result(result)
        return result

    total = len(pending.actions)
    done = 0

    def starting(action: Action) -> None:
        nonlocal done
        if job_context is not None:
            job_context.update(action.summary, int(100 * done / total))
        done += 1

    applier = Applier(transport, wait=True, timeout=timeout, on_line=say)
    outcome = applier.apply(pending, on_start=starting)
    if restart:
        restart_changed(outcome, transport, lambda line: say(line, "warning"))
    result = outcome.to_dict()
    if job_context is not None:
        job_context.set_result(result)
    for item in outcome.outcomes:
        say(
            f"{item.action.target}: {item.message or item.status}",
            "error"
            if item.status == "failed"
            else "warning"
            if item.status == "skipped"
            else "info",
        )
    if not outcome.ok:
        failed = next(o for o in outcome.outcomes if o.status == "failed")
        raise DeploymentError(
            f"Applying the manifest stopped at {failed.action.target}: {failed.message}",
            details="Fix the cause and apply again: the plan then holds only what is left.",
        )
    return result
