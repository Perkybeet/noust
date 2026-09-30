# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as a Slack incoming-webhook body: Block Kit inside an attachment.

An attachment because it is the only way a webhook message gets a coloured
strip down its side (the colour is the state's, the console's own token), and
it accepts ``blocks`` inside. There is no top-level ``text``: the strip and
the header say the state, and the attachment's ``fallback`` is what a
notification on a phone reads. (Whether Slack draws a strip for an attachment
whose message has no ``text`` is checked against a real workspace with
``scripts/notification_gallery.py --live``; if it does not, dropping the
attachment wrapper is the whole change.)

The layout, top to bottom: a ``header`` (glyph, state word, subject), a
``section`` with the summary, a ``section`` with the facts as two-column
``fields`` (the server first), the command, the excerpt in a code block with
an italic label, and a ``context`` line with the console link and
``Noust · <server>``.

No buttons: a button with a URL still sends an interaction payload that must
be acknowledged, and a webhook-only app has nobody to receive it. The link is
mrkdwn in the context block.

Slack has no escape for ``*``, ``_`` and ``~``, so untrusted values (a branch,
a commit message) are made harmless by a zero-width space after the character
that would start formatting; ``&``, ``<`` and ``>`` are entities, which also
disarms ``<!channel>`` and ``<@U123>``; a code block cannot be closed from
inside because its own three backticks are replaced.
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
)

#: Slack's limits: a header's text, a section's text, a field's text, and the
#: fields of one section.
HEADER_LIMIT = 150
SECTION_LIMIT = 3000
FIELD_LIMIT = 2000
FIELDS_PER_SECTION = 10
MAX_BLOCKS = 50

#: What the excerpt block may take of a section's 3000, label included.
EXCERPT_BUDGET = 2500
EXCERPT_LINES = 12
EXCERPT_CHARS = 1200

_ZWSP = "\u200b"


