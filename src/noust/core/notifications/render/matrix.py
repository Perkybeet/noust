# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as a Matrix room message (checklist K-10).

``PUT /_matrix/client/v3/rooms/{roomId}/send/m.room.message/{txnId}`` takes an
``m.room.message`` event. This writes an ``m.text`` with both bodies the spec
asks of a rich message: ``body`` is the plain text every client can show (the
same layout as the email's text part and the webhook's ``body``), and
``formatted_body`` is the subset of HTML clients agree on (``b``, ``code``,
``pre``, ``ul``, ``li``, ``a`` and ``br``).

``m.text`` and not ``m.notice``: clients treat a notice as something a bot said
that nobody needs to hear, which is the opposite of an alert.

Every value is escaped as HTML, and the event says explicitly that it mentions
nobody (``m.mentions: {}``, Matrix 1.7), so a branch named ``@room`` is not a
ping on a client that honours the field; for the older rule that matches the
text, an at sign before a word gets a zero-width space.
"""

from __future__ import annotations

import html
import re
from dataclasses import replace
from typing import Any

from noust.core.notifications.model import Excerpt, Fact, Notification
from noust.core.notifications.render.common import (
    clip,
    fit,
    http_url,
    omitted_marker,
    render_text,
    with_pinned_marker,
)

#: The size of an event is bounded at 65 KB by the protocol; the text and the
#: HTML together stay far under it.
BODY_BUDGET = 20_000
EXCERPT_LINES = 15
EXCERPT_CHARS = 2_000

_ZWSP = "​"
_MENTION = re.compile(r"@(?=[\w#!])")


def safe_text(text: str) -> str:
    """
    Args:
        text: Text from anywhere.

    Returns:
        It with a zero-width space after an at sign that starts a word, so the
        old push rules that look for ``@room`` or a user id in the text find
        nothing.
    """
    return _MENTION.sub("@" + _ZWSP, text)


def _e(text: str) -> str:
    """
    Args:
        text: Text from anywhere.

    Returns:
        It escaped for HTML, and safe from a mention.
    """
    return html.escape(safe_text(text), quote=True)


def _fact_html(fact: Fact, cap: int) -> str:
    """
    Args:
        fact: A labelled value.
        cap: The longest its value may be.

    Returns:
        A list item.
    """
    value = _e(clip(fact.value, cap))
    shown = f"<code>{value}</code>" if fact.mono else value
    return f"<li><b>{_e(clip(fact.label, 40))}:</b> {shown}</li>"


def _build(
    n: Notification, excerpt: Excerpt | None, facts: tuple[Fact, ...], cap: int
) -> tuple[str, str]:
    """
    Build both bodies.

    Args:
        n: The notification.
        excerpt: The excerpt to show, or None.
        facts: The facts to show, the server first.
        cap: The longest a fact's value may be.

    Returns:
        ``(plain text, formatted HTML)``.
    """
    shaped = replace(
        n,
        facts=tuple(replace(fact, value=clip(fact.value, cap)) for fact in facts),
        excerpt=excerpt,
    )
    body = safe_text(render_text(shaped))
    parts = [f"<b>{_e(n.glyph)} {_e(n.headline)}</b>"]
    if n.summary:
        parts.append(f"<br>{_e(n.summary)}")
    rows = [_fact_html(fact, cap) for fact in facts if fact.value]
    if n.command and n.command.value:
        rows.append(_fact_html(Fact("command", n.command.label, n.command.value, True), cap))
    if rows:
        parts.append("<ul>" + "".join(rows) + "</ul>")
    for section in n.sections:
        parts.append(f"<b>{_e(clip(section.heading, 200))}</b>")
        section_rows = [_fact_html(fact, cap) for fact in section.rows if fact.value]
        if section_rows:
            parts.append("<ul>" + "".join(section_rows) + "</ul>")
    if excerpt is not None and excerpt.lines:
        lines = [_e(line) for line in with_pinned_marker(list(excerpt.lines), excerpt, n)]
        marker = omitted_marker(excerpt, n)
        if marker:
            lines.append(_e(f"… {marker}"))
        label = f"<i>{_e(clip(excerpt.label, 200))}</i>" if excerpt.label else ""
        parts.append(f"{label}<pre><code>" + "\n".join(lines) + "</code></pre>")
    link = n.console_link
    if link is not None and http_url(link.url):
        parts.append(
            f'<a href="{html.escape(link.url, quote=True)}">{_e(clip(link.label, 60))}</a>'
        )
    return body, "".join(parts)


def render(n: Notification) -> dict[str, Any]:
    """
    Render a notification as the content of an ``m.room.message`` event.

    Args:
        n: The notification.

    Returns:
        The JSON body of the ``send`` request.
    """
    body, formatted = fit(
        n,
        lambda excerpt, facts, cap: _build(n, excerpt, facts, cap),
        lambda pair: len(pair[0]) + len(pair[1]),
        BODY_BUDGET,
        excerpt_lines=EXCERPT_LINES,
        excerpt_chars=EXCERPT_CHARS,
    )
    return {
        "msgtype": "m.text",
        "body": body,
        "format": "org.matrix.custom.html",
        "formatted_body": formatted,
        "m.mentions": {},
    }
