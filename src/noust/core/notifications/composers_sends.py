# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The composers of the copies that could not be sent to a destination, and of their recovery (item 201).

One message for a destination, not one for each application. A night of bad network between a server
and its destination used to be one message for every copy that did not arrive, said twice; now it is
one that names the destination, how many copies wait and for which applications, what was tried and how
often, when it is tried next and where the copies are meanwhile, with what the destination said
verbatim. When everything has arrived another says so, as the answer to the first: the pair is one
thing to every channel that can group (:func:`noust.core.notifications.grouping.thing_key`, whose key
here is the destination).

Pure, like the rest of the package: the sender reads the store and hands these functions the facts.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from noust.core.messages import message, plural
from noust.core.notifications.composers import (
    _english,
    _error_excerpt,
    _fact,
    _whole_output,
    build,
)
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.formatting import format_utc
from noust.core.notifications.model import Notification, State

__all__ = [
    "MAX_OWNERS_LISTED",
    "compose_backup_sends_failed",
    "compose_backup_sends_recovered",
]

#: The applications a message names; the rest are counted.
MAX_OWNERS_LISTED = 5


def _copies(count: int, owners: Sequence[str], ctx: NotificationContext) -> str:
    """
    Args:
        count: How many copies.
        owners: What they are copies of, each once, in the order they were first owed.
        ctx: The context.

    Returns:
        ``9: shop.example.com, blog.example.com, ... and 4 more``.
    """
    shown = list(owners[:MAX_OWNERS_LISTED])
    text = f"{count}: {', '.join(shown)}" if shown else str(count)
    extra = len(owners) - len(shown)
    if extra > 0:
        text += f" {message('ui.and_more', ctx.locale, count=extra)}"
    return text


def compose_backup_sends_failed(
    destination: str,
    count: int,
    owners: Sequence[str],
    error: str | None,
    ctx: NotificationContext,
    *,
    tried: int,
    next_try: datetime | None = None,
    kept_in: str | None = None,
    failure_id: int | None = None,
) -> Notification:
    """
    Compose the notification for copies that could not be sent to a destination.

    Args:
        destination: The destination they are owed to.
        count: How many copies are owed to it.
        owners: What they are copies of, each once.
        error: What the destination said, ``message\\n  Details: ...``, verbatim.
        ctx: The context.
        tried: How many times the send of the oldest was attempted.
        next_try: When the next try is, when there is one.
        kept_in: The directory the copies are in meanwhile.
        failure_id: The record that keeps the whole output, when one was kept.

    Returns:
        The notification, a warning under ``backup_failed``: the copies are safe here, not there.
    """
    code = "backup.sends_failed"
    summary = message(f"summary.{code}", ctx.locale)
    facts = [
        _fact("destination", destination, ctx),
        _fact("copies", _copies(count, owners, ctx), ctx),
        _fact("tried", f"{tried} {plural('time', ctx.locale, tried)}", ctx),
    ]
    if next_try is not None:
        facts.append(_fact("next_try", format_utc(next_try), ctx))
    if kept_in:
        facts.append(_fact("kept_in", kept_in, ctx, mono=True))
    whole, links = _whole_output(failure_id, ctx)
    return build(
        ctx,
        kind="backup_failed",
        code=code,
        state=State.WARNING,
        subject=destination,
        summary=summary,
        facts=facts,
        excerpt=_error_excerpt(
            error,
            ctx,
            repeated=[_english(code, "title"), _english(code, "summary"), destination],
            avoid=[summary, destination],
        ),
        command=whole or _fact("send_now", "noust backup resend", ctx, mono=True),
        path="/backups",
        extra_links=links,
    )


def compose_backup_sends_recovered(
    destination: str, count: int, ctx: NotificationContext
) -> Notification:
    """
    Compose the notification that closes the one about copies that could not be sent.

    Args:
        destination: The destination.
        count: How many copies reached it since it was said they had not.
        ctx: The context.

    Returns:
        The notification, under the same switch as the failure and in the same family, so that a
        channel that can answer puts it as the reply.
    """
    code = "backup.sends_recovered"
    return build(
        ctx,
        kind="backup_failed",
        code=code,
        state=State.OK,
        subject=destination,
        summary=message(f"summary.{code}", ctx.locale),
        facts=[
            _fact("destination", destination, ctx),
            _fact("copies", str(count), ctx),
        ],
        path="/backups",
    )
