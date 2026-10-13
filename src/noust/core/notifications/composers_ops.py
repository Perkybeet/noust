# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The composers of what the 3.4 monitor watches beyond units, certificates and disks.

The same rules as :mod:`noust.core.notifications.composers` (a title that is a
state and nothing else, one sentence, the server first, a program's own words
verbatim in the excerpt), for:

- a user-defined **alert rule** that started or stopped breaching its limit;
- a scheduled **backup** that did not run (nothing failed, so no failure said so);
- a **cron job** that ended with an error, or, for whoever wants a heartbeat, ran;
- operating system **updates** waiting, or the check that lists them failing;
- an API **token** about to expire, or expired;
- the **digest** of what a channel's quiet hours held.

Pure, like the rest of the package: the monitor reads the machine and hands
these functions the facts.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from noust.core.messages import message, plural
from noust.core.notifications.composers import _fact, build
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.excerpt import make_excerpt
from noust.core.notifications.formatting import format_date, format_duration, format_utc
from noust.core.notifications.model import (
    DIGEST_KIND,
    Fact,
    Notification,
    Section,
    State,
)

#: The packages a patches notification names; the rest are counted.
MAX_PACKAGES_LISTED = 8

#: The entries a digest spells out; the rest are counted.
MAX_DIGEST_ENTRIES = 25


def compose_alert_firing(
    rule: str,
    metric: str,
    value: str,
    threshold: str,
    comparator: str,
    duration_s: float,
    ctx: NotificationContext,
    *,
    failed: bool = False,
    subject: str | None = None,
    domain: str | None = None,
    since: datetime | None = None,
) -> Notification:
    """
    Compose the notification for a rule whose limit has been breached long enough.

    Args:
        rule: The rule's name.
        metric: What it measures, in words (``CPU of the server``).
        value: The reading now, with its unit (``93.4%``).
        threshold: The limit, with its unit (``90%``).
        comparator: ``>``, ``>=``, ``<`` or ``<=``.
        duration_s: How long the limit has been breached.
        ctx: The context.
        failed: The rule's severity is ``failed`` rather than ``warning``.
        subject: What it is about when it is not the rule (an application).
        domain: The application's domain, when the rule is about one.
        since: When the breach began.

    Returns:
        ``alert.above`` or ``alert.below`` (a warning, or a failure), under the
        ``alert_firing`` switch.
    """
    code = "alert.above" if comparator in (">", ">=") else "alert.below"
    facts = [
        _fact("rule", rule, ctx),
        _fact("value", value, ctx),
        _fact("threshold", threshold, ctx),
    ]
    if since is not None:
        facts.append(_fact("since", format_utc(since), ctx))
    return build(
        ctx,
        kind="alert_firing",
        code=code,
        state=State.FAILED if failed else State.WARNING,
        subject=subject or rule,
        summary=message(
            f"summary.{code}",
            ctx.locale,
            metric=metric,
            threshold=threshold,
            duration=format_duration(duration_s),
        ),
        facts=facts,
        path=f"/apps/{domain}" if domain else "/server",
        domain=domain,
        ts=since,
    )


def compose_alert_resolved(
    rule: str,
    metric: str,
    value: str,
    threshold: str,
    firing_for_s: float,
    ctx: NotificationContext,
    *,
    subject: str | None = None,
    domain: str | None = None,
) -> Notification:
    """
    Compose the notification that closes an alert.

    Args:
        rule: The rule's name.
        metric: What it measures, in words.
        value: The reading now, with its unit.
        threshold: The limit, with its unit.
        firing_for_s: How long it was firing.
        ctx: The context.
        subject: What it is about when it is not the rule.
        domain: The application's domain, when the rule is about one.

    Returns:
        ``alert.resolved``, under the ``alert_resolved`` switch.
    """
    code = "alert.resolved"
    return build(
        ctx,
        kind="alert_resolved",
        code=code,
        state=State.OK,
        subject=subject or rule,
        summary=message(
            f"summary.{code}", ctx.locale, metric=metric, value=value, threshold=threshold
        ),
        facts=[
            _fact("rule", rule, ctx),
            _fact("value", value, ctx),
            _fact("firing_for", format_duration(firing_for_s), ctx),
        ],
        path=f"/apps/{domain}" if domain else "/server",
        domain=domain,
    )


