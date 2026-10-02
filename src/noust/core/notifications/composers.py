# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
From what happened to a :class:`~noust.core.notifications.model.Notification`.

One composer per family of events. Each takes *facts* - a deployment's
event, a certificate's expiry, a unit's state - and the
:class:`~noust.core.notifications.context.NotificationContext`, and returns
the structured notification. None of them knows a channel; none of them
concatenates a paragraph for a chat.

The rules every composer keeps (the tests check each):

- The **title** is the state as a phrase and nothing else: no commit, no
  duration, no server. Those are facts.
- The **summary** is one sentence, in Noust's voice, that repeats neither the
  title nor a fact.
- A **fact** appears once. The commit is a fact, not also a headline.
- The **server** is the first fact, always.
- The failing step's own **message** is shown only when it says something the
  summary does not, and then as the first line of the excerpt, never as a
  paragraph of its own.
- What another program said - a journal, stderr - is the **excerpt**, the end
  of it, verbatim.
- **Certificates** that already expired are ``cert.expired``: never
  "expires in -80 days".

Composers are pure and cheap: they read nothing from the machine, so a gallery
or a test builds every event without one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from noust.core.messages import message, plural
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.excerpt import (
    make_excerpt,
    normalize,
    split_error_text,
    split_evidence,
)
from noust.core.notifications.formatting import (
    format_bytes,
    format_date,
    format_duration,
    format_utc,
)
from noust.core.notifications.model import (
    REPORT_KIND,
    TEST_KIND,
    Excerpt,
    Fact,
    Notification,
    Section,
    State,
    clean_inline,
)

if TYPE_CHECKING:
    from noust.deployers.deploy_events import DeployEvent

#: What a deployment can be, from the recorder's point of view.
OPERATIONS: tuple[str, ...] = ("deploy", "update", "rollback", "activate", "migrate")

#: The switch and the state of each way a deployment ends.
_DEPLOY_KINDS = {
    "started": ("deploy_started", State.PROGRESS),
    "succeeded": ("deploy_success", State.OK),
    "failed": ("deploy_failed", State.FAILED),
    "rolled_back": ("deploy_rolled_back", State.WARNING),
}

#: The longest a commit's subject line is shown.
_COMMIT_SUBJECT_LIMIT = 72

# Words that add nothing when deciding whether a message repeats what the
# title and the subject already say.
_STOP_WORDS = frozenset({"a", "an", "the", "of", "to", "for", "in", "on", "was", "is", "with"})


@dataclass(frozen=True)
class PreviewOf:
    """
    The pull request a preview application answers.

    Attributes:
        parent: The application the preview belongs to.
        number: The pull request number, when known.
    """

    parent: str
    number: int | None = None


# -- building blocks ----------------------------------------------------------


def _label(key: str, ctx: NotificationContext) -> str:
    """
    Args:
        key: A fact key (``commit``).
        ctx: The context.

    Returns:
        Its translated label.
    """
    return message(f"fact.{key}", ctx.locale)


def _fact(key: str, value: str, ctx: NotificationContext, *, mono: bool = False) -> Fact:
    """
    Args:
        key: The fact's key, also the catalog key of its label.
        value: Its value.
        ctx: The context.
        mono: Whether it is set in monospace.

    Returns:
        The fact.
    """
    return Fact(key, _label(key, ctx), value, mono)


def server_fact(ctx: NotificationContext) -> Fact:
    """
    Args:
        ctx: The context.

    Returns:
        The fact that names the server, the first of every notification.
    """
    return _fact("server", ctx.server, ctx)


def build(
    ctx: NotificationContext,
    *,
    kind: str,
    code: str,
    state: State,
    subject: str,
    summary: str,
    facts: Iterable[Fact] = (),
    command: Fact | None = None,
    excerpt: Excerpt | None = None,
    path: str | None = None,
    node: str | None = None,
    domain: str | None = None,
    ts: datetime | None = None,
    title: str | None = None,
    sections: Sequence[Section] = (),
) -> Notification:
    """
    Assemble a notification: the one constructor every composer uses.

    Public so another area's emitter (a server event, a fleet event) builds a
    notification with the same shape - the server first, one console link -
    instead of one of its own. ``title`` and ``summary`` must already be
    translated; add their keys to :mod:`noust.core.messages`.

    Args:
        ctx: The context.
        kind: The switch (:data:`~noust.core.notifications.model.EVENT_KINDS`).
        code: The fine name; also the catalog key of the title
            (``title.<code>``) when ``title`` is not given.
        state: How it went.
        subject: What it is about.
        summary: The one sentence, translated.
        facts: The labelled values; the server is put first.
        command: The command to run next.
        excerpt: The system's own words.
        path: The console page, starting with ``/``; no link when None.
        node: The managed server the page is about, on a central.
        domain: The application's domain, when it is about one.
        ts: When it happened; now when None.
        title: The state as a phrase; the catalog's ``title.<code>`` when None.
        sections: Blocks of facts, for a report.

    Returns:
        The notification.
    """
    link = ctx.link(path, node=node) if path else None
    extra: dict[str, Any] = {"ts": ts} if ts is not None else {}
    return Notification(
        kind=kind,
        code=code,
        state=state,
        locale=ctx.locale,
        title=title if title is not None else message(f"title.{code}", ctx.locale),
        subject=subject,
        summary=summary,
        server=ctx.server,
        facts=(server_fact(ctx), *facts),
        command=command,
        excerpt=excerpt,
        links=(link,) if link else (),
        sections=tuple(sections),
        domain=domain,
        **extra,
    )


