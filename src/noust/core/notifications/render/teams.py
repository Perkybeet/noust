# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as a Microsoft Teams message: an Adaptive Card.

Teams retired the Office 365 connectors (``MessageCard``); a webhook made with
the Workflows app ("Post to a channel when a webhook request is received")
takes a ``message`` with one Adaptive Card attachment, which is what this
writes. The card, top to bottom: the state and subject as a heading coloured by
the state (Teams has a handful of named colours: ``Good``, ``Warning``,
``Attention``), the summary, the facts as a ``FactSet`` (the server first), the
command and the excerpt in monospace, and an ``Action.OpenUrl`` to the console.

Every value is set as plain text (``wrap`` on) and nothing here builds markup
out of untrusted text: a card's ``TextBlock`` renders a Markdown subset, so a
value that must stay literal is never put in a field that interprets it unless
the notification model has already cleaned it.
"""

from __future__ import annotations

from typing import Any

from noust.core.notifications.model import Excerpt, Fact, Notification, State
from noust.core.notifications.render.common import (
    clip,
    fit,
    http_url,
    omitted_marker,
    with_pinned_marker,
)

#: Teams refuses a card over about 28 KB; this keeps well under it.
CARD_BUDGET = 20_000
EXCERPT_LINES = 12
EXCERPT_CHARS = 1500

#: The named colour of each state in an Adaptive Card.
COLOR = {
    State.OK: "Good",
    State.PROGRESS: "Warning",
    State.WARNING: "Warning",
    State.FAILED: "Attention",
    State.INFO: "Default",
}


def _facts(facts: tuple[Fact, ...], cap: int) -> list[dict[str, str]]:
    """
    Args:
        facts: Labelled values.
        cap: The longest a value may be.

    Returns:
        The entries of a ``FactSet``, without the empty ones.
    """
    return [{"title": clip(f.label, 40), "value": clip(f.value, cap)} for f in facts if f.value]


def _body(
    n: Notification, excerpt: Excerpt | None, facts: tuple[Fact, ...], cap: int
) -> list[dict[str, Any]]:
    """
    Args:
        n: The notification.
        excerpt: The excerpt to show, or None.
        facts: The facts to show, the server first.
        cap: The longest a fact's value may be.

    Returns:
        The card's body elements.
    """
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": clip(f"{n.glyph} {n.headline}", 250),
            "weight": "Bolder",
            "size": "Medium",
            "color": COLOR[n.state],
            "wrap": True,
        }
    ]
    if n.summary:
        body.append({"type": "TextBlock", "text": clip(n.summary, 600), "wrap": True})
    rows = _facts(facts, cap)
    if rows:
        body.append({"type": "FactSet", "facts": rows})
    if n.command and n.command.value:
        body.append(
            {
                "type": "TextBlock",
                "text": clip(f"{n.command.label}: {n.command.value}", 400),
                "fontType": "Monospace",
                "wrap": True,
            }
        )
    for section in n.sections:
        body.append({"type": "TextBlock", "text": clip(section.heading, 200), "weight": "Bolder"})
        section_rows = _facts(section.rows, cap)
        if section_rows:
            body.append({"type": "FactSet", "facts": section_rows})
    if excerpt is not None and excerpt.lines:
        lines = with_pinned_marker(list(excerpt.lines), excerpt, n)
        marker = omitted_marker(excerpt, n)
        if marker:
            lines.append(f"… {marker}")
        if excerpt.label:
            body.append(
                {
                    "type": "TextBlock",
                    "text": clip(excerpt.label, 200),
                    "isSubtle": True,
                    "wrap": True,
                }
            )
        body.append(
            {"type": "TextBlock", "text": "\n".join(lines), "fontType": "Monospace", "wrap": True}
        )
    return body


def render(n: Notification) -> dict[str, Any]:
    """
    Render a notification as the message a Workflows webhook accepts.

    Args:
        n: The notification.

    Returns:
        The JSON body.
    """
    body = fit(
        n,
        lambda excerpt, facts, cap: _body(n, excerpt, facts, cap),
        lambda elements: len(str(elements)),
        CARD_BUDGET,
        excerpt_lines=EXCERPT_LINES,
        excerpt_chars=EXCERPT_CHARS,
    )
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    link = n.console_link
    if link is not None and http_url(link.url):
        card["actions"] = [
            {"type": "Action.OpenUrl", "title": clip(link.label, 60), "url": link.url}
        ]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }
        ],
    }
