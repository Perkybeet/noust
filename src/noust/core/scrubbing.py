# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What must not reach a program that reads text, said once.

An MCP client and the built-in assistant are the same kind of reader: a program that takes what
Noust hands it and acts on it, in a place Noust does not control. Both must be handed text from
which the secrets are gone, and the one function that does it lives here, below the two (rule 3):
:func:`scrub_text` for a piece of text and :func:`hide_secrets` for a structure. They add to
:meth:`noust.core.redact.Scrubber.scrub` (the values Noust knows are secret, and the shapes of a
credential) what a log or a tool's answer carries in its own layout: a password in a URL, a
``KEY=value`` line, a bearer token and a private key block.

None of this can recognise a secret it was not told about. That is why the readers are also given
no way to reveal one in the first place.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from noust.core.audit.sanitize import secret_name
from noust.core.config import REDACTED
from noust.core.redact import scrubber_for
from noust.core.secret_detection import redact_url_credentials

__all__ = ["MAX_DEPTH", "REDACTED", "hide_secrets", "scrub_text"]

#: How deep a structure is walked.
MAX_DEPTH = 12

#: ``API_KEY=abc`` and ``password: abc`` in free text, as logs print them.
_ASSIGNMENT = re.compile(
    r"(?im)^(?P<head>[ \t]*(?:export[ \t]+)?[A-Za-z0-9_.\-]*"
    r"(?:secret|token|passw(?:or)?d|passwd|api[_\-]?key|private[_\-]?key|credential|auth)"
    r"[A-Za-z0-9_.\-]*[ \t]*[=:][ \t]*)(?P<value>\S[^\r\n]*)$"
)
#: A bearer token or a private key block, wherever it appears.
_BEARER = re.compile(r"(?i)(bearer[ \t]+)[A-Za-z0-9._~+/=\-]{12,}")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
)


def scrub_text(text: str, domain: str | None = None) -> str:
    """
    Remove from a piece of text what must not reach a client.

    Args:
        text: Output of an application, a job or the server.
        domain: The application it is about, whose own secret values are
            known and removed wherever they appear.

    Returns:
        The text with known secret values, URL passwords, secret-looking
        assignments, bearer tokens and private keys replaced.
    """
    cleaned = redact_url_credentials(text)
    cleaned = _PRIVATE_KEY.sub(REDACTED, cleaned)
    cleaned = _BEARER.sub(lambda match: f"{match.group(1)}{REDACTED}", cleaned)
    cleaned = _ASSIGNMENT.sub(lambda match: f"{match.group('head')}{REDACTED}", cleaned)
    return scrubber_for(domain).scrub(cleaned)


def hide_secrets(value: Any, domain: str | None = None, depth: int = 0) -> Any:
    """
    Walk an answer and remove every secret from it.

    Args:
        value: Anything JSON can hold.
        domain: The application the answer is about, if one.
        depth: Nesting level, for the recursion.

    Returns:
        A copy in which the value of every secret-named key is the redaction
        marker and every string is scrubbed.
    """
    if depth > MAX_DEPTH:
        return REDACTED
    if isinstance(value, str):
        return scrub_text(value, domain)
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if secret_name(name) and item not in (None, "", True, False):
                cleaned[name] = REDACTED
            else:
                cleaned[name] = hide_secrets(item, domain, depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [hide_secrets(item, domain, depth + 1) for item in value]
    return value
