# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The composers of what the monitor watches from outside an application.

The same rules as :mod:`noust.core.notifications.composers` (a title that is a
state and nothing else, one sentence, the server first, a program's own words
verbatim in the excerpt), for:

- the **disk cleanup** a policy ran by itself, which freed something, or did not;
- a **scheduled task whose timer is not running**, so it will not run at all;
- a **probe** of an address from outside the machine that stopped answering, and
  the moment it answered again.

Pure: whoever watches reads the machine and hands these functions the facts.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from noust.core.messages import message
from noust.core.notifications.composers import _fact, build
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.excerpt import make_excerpt
from noust.core.notifications.formatting import format_bytes, format_duration, format_utc
from noust.core.notifications.model import Fact, Notification, State


def compose_cleanup(
    status: str,
    ctx: NotificationContext,
    *,
    mount: str | None = None,
    percent_before: float | None = None,
    percent_after: float | None = None,
    freed_bytes: int | None = None,
    actions: Sequence[str] = (),
    failures: Sequence[tuple[str, str]] = (),
) -> Notification:
    """
    Compose the notification for a disk cleanup the server's policy ran.

    Args:
        status: How it ended: ``ok``, ``partial`` (some actions failed) or
            ``failed`` (all did).
        ctx: The context.
        mount: The filesystem it was about.
        percent_before: How full it was before.
        percent_after: How full it is now.
        freed_bytes: What the actions freed, when the tools said.
        actions: The actions that ran.
        failures: ``(action, the tool's own words)`` of each that failed.

    Returns:
        ``cleanup.completed`` (under ``cleanup_success``, which ships off),
        ``cleanup.partial`` (a warning) or ``cleanup.failed`` (a failure), both
        under ``cleanup_failed``.
    """
    code = {"ok": "cleanup.completed", "partial": "cleanup.partial"}.get(status, "cleanup.failed")
    facts: list[Fact] = []
    if percent_before is not None:
        used = f"{percent_before:.0f}%"
        if percent_after is not None:
            used += f" -> {percent_after:.0f}%"
        facts.append(_fact("used", used, ctx))
    if freed_bytes is not None:
        facts.append(_fact("freed", format_bytes(freed_bytes), ctx))
    if actions:
        facts.append(_fact("actions", ", ".join(actions), ctx))
    excerpt = None
    if failures:
        action, words = failures[0]
        excerpt = make_excerpt(
            words, label=message("excerpt.cleanup.error", ctx.locale, action=action)
        )
    return build(
        ctx,
        kind="cleanup_success" if status == "ok" else "cleanup_failed",
        code=code,
        state={"ok": State.OK, "partial": State.WARNING}.get(status, State.FAILED),
        subject=mount or ctx.server,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        command=None
        if status == "ok"
        else _fact("cleanup_runs", "noust server cleanup-schedule runs", ctx, mono=True),
        excerpt=excerpt,
        path="/server",
    )


def compose_cron_timer(
    job: str,
    stopped: bool,
    ctx: NotificationContext,
    *,
    state: str = "",
    schedule: str = "",
    domain: str | None = None,
) -> Notification:
    """
    Compose the notification for a scheduled task whose timer is not running.

    Nothing failed: the task never starts, so no run can say so.

    Args:
        job: The task's name.
        stopped: The timer stopped (otherwise it is running again).
        ctx: The context.
        state: systemd's word for the timer (``inactive``, ``failed``).
        schedule: When the task is meant to run.
        domain: The application it belongs to, when it has one.

    Returns:
        ``cron.timer_stopped`` (a warning) or ``cron.timer_running`` (the way
        back), both under ``cron_missed``.
    """
    code = "cron.timer_stopped" if stopped else "cron.timer_running"
    facts: list[Fact] = []
    if schedule:
        facts.append(_fact("schedule", schedule, ctx))
    if state and stopped:
        facts.append(_fact("timer", state, ctx))
    return build(
        ctx,
        kind="cron_missed",
        code=code,
        state=State.WARNING if stopped else State.OK,
        subject=job,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        command=_fact("enable_job", f"noust cron enable {job}", ctx, mono=True)
        if stopped
        else None,
        path="/cron",
        domain=domain,
    )


def compose_probe(
    name: str,
    failed: bool,
    ctx: NotificationContext,
    *,
    address: str = "",
    reason: str = "",
    since: datetime | None = None,
    down_for_s: float | None = None,
    latency_ms: int | None = None,
) -> Notification:
    """
    Compose the notification for a probe that stopped answering, or answers again.

    Args:
        name: The probe's name.
        failed: It stopped answering (otherwise it answers again).
        ctx: The context.
        address: What is asked.
        reason: What came back, verbatim (a status line, a connection error).
        since: When the failures began.
        down_for_s: How long it did not answer, for the recovery.
        latency_ms: How long the answer took, for the recovery.

    Returns:
        ``probe.failed`` (a failure, under ``probe_failed``) or
        ``probe.recovered`` (under ``probe_recovered``).
    """
    code = "probe.failed" if failed else "probe.recovered"
    facts: list[Fact] = []
    if address:
        facts.append(_fact("address", address, ctx))
    if since is not None and failed:
        facts.append(_fact("since", format_utc(since), ctx))
    if down_for_s is not None and not failed:
        facts.append(_fact("downtime", format_duration(down_for_s), ctx))
    if latency_ms is not None and not failed:
        facts.append(_fact("latency", f"{latency_ms} ms", ctx))
    excerpt = (
        make_excerpt(reason, label=message("excerpt.probe.reply", ctx.locale))
        if reason and failed
        else None
    )
    return build(
        ctx,
        kind="probe_failed" if failed else "probe_recovered",
        code=code,
        state=State.FAILED if failed else State.OK,
        subject=name,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        command=_fact("probe_run", f"noust probe run {name}", ctx, mono=True) if failed else None,
        excerpt=excerpt,
        path="/settings/probes",
        ts=since if failed else None,
    )
