# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as a Mattermost incoming-webhook body (checklist K-07).

Mattermost accepts the shape of Slack's *legacy* attachments (``fallback``,
``color``, ``title``, ``text``, ``fields``, ``footer``) and ignores Block Kit,
which is what the Slack renderer writes, so a Mattermost webhook pointed at the
Slack channel shows only the attachment's fallback line. This is the same
message in the part Mattermost reads: a coloured strip (the state's token), the
state and subject as the title linking to the console, the summary and the
command as text, the facts as two-column ``fields`` (the server first), the
excerpt in a fenced code block, and ``Noust - <server>`` as the footer.

Mattermost renders ``text`` and a field's ``value`` as Markdown and turns
``@channel``, ``@all``, ``@here`` and ``@name`` into notifications for people.
A branch name or a commit message is somebody else's text, so :func:`plain`
makes it arrive as text: a zero-width space after every character that would
start formatting, a mention, an emoji code or a link. A code block cannot be
closed from inside because its own three backticks are replaced.
"""

from __future__ import annotations

import json
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

#: What the whole body may weigh, measured as the JSON that is sent. A server's
#: post limit is 16383 characters in current releases and 4000 in old ones; a
#: field and the text have their own, lower, bounds.
BODY_BUDGET = 9_000
TEXT_LIMIT = 3_500
FIELD_LIMIT = 800
TITLE_LIMIT = 200
EXCERPT_LINES = 12
EXCERPT_CHARS = 1_200

_ZWSP = "​"

# An underscore only formats at the edge of a word; `feature_x_y` stays as typed.
_EDGE_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])")
# `@here`, `@channel`, `@all` and `@name` start a word; `user@host` is an address.
_MENTION = re.compile(r"(?<![\w.])@(?=\w)")
# `:tada:` becomes a picture; `12:30:45` does not match (a code has no digits-only).
_EMOJI = re.compile(r":(?=[a-z0-9_+-]+:)")
# `[text](url)` and `![alt](url)` are links and pictures only with the pair.
_LINK = re.compile(r"\](?=\()")
# A line that starts with these is a heading, a quote, a list or a table.
_BLOCK_START = re.compile(r"(?:[+|>-]|\d+[.)])(?:\s|$)|#")


def plain(text: str) -> str:
    """
    Make untrusted text arrive as text in Markdown.

    Args:
        text: A value someone else chose.

    Returns:
        The text with a zero-width space after every character that would
        start bold, strike-through, code, a mention, an emoji or a link, and
        before a first character that would make the line a heading, a quote,
        a list item or a table.
    """
    text = re.sub(r"([*~`|\\])", lambda m: m.group(1) + _ZWSP, text)
    text = _EDGE_UNDERSCORE.sub(lambda m: "_" + _ZWSP, text)
    text = _MENTION.sub("@" + _ZWSP, text)
    text = _EMOJI.sub(":" + _ZWSP, text)
    text = _LINK.sub("]" + _ZWSP, text)
    if _BLOCK_START.match(text):
        text = _ZWSP + text
    return text


def _code(text: str) -> str:
    """
    Args:
        text: A value shown in monospace.

    Returns:
        It in an inline code span that its own content cannot close.
    """
    return "`" + text.replace("`", "'") + "`"


def _field(fact: Fact, cap: int) -> dict[str, Any]:
    """
    Args:
        fact: A labelled value.
        cap: The longest its value may be.

    Returns:
        One ``fields`` entry.
    """
    value = clip(fact.value, cap)
    return {
        "short": len(fact.value) <= 40,
        "title": clip(plain(fact.label), 40),
        "value": clip(_code(value) if fact.mono else plain(value), FIELD_LIMIT),
    }


def _excerpt_text(excerpt: Excerpt, notification: Notification) -> str:
    """
    Args:
        excerpt: The excerpt.
        notification: Its notification, for the language.

    Returns:
        The label in italics and the lines in a code block that cannot close early.
    """
    shown = with_pinned_marker(list(excerpt.lines), excerpt, notification)
    # A code block is no shelter: the server looks for mentions in the whole text.
    lines = [_MENTION.sub("@" + _ZWSP, line.replace("```", "'''")) for line in shown]
    marker = omitted_marker(excerpt, notification)
    if marker:
        lines.append(f"… {marker}")
    label = f"*{plain(clip(excerpt.label, 200))}*\n" if excerpt.label else ""
    return f"{label}```\n" + "\n".join(lines) + "\n```"


def _attachment(
    n: Notification, excerpt: Excerpt | None, facts: tuple[Fact, ...], cap: int
) -> dict[str, Any]:
    """
    Build the attachment.

    Args:
        n: The notification.
        excerpt: The excerpt to show, or None.
        facts: The facts to show, the server first.
        cap: The longest a fact's value may be.

    Returns:
        The attachment.
    """
    parts: list[str] = []
    if n.summary:
        parts.append(plain(n.summary))
    if n.command and n.command.value:
        parts.append(f"**{plain(clip(n.command.label, 40))}:** {_code(clip(n.command.value, 300))}")
    fields = [_field(fact, cap) for fact in facts]
    for section in n.sections:
        parts.append(f"**{plain(clip(section.heading, 200))}**")
        parts.extend(
            f"{plain(clip(fact.label, 40))}: {_code(clip(fact.value, cap)) if fact.mono else plain(clip(fact.value, cap))}"
            for fact in section.rows
            if fact.value
        )
    if excerpt is not None and excerpt.lines:
        parts.append(_excerpt_text(excerpt, n))
    title = clip(f"{n.glyph} {plain(n.headline)}", TITLE_LIMIT)
    attachment: dict[str, Any] = {
        "fallback": clip(f"{n.glyph} {n.headline}", 300),
        "color": STATE_COLORS[n.state].strip,
        "title": title,
        "text": clip("\n\n".join(parts), TEXT_LIMIT) if parts else "",
        "fields": fields,
        "footer": f"Noust · {clip(plain(n.server), 64)}",
    }
    link = n.console_link
    if link is not None and http_url(link.url):
        attachment["title_link"] = link.url
    return attachment


def render(n: Notification) -> dict[str, Any]:
    """
    Render a notification as an incoming-webhook body.

    Args:
        n: The notification.

    Returns:
        The JSON body.
    """
    attachment = fit(
        n,
        lambda excerpt, facts, cap: _attachment(n, excerpt, facts, cap),
        lambda built: len(json.dumps(built)),
        BODY_BUDGET,
        excerpt_lines=EXCERPT_LINES,
        excerpt_chars=EXCERPT_CHARS,
    )
    return {"username": "Noust", "attachments": [attachment]}
