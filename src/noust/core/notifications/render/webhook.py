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
             verbatim. With ``pinned`` (a count) when the first lines
             are the output's first error, brought up above its end.
==========  ==========================================================

When ``notifications.channels.webhook.secret`` is set every delivery is signed:
``X-Noust-Signature: sha256=<hex>`` is the HMAC-SHA256 of the exact request
body with that secret, as GitHub signs its own. The body carries ``id`` and
``ts``, so a receiver that wants replay protection has both inside what is
signed. ``X-Noust-Event`` and ``X-Noust-Delivery`` repeat ``event`` and ``id``
for a receiver that routes before it parses.

A receiver that wants its own headers (an ``Authorization: Bearer`` for n8n or Home Assistant, an
API key) gets them from ``notifications.channels.webhook.webhook_headers``, one ``Name: value``
per line (:func:`parse_headers`). They are secrets: sealed in the file, redacted when read back.
They add to the headers above and never replace one of them.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
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
        "excerpt": _excerpt(n),
    }
    return payload


def _excerpt(n: Notification) -> dict[str, Any] | None:
    """
    Args:
        n: The notification.

    Returns:
        Its excerpt as data, or None; ``pinned`` only when lines were
        brought up, so a receiver written for the first three keys sees
        exactly what it always did otherwise.
    """
    if n.excerpt is None:
        return None
    data: dict[str, Any] = {
        "label": n.excerpt.label,
        "lines": list(n.excerpt.lines),
        "omitted": n.excerpt.omitted,
    }
    if n.excerpt.pinned:
        data["pinned"] = n.excerpt.pinned
    return data


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


#: The most headers a receiver may be given, and the longest value of one.
MAX_CUSTOM_HEADERS = 20
MAX_HEADER_VALUE = 4096

#: A header name as RFC 9110 defines a token.
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")

#: The headers the request already has, or that frame it: a receiver's headers add to these and
#: never replace them, or a configured header could forge what Noust says about a delivery.
_OWNED = frozenset(
    {
        "content-type",
        "content-length",
        "host",
        "user-agent",
        "transfer-encoding",
        "connection",
        "x-noust-event",
        "x-noust-delivery",
        "x-noust-signature",
    }
)


def parse_headers(text: str | None) -> dict[str, str]:
    """
    Read the extra headers of a webhook: one ``Name: value`` per line.

    The one parser of the setting, used when it is saved and when a request is built, so what was
    accepted is what is sent. A value could otherwise split the request (a line break) or take a
    header Noust sets itself.

    Args:
        text: ``notifications.channels.webhook.webhook_headers``.

    Returns:
        Name to value, in the order written. Blank lines are nothing.

    Raises:
        ValueError: A line is not a header, a name or value is not allowed, a name repeats, or
            there are too many; the message names the line.
    """
    found: dict[str, str] = {}
    seen: set[str] = set()
    for number, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        name, colon, value = line.partition(":")
        name, value = name.strip(), value.strip()
        if not colon or not name:
            raise ValueError(f"line {number} is not 'Name: value'")
        if _TOKEN.fullmatch(name) is None:
            raise ValueError(f"line {number}: {name!r} is not a header name")
        if name.lower() in _OWNED:
            raise ValueError(f"line {number}: {name} is a header Noust sets itself")
        if name.lower() in seen:
            raise ValueError(f"line {number}: {name} is given twice")
        if not value:
            raise ValueError(f"line {number}: {name} has no value")
        if len(value) > MAX_HEADER_VALUE:
            raise ValueError(f"line {number}: the value is longer than {MAX_HEADER_VALUE}")
        if not value.isascii() or any(
            (ord(char) < 32 and char != "\t") or ord(char) == 127 for char in value
        ):
            raise ValueError(f"line {number}: the value must be printable ASCII (no control)")
        if len(found) >= MAX_CUSTOM_HEADERS:
            raise ValueError(f"at most {MAX_CUSTOM_HEADERS} headers may be added")
        seen.add(name.lower())
        found[name] = value
    return found


def headers(
    n: Notification, body: bytes, secret: str, custom: Mapping[str, str] | None = None
) -> dict[str, str]:
    """
    The headers that identify, and when there is a secret sign, a delivery.

    Args:
        n: The notification being delivered.
        body: The exact request body.
        secret: The configured secret; empty for none.
        custom: The receiver's own headers (:func:`parse_headers`).

    Returns:
        The receiver's headers, then ``X-Noust-Event``, ``X-Noust-Delivery`` and, only with a
        secret, ``X-Noust-Signature``; Noust's win over a custom header of the same name.
    """
    result = dict(custom or {})
    result.update({"X-Noust-Event": n.kind, "X-Noust-Delivery": n.id})
    if secret:
        result["X-Noust-Signature"] = sign(body, secret)
    return result
