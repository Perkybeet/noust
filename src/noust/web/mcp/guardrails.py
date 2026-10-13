# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an agent is never handed, and what it must say twice (J-20).

An MCP client is a program that reads what a tool returns and acts on it. Two
things follow, and this module is where both are made true once, for every
tool:

- **It never receives a secret.** The value of a variable, a password, a key, a
  connection string's credential: none reaches the model, whatever the token is
  allowed to read over HTTP. Three layers, because each misses what another
  catches: tools that exist to list variables return their *names* only; every
  answer is walked and the value of anything whose name says it is secret is
  replaced; and every string is scrubbed of the values Noust knows are secret
  (the application's own, the server's credentials) and of passwords inside
  URLs and ``KEY=value`` lines. None of this can recognise a secret it was not
  told about: the tools therefore offer no way to reveal one in the first place
  (no ``secrets.reveal`` route is a tool, and none is reachable through them).
- **It cannot destroy something by accident.** A tool that stops what serves,
  or replaces it, needs ``confirm: true``; without it the call does nothing and
  says what it would have done, so the model has to say it twice.

What a tool returns is also *data*, not instruction: logs and diagnoses are
written by the applications they describe, so the tools' descriptions and the
prompts tell the model to read them as such.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from noust.core.audit.sanitize import secret_name
from noust.core.config import REDACTED
from noust.core.redact import scrubber_for
from noust.core.secret_detection import redact_url_credentials

#: The most characters one tool result carries. Past it the end is cut and the
#: model is told, because a megabyte of journal is not an answer.
MAX_RESULT_CHARS = 60_000

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
    Remove from a piece of text what must not reach an agent.

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


def limit(text: str, maximum: int = MAX_RESULT_CHARS) -> str:
    """
    Bound a result.

    Args:
        text: What a tool returns.
        maximum: The most characters kept.

    Returns:
        The text, with its end cut and said so when it was too long. The start
        is kept for a structure, the end for a log (see :func:`tail`).
    """
    if len(text) <= maximum:
        return text
    return f"{text[:maximum]}\n... (cut: {len(text) - maximum} more characters)"


def tail(text: str, maximum: int = MAX_RESULT_CHARS) -> str:
    """
    Bound a log, keeping its end: what happened last is what matters.

    Args:
        text: A log.
        maximum: The most characters kept.

    Returns:
        The text, with its start cut and said so when it was too long.
    """
    if len(text) <= maximum:
        return text
    return f"... (cut: {len(text) - maximum} earlier characters)\n{text[-maximum:]}"


def render(value: Any, domain: str | None = None) -> str:
    """
    Turn an answer into the text a tool returns, safe and bounded.

    Args:
        value: The API's decoded answer.
        domain: The application it is about, if one.

    Returns:
        Indented JSON for a structure, the scrubbed text for a string.
    """
    safe = hide_secrets(value, domain)
    if isinstance(safe, str):
        return limit(safe)
    return limit(json.dumps(safe, indent=2, ensure_ascii=False, default=str))


CONFIRM_NEEDED = (
    "Nothing was done. This would {effect}. If that is what is meant, call the tool again "
    "with confirm=true."
)