def _tokens(text: str) -> set[str]:
    """
    Args:
        text: Any text.

    Returns:
        The words of it that say something.
    """
    return {word for word in normalize(text).split() if word not in _STOP_WORDS}


def _repeats(text: str, known: Iterable[str]) -> bool:
    """
    Decide whether a message only says what the notification already says.

    Args:
        text: A failing step's own message.
        known: What the notification says in its own words (English, whatever
            the language: the message is English and so is what it is compared
            with).

    Returns:
        True when every word of it is already among the known texts.
    """
    words = _tokens(text)
    if not words:
        return True
    seen: set[str] = set()
    for item in known:
        seen |= _tokens(item)
    return words <= seen


def _error_excerpt(
    failure: str | None,
    ctx: NotificationContext,
    *,
    repeated: Iterable[str],
    avoid: Iterable[str],
) -> Excerpt | None:
    """
    Turn a failure's text into the excerpt: its output, led by its message.

    Args:
        failure: ``str(error)`` of a Noust error, possibly with the tool's
            output after it.
        ctx: The context.
        repeated: What the notification already says; the message is dropped
            when it only repeats that.
        avoid: Lines the excerpt must not repeat (facts, the command).

    Returns:
        The excerpt, or None when there is nothing left to show.
    """
    text, output = split_error_text(failure)
    lead = None if _repeats(text, repeated) else text
    return _excerpt_of(output, ctx, lead=lead, avoid=avoid)


def _excerpt_of(
    output: str,
    ctx: NotificationContext,
    *,
    lead: str | None,
    avoid: Iterable[str],
) -> Excerpt | None:
    """
    Take the excerpt of some failure output, preferring a journal.

    Args:
        output: What the tool or the health gate wrote.
        ctx: The context.
        lead: A line to put first.
        avoid: Lines the excerpt must not repeat.

    Returns:
        The excerpt, or None when there is nothing to show.
    """
    parts = split_evidence(output)
    if parts.journal:
        label = message("excerpt.journal", ctx.locale, unit=parts.unit)
        source: str | list[str] = list(parts.journal)
    else:
        label = message("excerpt.output", ctx.locale)
        source = "\n".join(piece for piece in (parts.summary, parts.trailing) if piece)
    return make_excerpt(source, label=label, avoid=avoid, lead=lead, pin_error=True)


def _english(code: str, part: str) -> str:
    """
    Args:
        code: A notification code whose summary takes no placeholder.
        part: ``title`` or ``summary``.

    Returns:
        The English text of it, for comparing against an English message.
    """
    return message(f"{part}.{code}", "en")


# -- deployments --------------------------------------------------------------


def _commit_value(event: DeployEvent) -> str | None:
    """
    Args:
        event: The deployment event.

    Returns:
        ``a1b2c3d (main): Fix cart total`` with what is known, or None.
    """
    if not event.commit:
        return None
    value = event.commit
    if event.branch:
        value += f" ({clean_inline(event.branch)})"
    subject = clean_inline(event.commit_message or "")
    if subject:
        if len(subject) > _COMMIT_SUBJECT_LIMIT:
            subject = subject[: _COMMIT_SUBJECT_LIMIT - 1] + "…"
        value += f": {subject}"
    return value


def _trigger_value(trigger: str, ctx: NotificationContext) -> str:
    """
    Args:
        trigger: What started it, as the deployment history records it.
        ctx: The context.

    Returns:
        A name for it in the reader's language; an unknown value as it is.
    """
    try:
        return message(f"trigger.{trigger}", ctx.locale)
    except KeyError:
        return trigger


