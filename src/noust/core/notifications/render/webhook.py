# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A notification as the generic webhook's JSON body, version 1.

The body before 3.1 was ``{event, title, body, domain, ts}``. Version 1 keeps
every one of those keys with its meaning and adds the rest of the model, so a
consumer written for the old body keeps working and a new one gets structure:

==========  ==========================================================
``version``  ``1``. Keys of version 1 are never removed or retyped; new
             optional keys may appear; a consumer ignores what it does
             not know, an ``event`` or a ``code`` included.
``id``       One per delivery (a UUID), so a receiver can deduplicate.
``event``    The kind, the switch an operator turns off (unchanged).
``code``     What actually happened, fine and stable: ``deploy.rolled_back``.
``state``    ``ok``, ``progress``, ``warning``, ``failed`` or ``info``.
``locale``   The language of ``title``, ``summary`` and every ``label``.
``title``    ``State: subject`` (``Rolled back: shop.example.com``). No
             longer a sentence to parse: use ``event``, ``code`` and ``facts``.
``summary``  One sentence.
``body``     Unchanged in kind: plain text of the summary, facts, excerpt
             and link, now without the title line.
``domain``   The application's domain, or null (unchanged).
``server``   The name of the server that raised it.
``ts``       ISO 8601, UTC, with a ``Z`` (unchanged in meaning).
``facts``    ``[{key, label, value}]``; ``key`` is stable (``commit``,
             ``duration``, ``server``), ``label`` is translated. The
             command to run next is the last fact.
``links``    ``[{rel, label, url}]``; ``rel`` ``console``.
``excerpt``  ``{label, lines, omitted}`` or null: the system's own lines,
             verbatim.
==========  ==========================================================

When ``notifications.channels.webhook.secret`` is set every delivery is signed:
``X-Noust-Signature: sha256=<hex>`` is the HMAC-SHA256 of the exact request
body with that secret, as GitHub signs its own. The body carries ``id`` and
``ts``, so a receiver that wants replay protection has both inside what is
signed. ``X-Noust-Event`` and ``X-Noust-Delivery`` repeat ``event`` and ``id``
for a receiver that routes before it parses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timezone
from typing import Any

from noust.core.notifications.model import Notification
from noust.core.notifications.render.common import detail_facts, render_text

#: The version of the body this module writes.
VERSION = 1


def _legacy_body(n: Notification) -> str:
    """
    Args:
        n: The notification.

    Returns:
        The plain text of it without the title line: the ``body`` a consumer
        of the old contract reads.
    """
    text = render_text(n)
    return text.split("\n", 1)[1].lstrip("\n") if "\n" in text else ""


def render(n: Notification) -> dict[str, Any]:
    """
    Render a notification as the webhook body.

    Args:
        n: The notification.

    Returns:
        The JSON-serialisable body.
    """
    stamp = n.ts.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "version": VERSION,
        "id": n.id,
        "event": n.kind,
        "code": n.code,
        "state": n.state.value,
        "locale": n.locale,
        "title": f"{n.title}: {n.subject}" if n.subject else n.title,
        "summary": n.summary,
        "body": _legacy_body(n),
        "domain": n.domain,
        "server": n.server,
        "ts": stamp,
        "facts": [
            {"key": fact.key, "label": fact.label, "value": fact.value} for fact in detail_facts(n)
        ],
        "links": [{"rel": link.rel, "label": link.label, "url": link.url} for link in n.links],
        "excerpt": (
            {
                "label": n.excerpt.label,
                "lines": list(n.excerpt.lines),
                "omitted": n.excerpt.omitted,
            }
            if n.excerpt is not None
            else None
        ),
    }
    return payload


def encode(payload: dict[str, Any]) -> bytes:
    """
    Serialise a body exactly once, so what is signed is what is sent.

    Args:
        payload: The body.

    Returns:
        Compact UTF-8 JSON.
    """
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign(body: bytes, secret: str) -> str:
    """
    Sign a request body.

    Args:
        body: The exact bytes that will be sent.
        secret: ``notifications.channels.webhook.secret``.

    Returns:
        ``sha256=<hex digest>`` of the HMAC-SHA256 of the body.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def headers(n: Notification, body: bytes, secret: str) -> dict[str, str]:
    """
    The headers that identify, and when there is a secret sign, a delivery.

    Args:
        n: The notification being delivered.
        body: The exact request body.
        secret: The configured secret; empty for none.

    Returns:
        ``X-Noust-Event``, ``X-Noust-Delivery`` and, only with a secret,
        ``X-Noust-Signature``.
    """
    result = {"X-Noust-Event": n.kind, "X-Noust-Delivery": n.id}
    if secret:
        result["X-Noust-Signature"] = sign(body, secret)
    return result