def entities(text: str) -> str:
    """
    Escape the three characters Slack reads as control sequences.

    Args:
        text: Text from anywhere.

    Returns:
        The text with ``&``, ``<`` and ``>`` as entities.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# An underscore only formats at the edge of a word, so only those are broken:
# `feature_x_y` stays exactly what a person will copy.
_EDGE_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])")


def plain(text: str) -> str:
    """
    Make untrusted text arrive as text in mrkdwn.

    Args:
        text: A value someone else chose.

    Returns:
        The text as entities, with a zero-width space after every character
        that would start bold, strike-through, code or italics.
    """
    text = entities(text)
    text = re.sub(r"([*~`])", lambda m: m.group(1) + _ZWSP, text)
    return _EDGE_UNDERSCORE.sub(lambda m: "_" + _ZWSP, text)


def literal(text: str) -> str:
    """
    Make text harmless where Slack takes plain text: a header, a fallback.

    Slack does not parse a ``plain_text`` object, but the ``fallback`` of an
    attachment is what a notification prints and may go through the same
    control-sequence parsing as a message, so ``<!channel>`` and ``<@U123>``
    in a branch name must not survive as written there either. A zero-width
    space after ``<`` keeps them from being control sequences and leaves them
    reading the same.

    Args:
        text: Text from anywhere.

    Returns:
        The text with a zero-width space after every ``<``.
    """
    return text.replace("<", "<" + _ZWSP)


def _code(text: str) -> str:
    """
    Args:
        text: A value shown in monospace.

    Returns:
        ``text`` in an inline code span that its own content cannot close.
    """
    return "`" + entities(text).replace("`", "'") + "`"


def _field(fact: Fact, cap: int) -> dict[str, str]:
    """
    Args:
        fact: A labelled value.
        cap: The longest its value may be.

    Returns:
        One ``fields`` entry: bold label, newline, value.
    """
    value = clip(fact.value, cap)
    shown = _code(value) if fact.mono else plain(value)
    return {
        "type": "mrkdwn",
        "text": clip(f"*{plain(clip(fact.label, 40))}*\n{shown}", FIELD_LIMIT),
    }


def _excerpt_text(excerpt: Excerpt, notification: Notification) -> str:
    """
    Args:
        excerpt: The excerpt.
        notification: Its notification, for the language.

    Returns:
        The italic label and the lines in a code block that cannot close early.
    """
    lines = [entities(line).replace("```", "'''") for line in excerpt.lines]
    marker = omitted_marker(excerpt, notification)
    if marker:
        lines.append(entities(f"… {marker}"))
    label = f"_{plain(clip(excerpt.label, 200))}_\n" if excerpt.label else ""
    return f"{label}```" + "\n".join(lines) + "```"


def _blocks(
    n: Notification, excerpt: Excerpt | None, facts: tuple[Fact, ...], cap: int
) -> list[dict[str, Any]]:
    """
    Build the blocks.

    Args:
        n: The notification.
        excerpt: The excerpt to show, or None.
        facts: The facts to show, the server first.
        cap: The longest a fact's value may be.

    Returns:
        The blocks, header first and context last.
    """
    glyph = n.glyph
    header = clip(literal(f"{glyph} {n.headline}"), HEADER_LIMIT)
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header, "emoji": False}}
    ]
    if n.summary:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": clip(plain(n.summary), SECTION_LIMIT)},
            }
        )
    fields = [_field(fact, cap) for fact in facts]
    for start in range(0, len(fields), FIELDS_PER_SECTION):
        blocks.append({"type": "section", "fields": fields[start : start + FIELDS_PER_SECTION]})
    if n.command and n.command.value:
        label = plain(clip(n.command.label, 40))
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": clip(f"*{label}:* {_code(clip(n.command.value, 300))}", SECTION_LIMIT),
                },
            }
        )
    for section in n.sections:
        rows = [_field(fact, cap) for fact in section.rows if fact.value]
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{plain(clip(section.heading, 200))}*",
                },
            }
        )
        for start in range(0, len(rows), FIELDS_PER_SECTION):
            blocks.append({"type": "section", "fields": rows[start : start + FIELDS_PER_SECTION]})
    if excerpt is not None and excerpt.lines:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": _excerpt_text(excerpt, n)},
            }
        )
    elements: list[dict[str, str]] = []
    link = n.console_link
    if link is not None and http_url(link.url):
        target = entities(link.url).replace("|", "%7C")
        elements.append({"type": "mrkdwn", "text": f"<{target}|{plain(clip(link.label, 60))}>"})
    elements.append({"type": "mrkdwn", "text": f"Noust · {plain(clip(n.server, 64))}"})
    blocks.append({"type": "context", "elements": elements})
    return blocks


def render(n: Notification) -> dict[str, Any]:
    """
    Render a notification as an incoming-webhook body.

    Args:
        n: The notification.

    Returns:
        The JSON body.
    """
    blocks = fit(
        n,
        lambda excerpt, facts, cap: _blocks(n, excerpt, facts, cap),
        _excerpt_size,
        EXCERPT_BUDGET,
        excerpt_lines=EXCERPT_LINES,
        excerpt_chars=EXCERPT_CHARS,
    )
    if len(blocks) > MAX_BLOCKS:
        # A report with very many entries: the footer, which carries the link,
        # is kept and the entries that do not fit are the ones that go.
        blocks = [*blocks[: MAX_BLOCKS - 1], blocks[-1]]
    return {
        "attachments": [
            {
                "color": STATE_COLORS[n.state].strip,
                "fallback": clip(literal(f"{n.glyph} {n.headline}"), 300),
                "blocks": blocks,
            }
        ]
    }


def _excerpt_size(blocks: list[dict[str, Any]]) -> int:
    """
    Args:
        blocks: The blocks.

    Returns:
        The length of the excerpt's block, the one that may need to give way.
    """
    return max(
        (
            len(block["text"]["text"])
            for block in blocks
            if block["type"] == "section" and "```" in block.get("text", {}).get("text", "")
        ),
        default=0,
    )