def compose_deploy(
    event: DeployEvent,
    ctx: NotificationContext,
    *,
    preview: PreviewOf | None = None,
) -> Notification:
    """
    Compose the notification for one moment of a deployment.

    Args:
        event: The deployment's event.
        ctx: The context.
        preview: The pull request this application previews, when it is one.

    Returns:
        The notification. Its kind is the switch (``deploy_success``) whatever
        the operation; its code says what it was (``update.succeeded``).
    """
    outcome = event.kind.value
    operation = event.operation if event.operation in OPERATIONS else "deploy"
    if operation == "migrate" and outcome == "rolled_back":
        # A migration is undone exactly when it fails; there is no previous
        # release that "serves again".
        outcome = "failed"
    kind, state = _DEPLOY_KINDS[outcome]
    code = f"{operation}.{outcome}"
    summary = message(f"summary.{code}", ctx.locale)

    facts: list[Fact] = []
    if preview is not None:
        value = preview.parent + (f" #{preview.number}" if preview.number is not None else "")
        facts.append(_fact("preview", value, ctx))
    if event.release_id:
        facts.append(_fact("release", event.release_id, ctx))
    commit = _commit_value(event)
    if commit:
        facts.append(_fact("commit", commit, ctx))
    if event.trigger:
        facts.append(_fact("trigger", _trigger_value(event.trigger, ctx), ctx))
    if event.duration_s is not None and outcome != "started":
        facts.append(_fact("duration", format_duration(event.duration_s), ctx))

    command = None
    excerpt = None
    if outcome in ("failed", "rolled_back"):
        if event.domain:
            command = _fact("inspect", f"noust diagnose {event.domain}", ctx, mono=True)
        failure = event.error_message or ""
        output = event.error_output or ""
        if not failure and not output and event.error:
            failure, output = split_error_text(event.error)
        lead = None
        if outcome == "failed" and not _repeats(
            failure, [_english(code, "title"), _english(code, "summary"), event.domain]
        ):
            lead = failure
        avoid = [
            message(f"title.{code}", ctx.locale),
            summary,
            *(fact.value for fact in facts),
        ]
        if command is not None:
            avoid.append(command.value)
        excerpt = _excerpt_of(output, ctx, lead=lead, avoid=avoid)

    if not event.domain:
        # A job that failed before it knew which application it was for.
        path = None
    elif event.deployment_id is not None:
        path = f"/apps/{event.domain}/deployments/{event.deployment_id}"
    else:
        path = f"/apps/{event.domain}"
    return build(
        ctx,
        kind=kind,
        code=code,
        state=state,
        subject=event.domain,
        summary=summary,
        facts=facts,
        command=command,
        excerpt=excerpt,
        path=path,
        domain=event.domain or None,
        ts=event.ts,
    )


# -- backups ------------------------------------------------------------------


def compose_restore(
    *,
    ok: bool,
    domain: str | None,
    backup_id: str | None,
    error: str | None,
    ctx: NotificationContext,
) -> Notification:
    """
    Compose the notification for a finished backup restore.

    A restore is not a deployment: it has its own switches
    (``restore_success``, ``restore_failed``) and its own code.

    Args:
        ok: Whether it completed.
        domain: The application restored, when the job says which.
        backup_id: The backup restored from, when known.
        error: The failure's text as the job holds it, when it failed.
        ctx: The context.

    Returns:
        The notification.
    """
    code = "restore.completed" if ok else "restore.failed"
    summary = message(f"summary.{code}", ctx.locale)
    facts = [_fact("backup", backup_id, ctx, mono=True)] if backup_id else []
    excerpt = None
    if not ok:
        excerpt = _error_excerpt(
            error,
            ctx,
            repeated=[_english(code, "title"), _english(code, "summary"), domain or ""],
            avoid=[summary, *(fact.value for fact in facts)],
        )
    return build(
        ctx,
        kind="restore_success" if ok else "restore_failed",
        code=code,
        state=State.OK if ok else State.FAILED,
        subject=domain or "",
        summary=summary,
        facts=facts,
        excerpt=excerpt,
        path="/backups",
        domain=domain,
    )


def compose_backup_failed(
    domain: str | None, error: str | None, ctx: NotificationContext
) -> Notification:
    """
    Compose the notification for a backup that did not complete.

    Args:
        domain: The application backed up, when known.
        error: The failure's text, ``message\\n  Details: ...``.
        ctx: The context.

    Returns:
        The notification.
    """
    code = "backup.failed"
    summary = message(f"summary.{code}", ctx.locale)
    return build(
        ctx,
        kind="backup_failed",
        code=code,
        state=State.FAILED,
        subject=domain or "",
        summary=summary,
        excerpt=_error_excerpt(
            error,
            ctx,
            repeated=[_english(code, "title"), _english(code, "summary"), domain or ""],
            avoid=[summary],
        ),
        path="/backups",
        domain=domain,
    )


