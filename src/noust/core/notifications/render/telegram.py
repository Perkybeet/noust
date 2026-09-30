# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as a Telegram Bot API ``sendMessage`` body.

``parse_mode`` is HTML: it needs three characters escaped (``<``, ``>``,
``&``), where MarkdownV2 needs eighteen, and one function here does it for
every piece of text that reaches the markup. The message is:

- a state line, glyph and bold word and the subject;
- the summary;
- the facts as ``Label: value``, the server first, mono values as ``code``;
- the excerpt in a ``pre`` block with an italic label;
- **the console link as an inline button**, not a line: it cannot be cut, and
  Telegram draws no link preview for a button.

The link preview is off (``link_preview_options``), and the message is silent
(``disable_notification``) unless the state is a warning or a failure: what
went right does not ring a phone.

Telegram counts the limit (4096) *after* entity parsing. :func:`render` stays
under it with a margin by giving way in a fixed order (:func:`common.fit`):
excerpt lines first, then fact values, and the button is not in the text at
all, so nothing can cut it.

If Telegram answers 400 ``can't parse entities`` the notifier sends
:func:`render` with ``plain=True`` once: the same message with no markup and
the link as a line, so an alert is never lost to a formatting quirk.
"""

from __future__ import annotations

import html
import re
from dataclasses import replace
from typing import Any

from noust.core.notifications.excerpt import strip_journal_prefix
from noust.core.notifications.model import Excerpt, Fact, Notification
from noust.core.notifications.render.common import (
    clip,
    fit,
    http_url,
    omitted_marker,
    render_text,
)

#: Telegram's own limit, characters after entity parsing.
TELEGRAM_LIMIT = 4096

#: What :func:`render` keeps under: a margin for whatever Telegram counts that
#: this module does not.
TEXT_BUDGET = 4000

#: A phone shows about this much of an excerpt; ``pre`` does not wrap.
EXCERPT_LINES = 8
EXCERPT_CHARS = 1000

_TAG = re.compile(r"<[^>]+>")


def escape(text: str) -> str:
    """
    Escape text for Telegram's HTML.

    Args:
        text: Text from anywhere.

    Returns:
        The text with ``&``, ``<`` and ``>`` as entities. Quotes stay: they
        are only special inside an attribute, and :func:`_attribute` handles
        that one.
    """
    return html.escape(text, quote=False)


def _attribute(text: str) -> str:
    """
    Args:
        text: A URL for an ``href``.

    Returns:
        The text safe inside a double-quoted attribute.
    """
    return html.escape(text, quote=True)


def _visible_length(text: str) -> int:
    """
    Args:
        text: Message HTML.

    Returns:
        How many characters Telegram counts: the text without tags, each
        entity as one character.
    """
    return len(html.unescape(_TAG.sub("", text)))


def _row(fact: Fact, cap: int) -> str:
    """
    Args:
        fact: A labelled value.
        cap: The longest its value may be.

    Returns:
        ``<b>Label:</b> value``, mono as ``code``.
    """
    value = escape(clip(fact.value, cap))
    value = f"<code>{value}</code>" if fact.mono else value
    return f"<b>{escape(clip(fact.label, 40))}:</b> {value}"


def _excerpt_block(excerpt: Excerpt, notification: Notification) -> list[str]:
    """
    Args:
        excerpt: The excerpt to show.
        notification: Its notification, for the language.

    Returns:
        The lines of the block: an italic label and a ``pre``.
    """
    lines = [escape(line) for line in strip_journal_prefix(excerpt.lines)]
    marker = omitted_marker(excerpt, notification)
    if marker:
        lines.append(escape(f"… {marker}"))
    label = [f"<i>{escape(clip(excerpt.label, 200))}</i>"] if excerpt.label else []
    return ["", *label, "<pre>" + "\n".join(lines) + "</pre>"]


def _html(n: Notification, excerpt: Excerpt | None, facts: tuple[Fact, ...], cap: int) -> str:
    """
    Build the message's HTML.

    Args:
        n: The notification.
        excerpt: The excerpt to show, or None.
        facts: The facts to show, the server first.
        cap: The longest a fact's value may be.

    Returns:
        The HTML text.
    """
    subject = f" · {escape(clip(n.subject, 200))}" if n.subject else ""
    parts = [f"{n.glyph} <b>{escape(clip(n.title, 100))}</b>{subject}"]
    if n.summary:
        parts.append(escape(clip(n.summary, 400)))
    rows = [*facts, *([n.command] if n.command and n.command.value else [])]
    if rows:
        parts.append("")
        parts.extend(_row(fact, cap) for fact in rows)
    for section in n.sections:
        parts.extend(["", f"<b>{escape(clip(section.heading, 200))}</b>"])
        parts.extend(_row(fact, cap) for fact in section.rows if fact.value)
    if excerpt is not None and excerpt.lines:
        parts.extend(_excerpt_block(excerpt, n))
    return "\n".join(parts)


def _plain(n: Notification, excerpt: Excerpt | None, facts: tuple[Fact, ...], cap: int) -> str:
    """
    Build the message as plain text, for the retry.

    Args:
        n: The notification.
        excerpt: The excerpt to show, or None.
        facts: The facts to show.
        cap: The longest a fact's value may be.

    Returns:
        The text, the console link as its last line.
    """
    shaped = replace(
        n,
        subject=clip(n.subject, 200),
        facts=tuple(replace(fact, value=clip(fact.value, cap)) for fact in facts),
        excerpt=excerpt,
    )
    return render_text(shaped)


def render(n: Notification, chat_id: str, *, plain: bool = False) -> dict[str, Any]:
    """
    Render a notification as a ``sendMessage`` body.

    Args:
        n: The notification.
        chat_id: The destination chat, already validated.
        plain: Render without markup, for the retry after Telegram refused
            the entities.

    Returns:
        The JSON body.
    """
    build = _plain if plain else _html
    size = len if plain else _visible_length
    text = fit(
        n,
        lambda excerpt, facts, cap: build(n, excerpt, facts, cap),
        size,
        TEXT_BUDGET,
        excerpt_lines=EXCERPT_LINES,
        excerpt_chars=EXCERPT_CHARS,
    )
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "link_preview_options": {"is_disabled": True},
        "disable_notification": not n.loud,
    }
    if not plain:
        payload["parse_mode"] = "HTML"
        link = n.console_link
        if link is not None and http_url(link.url):
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": clip(link.label, 60), "url": link.url}]]
            }
    return payload
