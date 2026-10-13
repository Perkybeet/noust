# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as an ntfy message, published as JSON.

ntfy accepts a message as headers over a plain body, which cannot carry a
non-ASCII title, or as a JSON document posted to the server's root, which can.
This is the second: ``topic``, ``title``, ``message``, ``priority``, ``tags`` and
the console link both as ``click`` (what a tap on the notification opens) and
as a ``view`` action button.

``priority`` follows the state: a failure is ``4`` (high), a warning ``3`` (the
default) and anything that is not a problem ``2`` (low, no sound). ``5`` is
left to whoever sets their own rules on the topic. The ``tags`` are the event's
kind and the server's name, plain labels: ntfy turns a tag that happens to be
an emoji shortcode into a picture, so the glyph in the title (a shape, as in
every channel) carries the state and no tag is picked for its picture.

The access token, when the topic needs one, is an ``Authorization`` header the
notifier adds; it is not part of the body.
"""

from __future__ import annotations

from typing import Any

from noust.core.notifications.model import Notification
from noust.core.notifications.render.common import clip, http_url, plain_body
from noust.core.notifications.routing import severity_of

#: ntfy rejects a message over 4096 bytes; characters are bytes only for ASCII,
#: so the budget leaves room for multi-byte text.
MESSAGE_LIMIT = 3000
TITLE_LIMIT = 250

#: The priority of each severity.
PRIORITY = {"failed": 4, "warning": 3, "info": 2}


def render(n: Notification, topic: str) -> dict[str, Any]:
    """
    Render a notification as the JSON ntfy publishes.

    Args:
        n: The notification.
        topic: The topic it is published to.

    Returns:
        The JSON body.
    """
    body: dict[str, Any] = {
        "topic": topic,
        "title": clip(f"{n.glyph} {n.headline}", TITLE_LIMIT),
        "message": plain_body(n, MESSAGE_LIMIT, excerpt_lines=10, excerpt_chars=1500) or n.title,
        "priority": PRIORITY[severity_of(n)],
        "tags": [n.kind, clip(n.server, 64)],
    }
    link = n.console_link
    if link is not None and http_url(link.url):
        body["click"] = link.url
        body["actions"] = [{"action": "view", "label": clip(link.label, 40), "url": link.url}]
    return body