def compose_backup_upload_failed(
    domain: str, destination: str, error: str | None, ctx: NotificationContext
) -> Notification:
    """
    Compose the notification for a backup that could not be uploaded.

    The local copy was kept, so this is a warning, not a failure.

    Args:
        domain: The application backed up.
        destination: The name of the destination that refused it.
        error: The failure's text; ``Details`` is rclone's own stderr.
        ctx: The context.

    Returns:
        The notification.
    """
    code = "backup.upload_failed"
    summary = message(f"summary.{code}", ctx.locale)
    facts = [_fact("destination", destination, ctx)]
    return build(
        ctx,
        kind="backup_failed",
        code=code,
        state=State.WARNING,
        subject=domain,
        summary=summary,
        facts=facts,
        excerpt=_error_excerpt(
            error,
            ctx,
            repeated=[_english(code, "title"), _english(code, "summary"), domain, destination],
            avoid=[summary, destination],
        ),
        path="/backups",
        domain=domain,
    )


def compose_backup_schedule_missing(domain: str, ctx: NotificationContext) -> Notification:
    """
    Compose the notification for a backup timer with no schedule behind it.

    Args:
        domain: The application whose timer fired.
        ctx: The context.

    Returns:
        The notification, with the command that saves the schedule again.
    """
    code = "backup.schedule_missing"
    return build(
        ctx,
        kind="backup_failed",
        code=code,
        state=State.WARNING,
        subject=domain,
        summary=message(f"summary.{code}", ctx.locale),
        command=_fact("reschedule", f"noust backup schedule update {domain}", ctx, mono=True),
        path="/backups",
        domain=domain,
    )


def compose_backup_completed(
    domain: str,
    backup_id: str,
    ctx: NotificationContext,
    *,
    size_bytes: int | None = None,
    destinations: Sequence[str] = (),
) -> Notification:
    """
    Compose the optional heartbeat for a scheduled backup that completed.

    Args:
        domain: The application backed up.
        backup_id: The new backup's id.
        ctx: The context.
        size_bytes: Its size, when known.
        destinations: The remote destinations it was uploaded to.

    Returns:
        The notification, under ``backup_success`` (off by default).
    """
    code = "backup.completed"
    facts = [_fact("backup", backup_id, ctx, mono=True)]
    if size_bytes is not None:
        facts.append(_fact("size", format_bytes(size_bytes), ctx))
    if destinations:
        facts.append(_fact("destinations", ", ".join(destinations), ctx))
    return build(
        ctx,
        kind="backup_success",
        code=code,
        state=State.OK,
        subject=domain,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        path="/backups",
        domain=domain,
    )


# -- the monitor --------------------------------------------------------------


def compose_certificate(
    name: str,
    domains: Sequence[str],
    expiry: str,
    days_left: int,
    ctx: NotificationContext,
) -> Notification:
    """
    Compose the notification for a certificate that expires, or already did.

    Args:
        name: The certificate's name.
        domains: The names it covers.
        expiry: Its expiry date, ISO ``YYYY-MM-DD``.
        days_left: Whole days until it expires; negative once it has.
        ctx: The context.

    Returns:
        ``cert.expiring`` (a warning) or, for a negative ``days_left``,
        ``cert.expired`` (a failure): both under the ``cert_expiring`` switch.
    """
    when = format_date(expiry, ctx.locale)
    if days_left < 0:
        code, state = "cert.expired", State.FAILED
        summary = message(
            "summary.cert.expired",
            ctx.locale,
            days=-days_left,
            unit=plural("day", ctx.locale, -days_left),
            date=when,
        )
    else:
        code, state = "cert.expiring", State.WARNING
        if days_left == 0:
            summary = message("summary.cert.expiring_today", ctx.locale, date=when)
        else:
            summary = message(
                "summary.cert.expiring",
                ctx.locale,
                days=days_left,
                unit=plural("day", ctx.locale, days_left),
                date=when,
            )
    covers = ", ".join(domains)
    facts = [_fact("covers", covers, ctx)] if covers and covers != name else []
    return build(
        ctx,
        kind="cert_expiring",
        code=code,
        state=state,
        subject=name,
        summary=summary,
        facts=facts,
        command=_fact("renew", f"noust cert renew {name}", ctx, mono=True),
        path="/domains",
    )


