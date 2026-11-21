# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a client is never handed, and what it must say twice (J-20).

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
from collections.abc import Mapping
from typing import Any

from noust.core.scrubbing import MAX_DEPTH, REDACTED, hide_secrets, scrub_text

__all__ = [
    "CONFIRM_NEEDED",
    "MAX_DEPTH",
    "MAX_RESULT_CHARS",
    "REDACTED",
    "hide_secrets",
    "limit",
    "program_words",
    "refusal_text",
    "render",
    "scrub_text",
    "tail",
]

#: The most characters one tool result carries. Past it the end is cut and the
#: model is told, because a megabyte of journal is not an answer.
MAX_RESULT_CHARS = 60_000


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


def refusal_text(status: int, decoded: Any) -> str:
    """
    Say what the server refused, in its own words, for a model to read.

    Args:
        status: The API's status.
        decoded: Its error body.

    Returns:
        The server's words, scrubbed and bounded: the status, its code, what it said, its hint and
        the output it quotes.
    """
    contract = decoded if isinstance(decoded, dict) else {}
    detail = contract.get("detail")
    if isinstance(detail, dict):
        contract, detail = detail, detail.get("detail")
    parts = [
        f"The server refused ({status}{', ' + contract['error'] if contract.get('error') else ''}): "
        f"{detail or decoded}"
    ]
    if contract.get("hint"):
        parts.append(f"Hint: {contract['hint']}")
    if contract.get("output"):
        parts.append(f"Output:\n{contract['output']}")
    return limit(scrub_text("\n".join(str(p) for p in parts)))


def _text(value: Any) -> bool:
    """
    Args:
        value: A field of an answer.

    Returns:
        Whether it is text that says something.
    """
    return isinstance(value, str) and value.strip() != ""


def _quotes_output(value: Any, depth: int = 0) -> bool:
    """
    Args:
        value: An answer, or a part of one.
        depth: How far down it is.

    Returns:
        Whether a field named ``output`` that holds text is anywhere in it, down to :data:`MAX_DEPTH`.
    """
    if depth > MAX_DEPTH:
        return False
    if isinstance(value, Mapping):
        return any(
            (key == "output" and _text(item)) or _quotes_output(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_quotes_output(item, depth + 1) for item in value)
    return False


def program_words(status: int, decoded: Any) -> bool:
    """
    Say whether an answer of the API quotes words that a program or a repository wrote.

    The error contract keeps a failing tool's own output verbatim in ``output``; its ``detail`` and ``hint`` are
    Noust's own words only when they are a sentence of the catalog (``coded``), and are otherwise whatever the
    code that failed put in them, which can fold in what a tool printed. A body that is not the contract at all
    (a proxy's page) is somebody else's text. In an answer that went well, ``output`` is the field a result keeps
    a program's output in. Words like these are marked as data when the model is given them, and mark the
    conversation as one that has read text from outside, as a read of a journal does.

    Args:
        status: The API's status.
        decoded: What it answered, decoded.

    Returns:
        Whether the answer carries such words.
    """
    if status < 400:
        return _quotes_output(decoded)
    if isinstance(decoded, str):
        return _text(decoded)
    contract = decoded if isinstance(decoded, Mapping) else {}
    inner = contract.get("detail")
    if isinstance(inner, Mapping):
        contract = inner
    if _text(contract.get("output")):
        return True
    if contract.get("coded"):
        return False
    return _text(contract.get("detail")) or _text(contract.get("hint"))


CONFIRM_NEEDED = (
    "Nothing was done. This would {effect}. If that is what is meant, call the tool again "
    "with confirm=true."
)
