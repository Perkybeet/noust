# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an audit event is allowed to carry.

The audit log is shipped to other machines and read by auditors: it is the
worst place for a secret to end up. Details pass through here before they are
written, and the rules are the configuration's own (a key whose name
:func:`~noust.core.config.is_secret_key` or
:func:`~noust.core.secret_detection.name_looks_secret` flags has its value
replaced), plus a password inside any URL, plus bounds on size so one event
cannot become a megabyte.

A SQL statement is not a secret by its name and can hold one in its text
(``ALTER USER ... PASSWORD '...'``), so :func:`statement_digest` records its
length, its SHA-256 and a shortened, scrubbed head instead of the statement.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from noust.core.config import REDACTED, is_secret_key
from noust.core.secret_detection import name_looks_secret, redact_url_credentials

#: Longest string value kept whole in an event.
MAX_VALUE_LENGTH = 2000

#: Most items of a list, or keys of a mapping, kept in an event.
MAX_ITEMS = 100

#: How deep nested details may go.
MAX_DEPTH = 4

#: Characters of a statement kept in its digest.
STATEMENT_HEAD = 200

_QUOTED_SECRET = re.compile(
    r"(?i)((?:password|passwd|secret|token|identified\s+by)\s*=?\s*)'[^']*'"
)

#: Detail names that the general rules would flag but that, in an audit
#: event, name a thing rather than hold a credential: the configuration key
#: that changed, the name of the token that acted.
NOT_SECRET_NAMES = frozenset({"key", "token_name"})


def secret_name(name: str) -> bool:
    """
    Whether a detail's name marks its value as a secret.

    Args:
        name: The key.

    Returns:
        True for names the configuration or the environment rules treat as
        secret (``password``, ``token``, ``api_key``, ``webhook_url``...),
        except those in :data:`NOT_SECRET_NAMES`.
    """
    if name in NOT_SECRET_NAMES:
        return False
    return is_secret_key(name) or name_looks_secret(name)


def clean_text(value: str, limit: int = MAX_VALUE_LENGTH) -> str:
    """
    Scrub and bound one string.

    Args:
        value: The text.
        limit: Longest result.

    Returns:
        The text with URL passwords replaced, cut at ``limit`` with a marker.
    """
    text = redact_url_credentials(value)
    if len(text) > limit:
        return f"{text[:limit]}... ({len(value)} characters)"
    return text


def clean_details(details: Mapping[str, Any] | None, depth: int = 0) -> dict[str, Any]:
    """
    Make details safe and bounded for the log.

    Args:
        details: Free-form context from the caller.
        depth: Nesting level, for the recursion.

    Returns:
        A JSON-ready copy with every secret-named value replaced by the
        redaction marker, URL passwords scrubbed, and sizes bounded.
    """
    if not details:
        return {}
    cleaned: dict[str, Any] = {}
    for index, (key, value) in enumerate(details.items()):
        if index >= MAX_ITEMS:
            cleaned["..."] = f"{len(details) - MAX_ITEMS} more"
            break
        name = str(key)[:64]
        if secret_name(name) and value not in (None, "", REDACTED):
            cleaned[name] = REDACTED
            continue
        cleaned[name] = _clean_value(value, depth + 1)
    return cleaned


def _clean_value(value: Any, depth: int) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return clean_text(value)
    if depth >= MAX_DEPTH:
        return clean_text(str(value), 200)
    if isinstance(value, Mapping):
        return clean_details(value, depth)
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        kept = [_clean_value(item, depth + 1) for item in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            kept.append(f"... {len(items) - MAX_ITEMS} more")
        return kept
    return clean_text(str(value))


def scrub_statement(statement: str) -> str:
    """
    Replace the secrets a SQL statement's text can carry.

    Quoted values after ``PASSWORD``, ``SECRET``, ``TOKEN`` or ``IDENTIFIED
    BY``, and passwords inside URLs. What the SQL console keeps in its
    history goes through this, as the digest's head does.

    Args:
        statement: The statement.

    Returns:
        The statement with those values replaced by the redaction marker.
    """
    head = _QUOTED_SECRET.sub(lambda match: f"{match.group(1)}'{REDACTED}'", statement)
    return redact_url_credentials(head)


def statement_digest(statement: str) -> dict[str, Any]:
    """
    Describe a SQL statement without storing it.

    Args:
        statement: The statement as it was run.

    Returns:
        Its length, its SHA-256 (so a copy kept elsewhere can be matched)
        and its first :data:`STATEMENT_HEAD` characters with quoted
        passwords and URL credentials replaced.
    """
    head = _QUOTED_SECRET.sub(lambda match: f"{match.group(1)}'{REDACTED}'", statement)
    return {
        "length": len(statement),
        "sha256": hashlib.sha256(statement.encode("utf-8", errors="replace")).hexdigest(),
        "head": clean_text(" ".join(head.split()), STATEMENT_HEAD),
    }
