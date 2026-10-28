# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Searching the things of every server.

The aggregator's ``index`` view has one row per thing a person may look for (an application, a
database, a site, a certificate, a scheduled job) and one per server, each with the words its
record carries. This chooses among them: every word of the query has to be found, and a name that
is the query comes before one that starts with it, before one that has it inside, before a record
that only mentions it. The query is text compared as text: nothing here builds a pattern from it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: What a search can find, in the order that settles a tie.
KINDS: tuple[str, ...] = ("server", "app", "site", "database", "certificate", "cron")

#: The most hits one answer carries, whatever is asked.
MAX_LIMIT = 200

#: Where a name stands against the query, best first.
_EXACT, _PREFIX, _INSIDE, _MENTIONED = range(4)


def _fields(row: Mapping[str, Any]) -> list[str]:
    """
    Args:
        row: A row of the index.

    Returns:
        Everything a person may type to find it, lower-cased: its name, its second identifier,
        its words and the server it is on.
    """
    parts = [row.get("name"), row.get("detail"), row.get("node"), *(row.get("words") or [])]
    return [part.lower() for part in parts if isinstance(part, str) and part]


def _standing(name: str, lead: str) -> int:
    """
    Args:
        name: The row's name, lower-cased.
        lead: The first word of the query, lower-cased.

    Returns:
        How well the name matches it: :data:`_EXACT`, :data:`_PREFIX`, :data:`_INSIDE`, or
        :data:`_MENTIONED` when only another field has it.
    """
    if name == lead:
        return _EXACT
    if name.startswith(lead):
        return _PREFIX
    return _INSIDE if lead in name else _MENTIONED


def search(
    rows: Iterable[Mapping[str, Any]],
    query: str,
    *,
    kinds: Iterable[str] | None = None,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int, bool]:
    """
    Choose the rows a query finds, best first.

    Args:
        rows: The rows of the ``index`` view.
        query: What was typed; every whitespace-separated word has to be found.
        kinds: Only these kinds; None for all of them.
        limit: The most hits to return, from 1 to :data:`MAX_LIMIT`.

    Returns:
        The hits (the row without the words it was found by), how many rows matched, and
        whether the hits are fewer than that.
    """
    words = query.lower().split()
    if not words:
        return [], 0, False
    wanted = set(kinds) if kinds is not None else None
    ranked: list[tuple[tuple[int, int, str, str], dict[str, Any]]] = []
    for row in rows:
        if wanted is not None and row.get("kind") not in wanted:
            continue
        haystack = _fields(row)
        if not all(any(word in field for field in haystack) for word in words):
            continue
        kind = str(row.get("kind"))
        order = KINDS.index(kind) if kind in KINDS else len(KINDS)
        name = str(row.get("name") or "")
        hit = {key: value for key, value in row.items() if key != "words"}
        ranked.append(((_standing(name.lower(), words[0]), order, str(row.get("node")), name), hit))
    ranked.sort(key=lambda entry: entry[0])
    size = max(1, min(int(limit), MAX_LIMIT))
    return [hit for _, hit in ranked[:size]], len(ranked), len(ranked) > size
