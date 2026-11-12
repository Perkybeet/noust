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
  the moment it answered again;
- the **central** that manages the server going quiet, and calling again: said by the
  server, because the central cannot say it of itself;
- the **renewal of certificates** that failed or whose timer is not running (item 172);
- an **application the kernel killed** for running out of memory (item 175);
- the **registration of a domain** that is about to lapse, or has (item 173).

Pure: whoever watches reads the machine and hands these functions the facts.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from noust.core.messages import message, plural
from noust.core.notifications.composers import _fact, build
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.excerpt import make_excerpt
from noust.core.notifications.formatting import (
    format_bytes,
    format_date,
    format_duration,
    format_utc,
)
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
        else _fact("cleanup_runs", "noust server cleanup-schedule history", ctx, mono=True),
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


def compose_central(
    central: str,
    silent: bool,
    ctx: NotificationContext,
    *,
    last_heard: float | None = None,
    silent_for_s: float | None = None,
) -> Notification:
    """
    Compose the notification for the central that manages this server going quiet, or calling again.

    Said by the server through its own channels, so it arrives when the central cannot be asked to.

    Args:
        central: The central, as this server's fleet token names it.
        silent: It stopped calling (otherwise it calls again).
        ctx: The context of the server, which is who says it.
        last_heard: Epoch seconds of its last call, for the silence.
        silent_for_s: How long it did not call.

    Returns:
        ``central.silent`` (a warning) or ``central.heard`` (the way back), both under
        ``central_silent``.
    """
    code = "central.silent" if silent else "central.heard"
    facts: list[Fact] = []
    if silent and last_heard is not None:
        facts.append(
            _fact("last_heard", format_utc(datetime.fromtimestamp(last_heard, timezone.utc)), ctx)
        )
    if not silent and silent_for_s is not None:
        facts.append(_fact("silent_for", format_duration(silent_for_s), ctx))
    minutes = max(round((silent_for_s or 0) / 60), 1)
    return build(
        ctx,
        kind="central_silent",
        code=code,
        state=State.WARNING if silent else State.OK,
        subject=central,
        summary=message(f"summary.{code}", ctx.locale, minutes=minutes)
        if silent
        else message(f"summary.{code}", ctx.locale),
        facts=facts,
    )


def compose_cert_renewal(
    problem: str,
    ctx: NotificationContext,
    *,
    unit: str = "certbot.timer",
    last_run: str | None = None,
    next_run: str | None = None,
    output: str = "",
    certificates: Sequence[str] = (),
) -> Notification:
    """
    Compose the notification for certbot's renewal not working.

    A certificate that is about to expire says so on its own (``cert_expiring``); this says the
    reason, early: the last run failed, or nothing runs it.

    Args:
        problem: ``failed`` (the last run ended in an error) or ``timer_stopped``.
        ctx: The context.
        unit: The timer that runs the renewal.
        last_run: When it last ran, as systemd or certbot's log wrote it.
        next_run: When the timer fires next.
        output: certbot's own words of the failed run, verbatim.
        certificates: The certificates certbot renews.

    Returns:
        ``cert.renewal_failed`` (a failure) or ``cert.renewal_stopped`` (a warning), both under
        ``cert_renewal_failed``.
    """
    failed = problem == "failed"
    code = "cert.renewal_failed" if failed else "cert.renewal_stopped"
    facts: list[Fact] = []
    if certificates:
        facts.append(_fact("certificates", ", ".join(certificates), ctx))
    if last_run:
        facts.append(_fact("renewal_last", last_run, ctx))
    if next_run and not failed:
        facts.append(_fact("renewal_next", next_run, ctx))
    excerpt = (
        make_excerpt(output, label=message("excerpt.cert.renewal", ctx.locale))
        if failed and output
        else None
    )
    command = (
        _fact("renew", "noust cert renew", ctx, mono=True)
        if failed
        else _fact("enable_timer", f"systemctl enable --now {unit}", ctx, mono=True)
    )
    return build(
        ctx,
        kind="cert_renewal_failed",
        code=code,
        state=State.FAILED if failed else State.WARNING,
        subject=unit,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        command=command,
        excerpt=excerpt,
        path="/domains",
    )


def compose_app_oom(
    domain: str,
    ctx: NotificationContext,
    *,
    count: int,
    limit: int | None = None,
    peak: int | None = None,
    since: str = "",
) -> Notification:
    """
    Compose the notification for an application the kernel killed for running out of memory.

    Args:
        domain: The application.
        ctx: The context.
        count: How many kills the counter rose by since the monitor last looked.
        limit: Its memory limit in bytes; None when nothing limits it but the machine.
        peak: The most it used, in bytes, when the kernel keeps it.
        since: When its unit started, which is where the kernel's count begins.

    Returns:
        ``app.oom_killed`` (a failure), under ``app_oom_killed``.
    """
    facts = [
        _fact("oom_kills", f"{count} {plural('time', ctx.locale, count)}", ctx),
        _fact(
            "oom_limit",
            format_bytes(limit) if limit is not None else message("fact.oom_none", ctx.locale),
            ctx,
        ),
    ]
    if peak is not None:
        facts.append(_fact("oom_peak", format_bytes(peak), ctx))
    if since:
        facts.append(_fact("oom_since", since, ctx))
    return build(
        ctx,
        kind="app_oom_killed",
        code="app.oom_killed",
        state=State.FAILED,
        subject=domain,
        summary=message("summary.app.oom_killed", ctx.locale, domain=domain),
        facts=facts,
        command=_fact("diagnose", f"noust diagnose {domain}", ctx, mono=True),
        path=f"/apps/{domain}",
        domain=domain,
    )


def compose_domain_registration(
    domain: str,
    expiry: str,
    days_left: int,
    ctx: NotificationContext,
    *,
    names: Sequence[str] = (),
    registry: str = "",
) -> Notification:
    """
    Compose the notification for the registration of a domain that is about to lapse, or has.

    Args:
        domain: The registrable domain, as the registry recognised it.
        expiry: When the registration expires, ISO ``YYYY-MM-DD``.
        days_left: Whole days until it expires; negative once it has.
        ctx: The context.
        names: The names Noust serves under it.
        registry: The registry that said so, by its host.

    Returns:
        ``domain.expiring`` (a warning, under ``domain_expiring``) or, for a negative
        ``days_left``, ``domain.expired`` (a failure, under ``domain_expired``).
    """
    when = format_date(expiry, ctx.locale)
    if days_left < 0:
        code, kind, state = "domain.expired", "domain_expired", State.FAILED
        summary = message(
            "summary.domain.expired",
            ctx.locale,
            domain=domain,
            days=-days_left,
            unit=plural("day", ctx.locale, -days_left),
            date=when,
        )
    else:
        code, kind, state = "domain.expiring", "domain_expiring", State.WARNING
        summary = (
            message("summary.domain.expiring_today", ctx.locale, domain=domain, date=when)
            if days_left == 0
            else message(
                "summary.domain.expiring",
                ctx.locale,
                domain=domain,
                days=days_left,
                unit=plural("day", ctx.locale, days_left),
                date=when,
            )
        )
    facts: list[Fact] = []
    if names:
        facts.append(_fact("domain_names", ", ".join(names), ctx))
    if registry:
        facts.append(_fact("domain_registry", registry, ctx))
    return build(
        ctx,
        kind=kind,
        code=code,
        state=state,
        subject=domain,
        summary=summary,
        facts=facts,
        command=_fact("domain_expiry", "noust domain expiry", ctx, mono=True),
        path="/domains",
    )