def compose_backup_missing(
    domain: str,
    days: int,
    ctx: NotificationContext,
    *,
    last_backup: str | None = None,
    schedule: str | None = None,
) -> Notification:
    """
    Compose the notification for a scheduled backup that did not run.

    Args:
        domain: The application.
        days: The days without a backup that raised this.
        ctx: The context.
        last_backup: The newest backup's date (ISO), None when there is none.
        schedule: The backup schedule, as the operator wrote it.

    Returns:
        ``backup.missing`` or, with no backup at all, ``backup.never``: both a
        warning under the ``backup_missing`` switch, with the command that
        makes one now.
    """
    never = last_backup is None
    code = "backup.never" if never else "backup.missing"
    facts: list[Fact] = []
    if last_backup is not None:
        facts.append(_fact("last_backup", format_date(last_backup, ctx.locale), ctx))
    if schedule:
        facts.append(_fact("schedule", schedule, ctx, mono=True))
    summary = (
        message("summary.backup.never", ctx.locale)
        if never
        else message(
            "summary.backup.missing", ctx.locale, days=days, unit=plural("day", ctx.locale, days)
        )
    )
    return build(
        ctx,
        kind="backup_missing",
        code=code,
        state=State.WARNING,
        subject=domain,
        summary=summary,
        facts=facts,
        command=_fact("backup_now", f"noust backup create {domain}", ctx, mono=True),
        path="/backups",
        domain=domain,
    )


def compose_cron(
    job: str,
    ok: bool,
    ctx: NotificationContext,
    *,
    exit_code: int | None = None,
    result: str | None = None,
    ran_at: datetime | None = None,
    output: str | None = None,
    domain: str | None = None,
) -> Notification:
    """
    Compose the notification for a run of a scheduled task.

    Args:
        job: The task's name.
        ok: Whether it ended without an error.
        ctx: The context.
        exit_code: The exit status of its command.
        result: systemd's word for how it ended (``exit-code``, ``timeout``).
        ran_at: When it ended.
        output: The last lines the task printed, verbatim.
        domain: The application it belongs to, when it has one.

    Returns:
        ``cron.failed`` (a failure, under ``cron_failed``) or ``cron.succeeded``
        (under ``cron_success``, which ships off).
    """
    code = "cron.succeeded" if ok else "cron.failed"
    facts: list[Fact] = []
    if ran_at is not None:
        facts.append(_fact("last_run", format_utc(ran_at), ctx))
    if exit_code is not None:
        facts.append(_fact("exit_status", str(exit_code), ctx))
    if result and not ok:
        facts.append(_fact("result", result, ctx))
    excerpt = (
        make_excerpt(output, label=message("excerpt.cron.output", ctx.locale, job=job))
        if output and not ok
        else None
    )
    return build(
        ctx,
        kind="cron_success" if ok else "cron_failed",
        code=code,
        state=State.OK if ok else State.FAILED,
        subject=job,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        command=None if ok else _fact("runs", f"noust cron runs {job}", ctx, mono=True),
        excerpt=excerpt,
        path="/cron",
        domain=domain,
        ts=ran_at,
    )


def compose_os_patches(
    total: int,
    security: int,
    ctx: NotificationContext,
    *,
    packages: Sequence[str] = (),
    reboot_required: bool | None = None,
) -> Notification:
    """
    Compose the notification for operating system updates that are waiting.

    Args:
        total: How many updates wait.
        security: How many of them are security updates.
        ctx: The context.
        packages: Names of the packages, security ones first; a few are listed.
        reboot_required: Whether the server is already waiting for a reboot.

    Returns:
        ``patches.security`` (a warning) when some are security updates, else
        ``patches.available`` (information): both under ``os_patches``.
    """
    code = "patches.security" if security else "patches.available"
    unit = plural("package", ctx.locale, total)
    facts = [_fact("packages", str(total), ctx)]
    if security:
        facts.append(_fact("security", str(security), ctx))
    if packages:
        listed = ", ".join(packages[:MAX_PACKAGES_LISTED])
        if len(packages) > MAX_PACKAGES_LISTED:
            listed += f" (+{len(packages) - MAX_PACKAGES_LISTED})"
        facts.append(_fact("detail", listed, ctx))
    if reboot_required:
        facts.append(_fact("reboot", message("ui.reboot_pending", ctx.locale), ctx))
    return build(
        ctx,
        kind="os_patches",
        code=code,
        state=State.WARNING if security else State.INFO,
        subject="",
        summary=message(f"summary.{code}", ctx.locale, count=total, security=security, unit=unit),
        facts=facts,
        command=_fact("list", "noust server updates list", ctx, mono=True),
        path="/server",
    )


