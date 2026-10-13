# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as a Pushover message.

Pushover takes a form (``application/x-www-form-urlencoded``) with the
application's token and the user's key; those two are credentials, so the
notifier adds them to the request and this renderer, which is pure and whose
output is snapshotted, never sees them. What it writes:

- ``title``: the state's glyph, the state word and the subject;
- ``message``: the summary, the facts and the excerpt as plain text, inside
  Pushover's 1024 characters, shortened the way every channel is (the oldest
  excerpt lines first, never the end);
- ``url`` and ``url_title``: the console link, as a field of its own so a tap
  opens it;
- ``priority``: a failure is ``1`` (high: it ignores the user's quiet hours), a
  warning ``0`` and anything that is not a problem ``-1``, which Pushover
  delivers without a sound. ``2`` (emergency) needs a retry loop and an
  acknowledgement Noust does not offer, so it is never used.
"""

from __future__ import annotations

from datetime import timezone

from noust.core.notifications.model import Notification
from noust.core.notifications.render.common import clip, http_url, plain_body
from noust.core.notifications.routing import severity_of

#: Pushover's limits, in characters.
MESSAGE_LIMIT = 1024
TITLE_LIMIT = 250
URL_LIMIT = 512
URL_TITLE_LIMIT = 100

#: The priority of each severity.
PRIORITY = {"failed": 1, "warning": 0, "info": -1}


def render(n: Notification) -> dict[str, str]:
    """
    Render a notification as the fields of a Pushover message.

    Args:
        n: The notification.

    Returns:
        The form fields, all strings, without the token and the user key.
    """
    fields = {
        "title": clip(f"{n.glyph} {n.headline}", TITLE_LIMIT),
        "message": plain_body(n, MESSAGE_LIMIT, excerpt_lines=6, excerpt_chars=500) or n.title,
        "priority": str(PRIORITY[severity_of(n)]),
        "timestamp": str(int(n.ts.astimezone(timezone.utc).timestamp())),
    }
    link = n.console_link
    if link is not None and http_url(link.url) and len(link.url) <= URL_LIMIT:
        fields["url"] = link.url
        fields["url_title"] = clip(link.label, URL_TITLE_LIMIT)
    return fields