def compose_unit_failure(
    failure: str,
    unit: str,
    ctx: NotificationContext,
    *,
    result: str = "",
    exit_status: int | str | None = None,
    restarts: int | None = None,
    restarts_grew: int = 0,
    journal: str | None = None,
    domain: str | None = None,
) -> Notification:
    """
    Compose the notification for a systemd unit that failed.

    Args:
        failure: ``failed``, ``crash_loop`` or ``stopped_on_failure``.
        unit: The unit's name.
        ctx: The context.
        result: systemd's ``Result`` for its last run, verbatim.
        exit_status: The main process's exit status, when systemd gave one.
        restarts: How many automatic restarts in total.
        restarts_grew: How many since the previous check.
        journal: The unit's last journal lines, when they could be read.
        domain: The application the unit serves, when known; then it is the
            subject and the unit a fact.

    Returns:
        The notification, under the ``unit_failed`` switch.
    """
    code = f"unit.{failure}"
    summary = message(
        f"summary.{code}",
        ctx.locale,
        count=restarts_grew,
        times=plural("time", ctx.locale, restarts_grew),
    )
    facts: list[Fact] = []
    if domain:
        facts.append(_fact("unit", unit, ctx, mono=True))
    if restarts is not None:
        facts.append(_fact("restarts", str(restarts), ctx))
    command = _fact("inspect", f"journalctl -u {unit} -n 50", ctx, mono=True)
    excerpt = None
    if journal and journal.strip():
        excerpt = make_excerpt(
            journal,
            label=message("excerpt.journal", ctx.locale, unit=unit),
            avoid=[summary, command.value, *(fact.value for fact in facts)],
            pin_error=True,
        )
    if excerpt is None:
        # What systemd reported about the last run is what the journal, when
        # there is one, already says: a fact the excerpt carries is left out
        # (design rule D-7), so it is only stated when nothing else does.
        if result:
            facts.append(_fact("result", result, ctx, mono=True))
        if exit_status not in (None, "", 0, "0"):
            facts.append(_fact("exit_status", str(exit_status), ctx))
    return build(
        ctx,
        kind="unit_failed",
        code=code,
        state=State.FAILED,
        subject=domain or unit,
        summary=summary,
        facts=facts,
        command=command,
        excerpt=excerpt,
        path=f"/server/services/{unit}",
        domain=domain,
    )


def compose_unit_recovered(unit: str, ctx: NotificationContext) -> Notification:
    """
    Compose the notification that closes a unit's failure.

    Args:
        unit: The unit that is running again.
        ctx: The context.

    Returns:
        The notification, under the ``unit_failed`` switch that opened it.
    """
    code = "unit.recovered"
    return build(
        ctx,
        kind="unit_failed",
        code=code,
        state=State.OK,
        subject=unit,
        summary=message(f"summary.{code}", ctx.locale),
        path=f"/server/services/{unit}",
    )


def compose_app_unreachable(
    domain: str,
    ctx: NotificationContext,
    *,
    since: datetime | None = None,
    probe: str = "",
    containers: bool = False,
) -> Notification:
    """
    Compose the notification for an application whose unit runs but does not answer.

    The monitor sends it after several failed probes in a row, so a restart or
    a deploy never raises it; ``unit_failed`` stays the event for systemd
    giving a unit up.

    Args:
        domain: The application (the subject).
        ctx: The context.
        since: When the first of the failed probes happened.
        probe: The last probe, verbatim: the request and what came back, or,
            for a stack with no published port, one line per container that
            is not fine.
        containers: Whether the application was judged by its containers
            (a Compose stack that publishes no port) rather than by an HTTP
            probe.

    Returns:
        The notification, under the ``app_unreachable`` switch.
    """
    code = "app.unreachable"
    summary_key = "summary.app.unreachable_containers" if containers else f"summary.{code}"
    facts = [_fact("since", format_utc(since), ctx)] if since is not None else []
    return build(
        ctx,
        kind="app_unreachable",
        code=code,
        state=State.FAILED,
        subject=domain,
        summary=message(summary_key, ctx.locale),
        facts=facts,
        command=_fact("diagnose", f"noust diagnose {domain}", ctx, mono=True),
        excerpt=(
            make_excerpt(probe, label=message("excerpt.probe", ctx.locale)) if probe else None
        ),
        path=f"/apps/{domain}",
        domain=domain,
    )


