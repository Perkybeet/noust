# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as a Discord webhook body: one embed.

The embed's title is the state line (glyph, word, subject) and *is* the link
to the console (``url``), so the address is not written again. The colour bar
is the state's. The summary, the command and the excerpt (in a code block)
are the description; the facts are inline fields, the server first; the footer
is ``Noust · <server>`` and the timestamp is the event's.

The limits Discord enforces are one by one below, and one across the lot: title,
description, every field's name and value and the footer together stay under
6000 characters. :func:`render` gives way in a fixed order (excerpt lines,
then fact values, then facts) and never drops the link.

Two protections apply everywhere: ``allowed_mentions`` allows nothing, so not
even a role id in a branch name pings anyone, and ``@everyone`` and ``@here``
get a zero-width space so they do not even look like one. Untrusted values are
markdown-escaped with a backslash, which Discord does honour; an excerpt sits
in a code block whose own fence is replaced so its content cannot close it.
Quiet states (ok, progress, info) suppress the notification sound
(``flags: 4096``).
"""

from __future__ import annotations

import re
from typing import Any

from noust.core.notifications.model import STATE_COLORS, Excerpt, Fact, Notification
from noust.core.notifications.render.common import (
    clip,
    fit,
    http_url,
    omitted_marker,
    with_pinned_marker,
)

TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024
FIELD_COUNT_LIMIT = 25
FOOTER_LIMIT = 2048
#: Discord counts title, description, field names and values and the footer
#: together; this is that limit with a margin.
EMBED_BUDGET = 5800
#: The flag that sends the message without a notification.
SUPPRESS_NOTIFICATIONS = 1 << 12
EXCERPT_LINES = 12
EXCERPT_CHARS = 1200

_ZWSP = "\u200b"
_MASS_MENTION = re.compile(r"@(everyone|here)", re.IGNORECASE)
_MARKDOWN = re.compile(r"([\\*_~`|\[\]])")


def defuse(text: str) -> str:
    """
    Break Discord's mass mentions and mention syntax in text someone chose.

    Args:
        text: Text to send.

    Returns:
        The text with a zero-width space after the ``@`` of ``@everyone`` and
        ``@here``, and after every ``<`` (which opens ``<@id>``, ``<@&role>``,
        ``<#channel>``), so it reads the same and pings nobody.
    """
    return _MASS_MENTION.sub("@" + _ZWSP + r"\1", text).replace("<", "<" + _ZWSP)


def markdown(text: str) -> str:
    """
    Make untrusted text arrive as text in Discord's markdown.

    Args:
        text: A value someone else chose.

    Returns:
        The text with markdown characters backslash-escaped, a leading ``#``,
        ``>`` or ``-`` escaped so it is not a heading, quote or list, and
        mentions defused.
    """
    escaped = _MARKDOWN.sub(r"\\\1", text)
    if escaped[:1] in ("#", ">", "-"):
        escaped = "\\" + escaped
    return defuse(escaped)


def _code(text: str) -> str:
    """
    Args:
        text: A value shown in monospace.

    Returns:
        An inline code span its content cannot close.
    """
    return "`" + defuse(text).replace("`", "'") + "`"


def _field(fact: Fact, cap: int) -> dict[str, Any]:
    """
    Args:
        fact: A labelled value.
        cap: The longest its value may be.

    Returns:
        One inline field.
    """
    value = clip(fact.value, cap)
    shown = _code(value) if fact.mono else markdown(value)
    return {
        "name": clip(defuse(fact.label), FIELD_NAME_LIMIT),
        "value": clip(shown, FIELD_VALUE_LIMIT),
        "inline": True,
    }


def _description(n: Notification, excerpt: Excerpt | None) -> str:
    """
    Args:
        n: The notification.
        excerpt: The excerpt to show, or None.

    Returns:
        The summary, the command and the excerpt as Discord markdown.
    """
    parts: list[str] = []
    if n.summary:
        parts.append(markdown(clip(n.summary, 400)))
    if n.command and n.command.value:
        parts.append(
            f"**{defuse(clip(n.command.label, 40))}:** {_code(clip(n.command.value, 300))}"
        )
    for section in n.sections:
        rows = "\n".join(
            f"**{defuse(fact.label)}:** "
            + (_code(fact.value) if fact.mono else markdown(fact.value))
            for fact in section.rows
            if fact.value
        )
        parts.append(f"**{markdown(clip(section.heading, 200))}**\n{rows}")
    if excerpt is not None and excerpt.lines:
        shown = with_pinned_marker(list(excerpt.lines), excerpt, n)
        lines = [defuse(line).replace("```", "'''") for line in shown]
        marker = omitted_marker(excerpt, n)
        if marker:
            lines.append(f"… {marker}")
        label = f"*{markdown(clip(excerpt.label, 200))}*\n" if excerpt.label else ""
        parts.append(f"{label}```\n" + "\n".join(lines) + "\n```")
    return "\n\n".join(parts)


def _embed(
    n: Notification, excerpt: Excerpt | None, facts: tuple[Fact, ...], cap: int
) -> dict[str, Any]:
    """
    Build the embed.

    Args:
        n: The notification.
        excerpt: The excerpt to show, or None.
        facts: The facts to show, the server first.
        cap: The longest a fact's value may be.

    Returns:
        The embed.
    """
    embed: dict[str, Any] = {
        "title": clip(defuse(f"{n.glyph} {n.headline}"), TITLE_LIMIT),
        "description": clip(_description(n, excerpt), DESCRIPTION_LIMIT),
        "color": int(STATE_COLORS[n.state].strip.lstrip("#"), 16),
        "fields": [_field(fact, cap) for fact in facts][:FIELD_COUNT_LIMIT],
        "footer": {"text": clip(defuse(f"Noust · {n.server}"), FOOTER_LIMIT)},
        "timestamp": n.ts.isoformat(),
    }
    link = n.console_link
    if link is not None and http_url(link.url):
        embed["url"] = link.url
    return embed


def _size(embed: dict[str, Any]) -> int:
    """
    Args:
        embed: An embed.

    Returns:
        The number Discord's 6000-character limit counts.
    """
    return (
        len(embed["title"])
        + len(embed["description"])
        + len(embed["footer"]["text"])
        + sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
    )


def render(n: Notification) -> dict[str, Any]:
    """
    Render a notification as a webhook body.

    Args:
        n: The notification.

    Returns:
        The JSON body.
    """
    embed = fit(
        n,
        lambda excerpt, facts, cap: _embed(n, excerpt, facts, cap),
        _size,
        EMBED_BUDGET,
        excerpt_lines=EXCERPT_LINES,
        excerpt_chars=EXCERPT_CHARS,
    )
    payload: dict[str, Any] = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    if not n.loud:
        payload["flags"] = SUPPRESS_NOTIFICATIONS
    return payload
