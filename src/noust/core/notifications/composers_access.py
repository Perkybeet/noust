# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The composer of the notification about a change to how the server is reached that was undone (item 138).

A change to the firewall or to SSH is applied with a timer that undoes it unless somebody confirms it
from a new session. When the timer does, an audit entry is all there was; this says it through the
channels, in the same shape as every other event: one sentence, the server first, what the undo
printed verbatim in an excerpt and the console page where the change is made again.

Pure: whoever undid the change hands it the facts.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from noust.core.access_changes import describe_record
from noust.core.messages import message, plural
from noust.core.notifications.composers import _fact, build
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.excerpt import make_excerpt
from noust.core.notifications.formatting import format_utc
from noust.core.notifications.model import Fact, Notification, State

#: The kind of change, as the ledger names it, and the code and console page of each.
_KINDS: dict[str, tuple[str, str]] = {
    "firewall": ("access.firewall_reverted", "/network/firewall"),
    "sshd": ("access.ssh_reverted", "/security/ssh"),
}


def compose_access_change_reverted(
    kind: str,
    change: str,
    ctx: NotificationContext,
    *,
    actor: str,
    applied_at: float,
    window_s: float,
    output: str = "",
    what: str = "",
    values: Mapping[str, str] | None = None,
) -> Notification:
    """
    Compose the notification for a change nobody confirmed in time, which the timer undid.

    Args:
        kind: ``firewall`` or ``sshd``, as the change ledger names the kind.
        change: What was changed, in the words the change was recorded with: said as it is, in
            quotes, only when ``what`` is empty or not a sentence this version knows.
        ctx: The context.
        actor: Who applied it.
        applied_at: When it was applied, epoch seconds.
        window_s: How long it waited for a confirmation, in seconds.
        output: What undoing it printed, verbatim; empty when it printed nothing.
        what: The code of the sentence that says what the change was
            (:mod:`noust.core.access_changes`), kept by the ledger; empty for a change recorded
            before it did.
        values: What that sentence puts in.

    Returns:
        ``access.firewall_reverted`` or ``access.ssh_reverted``, a warning, under
        ``access_change_reverted``.

    Raises:
        KeyError: ``kind`` is not one the ledger records.
    """
    code, path = _KINDS[kind]
    minutes = max(1, round(window_s / 60))
    # Said in the reader's language from what the change is, not pasted from the sentence somebody
    # wrote when it was made, which is English in a Spanish notice.
    said = describe_record(what, values or {}, change, ctx.locale)
    facts: list[Fact] = [
        _fact("change", said, ctx),
        _fact("change_by", actor, ctx),
        _fact("change_at", format_utc(datetime.fromtimestamp(applied_at, tz=timezone.utc)), ctx),
    ]
    excerpt = (
        make_excerpt(output, label=message("excerpt.access.undo", ctx.locale))
        if output.strip()
        else None
    )
    return build(
        ctx,
        kind="access_change_reverted",
        code=code,
        state=State.WARNING,
        subject=said,
        summary=message(
            f"summary.{code}",
            ctx.locale,
            minutes=minutes,
            unit=plural("minute", ctx.locale, minutes),
        ),
        facts=facts,
        excerpt=excerpt,
        path=path,
    )