def compose_app_recovered(
    domain: str,
    ctx: NotificationContext,
    *,
    down_for_s: float | None = None,
    containers: bool = False,
) -> Notification:
    """
    Compose the notification that closes an application's unreachable alert.

    Args:
        domain: The application (the subject).
        ctx: The context.
        down_for_s: How long it did not answer, in seconds.
        containers: Whether it is a stack judged by its containers.

    Returns:
        The notification, under the ``app_recovered`` switch.
    """
    code = "app.recovered"
    summary_key = "summary.app.recovered_containers" if containers else f"summary.{code}"
    facts = [_fact("downtime", format_duration(down_for_s), ctx)] if down_for_s is not None else []
    return build(
        ctx,
        kind="app_recovered",
        code=code,
        state=State.OK,
        subject=domain,
        summary=message(summary_key, ctx.locale),
        facts=facts,
        path=f"/apps/{domain}",
        domain=domain,
    )


def _used_value(percent: float, used_bytes: int | None, total_bytes: int | None) -> str:
    """
    Args:
        percent: How full it is.
        used_bytes: Bytes in use, when known.
        total_bytes: Size of the filesystem, when known.

    Returns:
        ``91.7% (18.3 GB / 20.0 GB)``, or just the percentage.
    """
    value = f"{percent:.1f}%"
    if used_bytes is not None and total_bytes is not None:
        value += f" ({format_bytes(used_bytes)} / {format_bytes(total_bytes)})"
    return value


def compose_disk(
    mountpoint: str,
    percent: float,
    threshold: float,
    ctx: NotificationContext,
    *,
    used_bytes: int | None = None,
    total_bytes: int | None = None,
    free_bytes: int | None = None,
) -> Notification:
    """
    Compose the notification for a filesystem that crossed the alert line.

    Args:
        mountpoint: Where it is mounted.
        percent: How full it is.
        threshold: The alert threshold, in percent.
        ctx: The context.
        used_bytes: Bytes in use, when known.
        total_bytes: Size of the filesystem, when known.
        free_bytes: Bytes available, when known.

    Returns:
        The notification, under the ``disk_threshold`` switch.
    """
    code = "disk.threshold"
    facts = [_fact("used", _used_value(percent, used_bytes, total_bytes), ctx)]
    if free_bytes is not None:
        facts.append(_fact("free", format_bytes(free_bytes), ctx))
    return build(
        ctx,
        kind="disk_threshold",
        code=code,
        state=State.WARNING,
        subject=mountpoint,
        summary=message(f"summary.{code}", ctx.locale, threshold=f"{threshold:.0f}"),
        facts=facts,
        path="/server",
    )


def compose_disk_recovered(
    mountpoint: str, percent: float, ctx: NotificationContext
) -> Notification:
    """
    Compose the notification that closes a disk alert.

    Args:
        mountpoint: Where it is mounted.
        percent: How full it is now.
        ctx: The context.

    Returns:
        The notification, under the ``disk_threshold`` switch that opened it.
    """
    code = "disk.recovered"
    return build(
        ctx,
        kind="disk_threshold",
        code=code,
        state=State.OK,
        subject=mountpoint,
        summary=message(f"summary.{code}", ctx.locale),
        facts=[_fact("used", f"{percent:.1f}%", ctx)],
        path="/server",
    )


def compose_observations(
    sections: Sequence[Section],
    *,
    processes: int,
    warnings: int,
    ctx: NotificationContext,
) -> Notification:
    """
    Compose the monitor's process-observation report.

    Args:
        sections: One block per observed process.
        processes: How many processes were observed.
        warnings: How many of them are warnings.
        ctx: The context.

    Returns:
        The report; it travels by email only.
    """
    code = "report.observations"
    return build(
        ctx,
        kind=REPORT_KIND,
        code=code,
        state=State.WARNING if warnings else State.INFO,
        subject="",
        summary=message(f"summary.{code}", ctx.locale),
        facts=[
            _fact("processes", str(processes), ctx),
            _fact("warnings", str(warnings), ctx),
        ],
        sections=sections,
    )


# -- the settings page's test button ------------------------------------------


def compose_test(channel: str, ctx: NotificationContext) -> Notification:
    """
    Compose the message the "send a test" button sends.

    Args:
        channel: The channel's name (``telegram``); its display name comes
            from the catalog, with its own capitals.
        ctx: The context.

    Returns:
        The notification. Never filtered by an event switch.
    """
    try:
        name = message(f"channel.{channel}", ctx.locale)
    except KeyError:
        name = channel
    return build(
        ctx,
        kind=TEST_KIND,
        code="test",
        state=State.INFO,
        subject=name,
        summary=message("summary.test", ctx.locale, channel=name),
    )


# -- a central and the servers it manages -------------------------------------


