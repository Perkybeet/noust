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

The one thing that can ping is a :class:`Mention` the operator configured
(a role and/or a user id), and only for a critical notification (state
``failed``). The ping is in the message ``content``, the only place Discord
honours a mention, never in the embed, and ``allowed_mentions`` then lists
exactly those ids: nothing anyone else wrote can ride on it.

:func:`render_plain` is the same message as plain text in ``content``, for when
Discord refuses the embed: an alert must not be lost to a formatting quirk.
It fits the 2000 characters a message may have the way the embed fits its
budget, keeping the headline and the link and giving up the oldest log lines
first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from noust.core.notifications.model import STATE_COLORS, Excerpt, Fact, Notification
from noust.core.notifications.render.common import (
    clip,
    fit,
    http_url,
    omitted_marker,
    with_pinned_marker,
)
from noust.validators.discord import validate_discord_id

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
#: What a message's own text may have, and what the fallback aims for.
CONTENT_LIMIT = 2000
CONTENT_BUDGET = 1900
#: The headline written beside a mention, for a phone that shows only ``content``.
LEAD_LIMIT = 200
PLAIN_EXCERPT_LINES = 10
PLAIN_EXCERPT_CHARS = 800

_ZWSP = "\u200b"
_MASS_MENTION = re.compile(r"@(everyone|here)", re.IGNORECASE)
_MARKDOWN = re.compile(r"([\\*_~`|\[\]])")


@dataclass(frozen=True)
class Mention:
    """
    Whom a critical notification pings.

    Attributes:
        role_id: A role's id, or empty for none.
        user_id: A user's id, or empty for none.

    Raises:
        ValueError: On construction, for an id that is not, in full, a Discord
            id. An id is never put into a message unless it is one, so what
            lands in ``content`` cannot be anything but a mention of that id.
    """

    role_id: str = ""
    user_id: str = ""

    def __post_init__(self) -> None:
        """Refuse an id that is not one."""
        if self.role_id:
            validate_discord_id(self.role_id, "a Discord role id")
        if self.user_id:
            validate_discord_id(self.user_id, "a Discord user id")

    def __bool__(self) -> bool:
        """True when someone is named."""
        return bool(self.role_id or self.user_id)

    def tokens(self) -> str:
        """
        Returns:
            The mentions as Discord reads them: ``<@&role> <@user>``.
        """
        parts = []
        if self.role_id:
            parts.append(f"<@&{self.role_id}>")
        if self.user_id:
            parts.append(f"<@{self.user_id}>")
        return " ".join(parts)

    def allowed(self) -> dict[str, Any]:
        """
        Returns:
            ``allowed_mentions`` that parses nothing and lets exactly these
            ids notify.
        """
        allowed: dict[str, Any] = {"parse": []}
        if self.role_id:
            allowed["roles"] = [self.role_id]
        if self.user_id:
            allowed["users"] = [self.user_id]
        return allowed


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


def _pinged(n: Notification, mention: Mention | None) -> Mention | None:
    """
    Args:
        n: The notification.
        mention: Whom the operator named, if anyone.

    Returns:
        The mention when this notification is critical and someone is named,
        otherwise None: nothing else pings.
    """
    return mention if mention and n.critical else None


def _lead(n: Notification, ping: Mention) -> str:
    """
    Args:
        n: A critical notification.
        ping: Whom it pings.

    Returns:
        The mentions and, beside them, what happened: a phone that shows only
        ``content`` still says more than a name.
    """
    headline = clip(markdown(f"{n.glyph} {n.headline}"), LEAD_LIMIT)
    return f"{ping.tokens()} {headline}"


def render(n: Notification, *, mention: Mention | None = None) -> dict[str, Any]:
    """
    Render a notification as a webhook body.

    Args:
        n: The notification.
        mention: Whom to ping if it is critical; nobody by default.

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
    ping = _pinged(n, mention)
    if ping is not None:
        payload["content"] = _lead(n, ping)
        payload["allowed_mentions"] = ping.allowed()
    if not n.loud:
        payload["flags"] = SUPPRESS_NOTIFICATIONS
    return payload


def _text(
    n: Notification,
    excerpt: Excerpt | None,
    facts: tuple[Fact, ...],
    cap: int,
    lead: str,
) -> str:
    """
    Build the plain-text message.

    Args:
        n: The notification.
        excerpt: The excerpt to show, or None.
        facts: The facts to show, the server first.
        cap: The longest a fact's value may be.
        lead: The mentions to start with, or an empty string.

    Returns:
        The headline, the link, the description and the facts as Discord
        markdown. The link is on the second line, so cutting the end can
        never take it.
    """
    headline = clip(markdown(f"{n.glyph} {n.headline}"), TITLE_LIMIT)
    lines = [f"{lead}**{headline}**"]
    link = n.console_link
    if link is not None and http_url(link.url):
        # Angle brackets keep Discord from adding a second, larger preview.
        lines.append(f"<{link.url}>")
    description = _description(n, excerpt)
    if description:
        lines.extend(["", description])
    rows = [_field(fact, cap) for fact in facts]
    if rows:
        lines.append("")
        lines.extend(f"**{row['name']}:** {row['value']}" for row in rows)
    return "\n".join(lines)


def render_plain(n: Notification, *, mention: Mention | None = None) -> dict[str, Any]:
    """
    Render a notification as plain text in ``content``, without an embed.

    Args:
        n: The notification.
        mention: Whom to ping if it is critical; nobody by default.

    Returns:
        The JSON body: ``content`` of at most :data:`CONTENT_LIMIT` characters
        and the same ``allowed_mentions`` and ``flags`` as :func:`render`.
    """
    ping = _pinged(n, mention)
    lead = f"{ping.tokens()} " if ping is not None else ""
    content = fit(
        n,
        lambda excerpt, facts, cap: _text(n, excerpt, facts, cap, lead),
        len,
        CONTENT_BUDGET,
        excerpt_lines=PLAIN_EXCERPT_LINES,
        excerpt_chars=PLAIN_EXCERPT_CHARS,
    )
    payload: dict[str, Any] = {
        "content": clip(content, CONTENT_LIMIT),
        "allowed_mentions": ping.allowed() if ping is not None else {"parse": []},
    }
    if not n.loud:
        payload["flags"] = SUPPRESS_NOTIFICATIONS
    return payload
