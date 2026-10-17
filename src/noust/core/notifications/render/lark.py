# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as a Lark (Feishu) custom-bot message (checklist K-10).

A custom bot of a group chat takes ``POST /open-apis/bot/v2/hook/<token>`` with
``msg_type: interactive`` and a ``card``. The card, top to bottom: a header
coloured by the state (Lark's named templates), the summary, the facts as
two-column ``fields`` (the server first), the command and the excerpt, a note
with ``Noust - <server>``, and a button to the console.

Every text element is ``plain_text``: ``lark_md`` interprets Markdown and
``<at id=all></at>``, and a value somebody else chose (a branch, a commit
message) must neither format nor mention. A card has no monospace, so the
command and the excerpt are plain lines.

A bot with "signature verification" on wants ``timestamp`` and ``sign`` in the
body (:func:`signature`). Lark answers an error with HTTP 200 and a ``code``
that is not zero, so :func:`check_reply` is what makes a refusal one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from noust.core.notifications.model import Excerpt, Fact, Notification, State
from noust.core.notifications.render.common import (
    clip,
    fit,
    http_url,
    omitted_marker,
    with_pinned_marker,
)

#: A card over about 30 KB is refused; this keeps well under it.
CARD_BUDGET = 18_000
EXCERPT_LINES = 12
EXCERPT_CHARS = 1_500
MAX_FIELDS = 20

#: The named template of each state in a card header.
TEMPLATE = {
    State.OK: "green",
    State.PROGRESS: "orange",
    State.WARNING: "orange",
    State.FAILED: "red",
    State.INFO: "grey",
}


def _text(content: str) -> dict[str, str]:
    """
    Args:
        content: Text from anywhere.

    Returns:
        A ``plain_text`` element, which Lark shows literally.
    """
    return {"tag": "plain_text", "content": content}


def _elements(
    n: Notification, excerpt: Excerpt | None, facts: tuple[Fact, ...], cap: int
) -> list[dict[str, Any]]:
    """
    Build the card's body.

    Args:
        n: The notification.
        excerpt: The excerpt to show, or None.
        facts: The facts to show, the server first.
        cap: The longest a fact's value may be.

    Returns:
        The elements.
    """
    elements: list[dict[str, Any]] = []
    if n.summary:
        elements.append({"tag": "div", "text": _text(clip(n.summary, 600))})
    rows = [
        {
            "is_short": len(fact.value) <= 40,
            "text": _text(f"{clip(fact.label, 40)}\n{clip(fact.value, cap)}"),
        }
        for fact in facts
        if fact.value
    ]
    if rows:
        elements.append({"tag": "div", "fields": rows[:MAX_FIELDS]})
    if n.command and n.command.value:
        elements.append(
            {
                "tag": "div",
                "text": _text(f"{clip(n.command.label, 40)}: {clip(n.command.value, 300)}"),
            }
        )
    for section in n.sections:
        elements.append({"tag": "div", "text": _text(clip(section.heading, 200))})
        section_rows = [
            {"is_short": True, "text": _text(f"{clip(fact.label, 40)}\n{clip(fact.value, cap)}")}
            for fact in section.rows
            if fact.value
        ]
        if section_rows:
            elements.append({"tag": "div", "fields": section_rows[:MAX_FIELDS]})
    if excerpt is not None and excerpt.lines:
        lines = with_pinned_marker(list(excerpt.lines), excerpt, n)
        marker = omitted_marker(excerpt, n)
        if marker:
            lines.append(f"… {marker}")
        label = f"{clip(excerpt.label, 200)}\n" if excerpt.label else ""
        elements.append({"tag": "div", "text": _text(label + "\n".join(lines))})
    elements.append({"tag": "note", "elements": [_text(f"Noust · {clip(n.server, 64)}")]})
    link = n.console_link
    if link is not None and http_url(link.url):
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": _text(clip(link.label, 60)),
                        "url": link.url,
                        "type": "default",
                    }
                ],
            }
        )
    return elements


def render(n: Notification) -> dict[str, Any]:
    """
    Render a notification as the body of a custom-bot request.

    Args:
        n: The notification.

    Returns:
        The JSON body, without the signature (:func:`sign` adds it).
    """
    elements = fit(
        n,
        lambda excerpt, facts, cap: _elements(n, excerpt, facts, cap),
        lambda built: len(json.dumps(built)),
        CARD_BUDGET,
        excerpt_lines=EXCERPT_LINES,
        excerpt_chars=EXCERPT_CHARS,
    )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": _text(clip(f"{n.glyph} {n.headline}", 150)),
                "template": TEMPLATE[n.state],
            },
            "elements": elements,
        },
    }


def signature(timestamp: int, secret: str) -> str:
    """
    Sign a request the way a bot with signature verification expects.

    Args:
        timestamp: Epoch seconds; Lark refuses one more than an hour from its own clock.
        secret: The bot's signing secret.

    Returns:
        Base64 of the HMAC-SHA256 of an empty message, keyed by
        ``"<timestamp>\\n<secret>"``: Lark's own (unusual) construction.
    """
    key = f"{timestamp}\n{secret}".encode()
    return base64.b64encode(hmac.new(key, digestmod=hashlib.sha256).digest()).decode("ascii")


def sign(body: dict[str, Any], timestamp: int, secret: str) -> dict[str, Any]:
    """
    Args:
        body: A rendered message.
        timestamp: Epoch seconds.
        secret: The bot's signing secret.

    Returns:
        The body with ``timestamp`` and ``sign`` added.
    """
    return {**body, "timestamp": str(timestamp), "sign": signature(timestamp, secret)}


def check_reply(reply: bytes) -> None:
    """
    Turn Lark's refusal, which comes with HTTP 200, into an error.

    Args:
        reply: The response body.

    Raises:
        ValueError: When the answer carries a ``code`` (or the older
            ``StatusCode``) that is not zero; the message is Lark's own words.
    """
    try:
        answer = json.loads(reply.decode("utf-8", "replace"))
    except ValueError:
        return
    if not isinstance(answer, dict):
        return
    code = answer.get("code", answer.get("StatusCode", 0))
    if code in (0, None):
        return
    text = str(answer.get("msg") or answer.get("StatusMessage") or "").strip()
    raise ValueError(f"Lark refused the message (code {code}): {clip(text, 300)}".rstrip(": "))