def compose_node_unreachable(
    node: str,
    ctx: NotificationContext,
    *,
    reason: str | None = None,
    address: str | None = None,
    since: datetime | None = None,
) -> Notification:
    """
    Compose the notification for a managed server the central cannot reach.

    Called by the fleet code, on the central, when a node's tunnel stays down.

    Args:
        node: The managed server's name (the subject).
        ctx: The context of the *central*: its name is the ``Server`` fact.
        reason: Why, in the tunnel's own words (ssh's stderr).
        address: Where the central tries to reach it.
        since: When it was last known to be up.

    Returns:
        The notification, under the ``node_unreachable`` switch.
    """
    code = "node.unreachable"
    facts: list[Fact] = []
    if address:
        facts.append(_fact("address", address, ctx, mono=True))
    if since is not None:
        facts.append(_fact("since", format_utc(since), ctx))
    return build(
        ctx,
        kind="node_unreachable",
        code=code,
        state=State.FAILED,
        subject=node,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        excerpt=(
            make_excerpt(reason, label=message("excerpt.output", ctx.locale)) if reason else None
        ),
        path="/fleet",
    )


def compose_node_recovered(
    node: str, ctx: NotificationContext, *, down_for_s: float | None = None
) -> Notification:
    """
    Compose the notification that closes a node's unreachable alert.

    Args:
        node: The managed server's name.
        ctx: The context of the central.
        down_for_s: How long it was unreachable, in seconds.

    Returns:
        The notification, under the ``node_recovered`` switch.
    """
    code = "node.recovered"
    facts = [_fact("downtime", format_duration(down_for_s), ctx)] if down_for_s is not None else []
    return build(
        ctx,
        kind="node_recovered",
        code=code,
        state=State.OK,
        subject=node,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        # Reachable again, so its own console is worth opening.
        path="/",
        node=node,
    )


def compose_node_host_key_changed(
    node: str,
    ctx: NotificationContext,
    *,
    address: str | None = None,
    pinned: str | None = None,
    presented: str | None = None,
    command: str | None = None,
) -> Notification:
    """
    Compose the notification for a managed server whose SSH host key changed.

    The tunnel is closed and stays closed: a changed host key is never
    accepted silently.

    Args:
        node: The managed server's name.
        ctx: The context of the central.
        address: Where the central reached it.
        pinned: The fingerprint pinned when the server was added.
        presented: The fingerprint it presented now.
        command: The command that checks and, when it is right, accepts the
            new key; the message says nothing about one when it is None.

    Returns:
        The notification, under the ``node_host_key_changed`` switch.
    """
    code = "node.host_key_changed"
    facts: list[Fact] = []
    if address:
        facts.append(_fact("address", address, ctx, mono=True))
    if pinned:
        facts.append(_fact("pinned", pinned, ctx, mono=True))
    if presented:
        facts.append(_fact("presented", presented, ctx, mono=True))
    return build(
        ctx,
        kind="node_host_key_changed",
        code=code,
        state=State.FAILED,
        subject=node,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        command=_fact("verify", command, ctx, mono=True) if command else None,
        path="/fleet",
    )


# -- the server itself ---------------------------------------------------------


def compose_server_rebooted(
    ctx: NotificationContext, *, returned_at: datetime | None = None
) -> Notification:
    """
    Compose the notification for a server that restarted when nobody asked from Noust.

    Sent by the console when it starts on a new boot that no reboot scheduled
    from Noust explains: the provider, a kernel panic, ``reboot`` typed in a
    terminal. Everything may have come back, or not; the operator looks.

    Args:
        ctx: The context; its server is the subject.
        returned_at: When the console noticed it was back.

    Returns:
        The notification, a warning under the ``server_rebooted`` switch.
    """
    code = "server.rebooted"
    facts = [_fact("returned", format_utc(returned_at), ctx)] if returned_at is not None else []
    return build(
        ctx,
        kind="server_rebooted",
        code=code,
        state=State.WARNING,
        subject=ctx.server,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        path="/server",
    )


def compose_server_back(
    ctx: NotificationContext,
    *,
    requested_by: str | None = None,
    scheduled_for: datetime | None = None,
    took_s: float | None = None,
    returned_at: datetime | None = None,
) -> Notification:
    """
    Compose the notification that a reboot asked from Noust is done.

    Args:
        ctx: The context; its server is the subject.
        requested_by: Who scheduled it.
        scheduled_for: When it was due.
        took_s: From the moment it was due to the console running again.
        returned_at: When the console noticed it was back.

    Returns:
        The notification, under the ``server_back`` switch.
    """
    code = "server.back"
    facts: list[Fact] = []
    if requested_by:
        facts.append(_fact("requested_by", requested_by, ctx))
    if scheduled_for is not None:
        facts.append(_fact("scheduled", format_utc(scheduled_for), ctx))
    if took_s is not None:
        facts.append(_fact("took", format_duration(took_s), ctx))
    if returned_at is not None:
        facts.append(_fact("returned", format_utc(returned_at), ctx))
    return build(
        ctx,
        kind="server_back",
        code=code,
        state=State.OK,
        subject=ctx.server,
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        path="/server",
    )


