# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What every renderer needs and none of them should write twice.

A renderer is a pure function from a :class:`~noust.core.notifications.model.Notification`
to what one channel receives. The pieces here are the ones the channels share:
the plain-text layout (the email's text part, Telegram's plain retry, the
webhook's legacy ``body``), the marker for omitted lines, a bound on any text,
and the one test for a URL a renderer may link to.

Escaping is not shared, on purpose: each channel's grammar has its own
metacharacters, and the escape for them lives in that channel's renderer, at
the boundary where its markup is written, so a value never reaches a channel's
syntax unescaped by way of another channel's helper.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TypeVar
from urllib.parse import urlparse

from noust.core.messages import message
from noust.core.notifications.excerpt import trim_excerpt
from noust.core.notifications.formatting import format_utc
from noust.core.notifications.model import Excerpt, Fact, Notification

_ELLIPSIS = "…"

T = TypeVar("T")

#: How far a chat message's fact values are cut, from generous to tight, before
#: whole facts are dropped: a hostile or absurd value can never push out the
#: header, the server, or the link.
VALUE_CAPS = (300, 120, 60)


def clip(text: str, limit: int) -> str:
    """
    Bound a text.

    Args:
        text: Any text.
        limit: The most characters it may have, ellipsis included.

    Returns:
        The text, or its start and an ellipsis.
    """
    if limit <= 0:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + _ELLIPSIS


def http_url(url: str) -> str | None:
    """
    Accept only a URL a client may safely turn into a link.

    Args:
        url: A URL.

    Returns:
        The URL when it is absolute http(s) without control characters or
        spaces; None otherwise, so no channel ever links to ``javascript:``.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return None
    return url


def omitted_marker(excerpt: Excerpt, notification: Notification) -> str:
    """
    Say how many earlier lines an excerpt leaves out.

    Args:
        excerpt: The excerpt.
        notification: The notification it belongs to, for the language.

    Returns:
        ``35 earlier lines omitted`` in the reader's language, or an empty
        string when nothing was left out.
    """
    if excerpt.omitted <= 0:
        return ""
    key = "ui.omitted.one" if excerpt.omitted == 1 else "ui.omitted.other"
    return message(key, notification.locale, count=excerpt.omitted)


def with_pinned_marker(lines: list[str], excerpt: Excerpt, notification: Notification) -> list[str]:
    """
    Mark where an excerpt's first error ends and the end of the output begins.

    Args:
        lines: The excerpt's lines as the channel shows them (escaped, or
            without the journal's prefix), one for each of ``excerpt.lines``.
        excerpt: The excerpt.
        notification: The notification it belongs to, for the language.

    Returns:
        The lines, with ``… First error above; the last lines follow`` in the
        reader's language after the pinned ones (:attr:`Excerpt.pinned`), when
        lines follow them. The marker is Noust's, so it is plain text that
        each renderer escapes like the lines; nothing it points at changes.
    """
    if not 0 < excerpt.pinned < len(lines):
        return lines
    marker = f"\u2026 {message('ui.first_error', notification.locale)}"
    return [*lines[: excerpt.pinned], marker, *lines[excerpt.pinned :]]


def detail_facts(notification: Notification) -> tuple[Fact, ...]:
    """
    Args:
        notification: The notification.

    Returns:
        Its facts, then its command: the labelled lines every channel shows
        after the summary.
    """
    facts = (
        (*notification.facts, notification.command) if notification.command else notification.facts
    )
    return tuple(fact for fact in facts if fact.value)


def render_text(notification: Notification, *, footer: bool = False) -> str:
    """
    Lay a notification out as plain text.

    The email's text part, Telegram's retry when its markup is refused and the
    webhook's legacy ``body`` are this, so a reader with no markup sees the
    same message in the same order.

    Args:
        notification: The notification.
        footer: Add the email footer: who sent it, when, and where to change
            what is received.

    Returns:
        The text; lines separated by ``\\n``, no trailing newline.
    """
    n = notification
    lines: list[str] = [f"{n.glyph} {n.headline}"]
    if n.summary:
        lines.append(n.summary)
    facts = detail_facts(n)
    if facts:
        lines.append("")
        lines.extend(f"{fact.label}: {fact.value}" for fact in facts)
    for section in n.sections:
        lines.extend(["", section.heading])
        lines.extend(f"  {fact.label}: {fact.value}" for fact in section.rows)
    if n.excerpt is not None and n.excerpt.lines:
        lines.extend(["", f"{n.excerpt.label}:"] if n.excerpt.label else [""])
        lines.extend(
            f"  {line}" for line in with_pinned_marker(list(n.excerpt.lines), n.excerpt, n)
        )
        marker = omitted_marker(n.excerpt, n)
        if marker:
            lines.append(f"  {marker}")
    link = n.console_link
    if link is not None and http_url(link.url):
        lines.extend(["", f"{link.label}: {link.url}"])
    if footer:
        lines.extend(["", "-- ", footer_line(n), message("ui.change_settings", n.locale)])
    return "\n".join(lines)


def footer_line(notification: Notification) -> str:
    """
    Args:
        notification: The notification.

    Returns:
        ``Sent by Noust · web-1 · 2026-09-29 10:45 UTC``.
    """
    return (
        f"{message('ui.sent_by', notification.locale)} · {notification.server}"
        f" · {format_utc(notification.ts)}"
    )


def fit(
    notification: Notification,
    build: Callable[[Excerpt | None, tuple[Fact, ...], int], T],
    size: Callable[[T], int],
    limit: int,
    *,
    excerpt_lines: int,
    excerpt_chars: int,
) -> T:
    """
    Build a message that fits a channel's limit, giving way in a fixed order.

    The header, the server and the link are not negotiable; what gives is, in
    order: the oldest lines of the excerpt (all of them, if it comes to that),
    then the length of every fact's value, then the last facts. A message that
    is too long is therefore always shorter in the least important part, never
    cut at the end - where the link used to be.

    Args:
        notification: What to render.
        build: Makes the message from an excerpt (or None), the facts to
            show (the server first) and the longest a fact's value may be.
        size: Measures a message the way the channel's limit does.
        limit: The channel's limit, with whatever margin the caller wants.
        excerpt_lines: The most excerpt lines this channel shows at all.
        excerpt_chars: The most excerpt characters this channel shows at all.

    Returns:
        The message.
    """
    excerpt = trim_excerpt(notification.excerpt, max_lines=excerpt_lines, max_chars=excerpt_chars)
    facts = tuple(fact for fact in notification.facts if fact.value)
    for cap in VALUE_CAPS:
        current = excerpt
        while True:
            message_ = build(current, facts, cap)
            if size(message_) <= limit:
                return message_
            if current is None:
                break
            current = trim_excerpt(current, max_lines=len(current.lines) - 1, max_chars=10**9)
    tail = VALUE_CAPS[-1]
    while len(facts) > 1:
        facts = facts[:-1]
        message_ = build(None, facts, tail)
        if size(message_) <= limit:
            return message_
    return build(None, facts, tail)


def with_lines(excerpt: Excerpt, lines: tuple[str, ...]) -> Excerpt:
    """
    Args:
        excerpt: An excerpt.
        lines: The lines to show instead of its own.

    Returns:
        The same excerpt showing those lines.
    """
    return replace(excerpt, lines=lines)