def compose_os_patches_check_failed(error: str, ctx: NotificationContext) -> Notification:
    """
    Compose the notification for an update check that could not be made.

    Args:
        error: Why, in the package manager's own words, verbatim.
        ctx: The context.

    Returns:
        ``patches.check_failed`` (a warning) under ``os_patches``, the error as
        the excerpt.
    """
    code = "patches.check_failed"
    return build(
        ctx,
        kind="os_patches",
        code=code,
        state=State.WARNING,
        subject="",
        summary=message(f"summary.{code}", ctx.locale),
        excerpt=make_excerpt(error, label=""),
        command=_fact("list", "noust server updates list", ctx, mono=True),
        path="/server",
    )


def compose_token_expiring(
    name: str,
    expires: str,
    days_left: int,
    ctx: NotificationContext,
    *,
    owner: str | None = None,
) -> Notification:
    """
    Compose the notification for an API token that expires, or already did.

    Args:
        name: The token's name.
        expires: The day it expires, ISO ``YYYY-MM-DD``.
        days_left: Whole days until then; negative once it has.
        ctx: The context.
        owner: The account that owns it.

    Returns:
        ``token.expiring`` or ``token.today`` (warnings) or ``token.expired``
        (a failure), all under ``token_expiring``.
    """
    when = format_date(expires, ctx.locale)
    if days_left < 0:
        code, state = "token.expired", State.FAILED
        summary = message("summary.token.expired", ctx.locale)
    elif days_left == 0:
        code, state = "token.today", State.WARNING
        summary = message("summary.token.today", ctx.locale)
    else:
        code, state = "token.expiring", State.WARNING
        summary = message(
            "summary.token.expiring",
            ctx.locale,
            days=days_left,
            unit=plural("day", ctx.locale, days_left),
        )
    facts = [_fact("expires", when, ctx)]
    if owner:
        facts.append(_fact("owner", owner, ctx))
    return build(
        ctx,
        kind="token_expiring",
        code=code,
        state=state,
        subject=name,
        summary=summary,
        facts=facts,
        command=_fact("revoke", "noust token create", ctx, mono=True),
        path="/settings/tokens",
    )


def compose_digest(items: Sequence[Notification], ctx: NotificationContext) -> Notification:
    """
    Compose the one message that stands for what a channel's quiet hours held.

    Args:
        items: The held notifications, oldest first.
        ctx: The context.

    Returns:
        ``digest.held``: how many, between when and when, and one section per
        notification (its headline, summary and time) up to
        :data:`MAX_DIGEST_ENTRIES`. A warning when any held one was, else
        information; it is not an event and has no switch.
    """
    code = "digest.held"
    shown = items[:MAX_DIGEST_ENTRIES]
    sections = tuple(
        Section(
            heading=item.headline,
            rows=tuple(
                fact
                for fact in (
                    _fact("when", format_utc(item.ts), ctx),
                    _fact("detail", item.summary, ctx) if item.summary else None,
                )
                if fact is not None
            ),
            state=item.state,
        )
        for item in shown
    )
    facts = [_fact("events", str(len(items)), ctx)]
    if items:
        first, last = items[0].ts, items[-1].ts
        facts.append(_fact("period", f"{format_utc(first)} - {format_utc(last)}", ctx))
    worst = (
        State.WARNING
        if any(item.state in (State.WARNING, State.FAILED) for item in items)
        else State.INFO
    )
    return build(
        ctx,
        kind=DIGEST_KIND,
        code=code,
        state=worst,
        subject="",
        summary=message(f"summary.{code}", ctx.locale, count=len(items)),
        facts=facts,
        sections=sections,
    )