# -- four-eyes approvals --------------------------------------------------------

#: Where approvals are decided in the console.
APPROVALS_PAGE = "/settings/security"


def _approval_facts(
    ctx: NotificationContext, approval_id: int, action: str, call: str, requester: str
) -> list[Fact]:
    """
    Args:
        ctx: The context.
        approval_id: The request's number.
        action: Its rule's stable name (``fleet.node.add``).
        call: ``METHOD /path``.
        requester: Who asked.

    Returns:
        The facts every approval notification opens with.
    """
    return [
        _fact("request", f"#{approval_id}", ctx),
        _fact("action", action, ctx, mono=True),
        _fact("call", call, ctx, mono=True),
        _fact("requested_by", requester, ctx),
    ]


def compose_approval_requested(
    ctx: NotificationContext,
    *,
    approval_id: int,
    action: str,
    call: str,
    requester: str,
    reason: str | None = None,
    expires_at: datetime | None = None,
) -> Notification:
    """
    Compose the notification for a change waiting for a second person.

    Args:
        ctx: The context.
        approval_id: The request's number.
        action: Its rule's stable name.
        call: ``METHOD /path`` of the call it would allow.
        requester: Who asked.
        reason: Why, in their words.
        expires_at: When it expires undecided.

    Returns:
        The notification, under the ``approval_requested`` switch.
    """
    code = "approval.requested"
    facts = _approval_facts(ctx, approval_id, action, call, requester)
    if reason:
        facts.append(_fact("reason", reason, ctx))
    if expires_at is not None:
        facts.append(_fact("decide_before", format_utc(expires_at), ctx))
    return build(
        ctx,
        kind="approval_requested",
        code=code,
        state=State.PROGRESS,
        subject=f"#{approval_id}",
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        command=_fact("decide", f"noust approval approve {approval_id}", ctx, mono=True),
        path=APPROVALS_PAGE,
    )


def compose_approval_decided(
    ctx: NotificationContext,
    *,
    approval_id: int,
    approved: bool,
    action: str,
    call: str,
    requester: str,
    decider: str,
    comment: str | None = None,
    use_before: datetime | None = None,
) -> Notification:
    """
    Compose the notification for a decided request, for the person who asked.

    Args:
        ctx: The context.
        approval_id: The request's number.
        approved: Approved rather than rejected.
        action: Its rule's stable name.
        call: ``METHOD /path``.
        requester: Who asked.
        decider: Who decided.
        comment: What the decider said.
        use_before: Until when an approval allows the call.

    Returns:
        The notification, under the ``approval_decided`` switch.
    """
    code = "approval.approved" if approved else "approval.rejected"
    facts = _approval_facts(ctx, approval_id, action, call, requester)
    facts.append(_fact("decided_by", decider, ctx))
    if comment:
        facts.append(_fact("comment", comment, ctx))
    if approved and use_before is not None:
        facts.append(_fact("use_before", format_utc(use_before), ctx))
    return build(
        ctx,
        kind="approval_decided",
        code=code,
        state=State.OK if approved else State.WARNING,
        subject=f"#{approval_id}",
        summary=message(f"summary.{code}", ctx.locale),
        facts=facts,
        path=APPROVALS_PAGE,
    )


__all__ = [
    "OPERATIONS",
    "PreviewOf",
    "build",
    "compose_app_recovered",
    "compose_app_unreachable",
    "compose_approval_decided",
    "compose_approval_requested",
    "compose_backup_completed",
    "compose_backup_failed",
    "compose_backup_schedule_missing",
    "compose_backup_upload_failed",
    "compose_certificate",
    "compose_deploy",
    "compose_disk",
    "compose_disk_recovered",
    "compose_node_host_key_changed",
    "compose_node_recovered",
    "compose_node_unreachable",
    "compose_observations",
    "compose_restore",
    "compose_server_back",
    "compose_server_rebooted",
    "compose_test",
    "compose_unit_failure",
    "compose_unit_recovered",
    "format_bytes",
    "format_date",
    "format_duration",
    "format_utc",
    "server_fact",
]
