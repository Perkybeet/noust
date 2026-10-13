# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as a Gotify message.

Gotify's ``POST /message`` takes ``title``, ``message``, ``priority`` and free
``extras``. The application token is a header (``X-Gotify-Key``) the notifier
adds, never a query parameter: a URL is written into proxy logs.

``priority`` is Gotify's 0 to 10: a failure is ``8`` (the clients' loud level),
a warning ``5`` and anything that is not a problem ``2``. The message is plain
text; ``extras`` tells the Android client to open the console link when the
notification is tapped.
"""

from __future__ import annotations

from typing import Any

from noust.core.notifications.model import Notification
from noust.core.notifications.render.common import clip, http_url, plain_body
from noust.core.notifications.routing import severity_of

MESSAGE_LIMIT = 3000
TITLE_LIMIT = 250

#: The priority of each severity.
PRIORITY = {"failed": 8, "warning": 5, "info": 2}


def render(n: Notification) -> dict[str, Any]:
    """
    Render a notification as the JSON body of ``POST /message``.

    Args:
        n: The notification.

    Returns:
        The JSON body.
    """
    body: dict[str, Any] = {
        "title": clip(f"{n.glyph} {n.headline}", TITLE_LIMIT),
        "message": plain_body(n, MESSAGE_LIMIT, excerpt_lines=10, excerpt_chars=1500) or n.title,
        "priority": PRIORITY[severity_of(n)],
    }
    link = n.console_link
    if link is not None and http_url(link.url):
        body["extras"] = {"client::notification": {"click": {"url": link.url}}}
    return body
