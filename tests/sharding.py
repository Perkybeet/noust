# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cutting the suite into slices for CI (``--shard K/N``).

The cut is made on whole test files and from nothing but what was collected: every xdist worker
collects the whole suite and must keep the same slice, and the jobs of a CI matrix run on machines
that never talk to each other. The files are dealt out greedily, biggest first, each to the slice
that holds the fewest tests so far, which keeps the slices within one file of each other by test
count (the one measure known before a test has run) and is the same answer on every machine.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

#: ``K/N``, ASCII digits only: ``str.isdigit`` accepts the digits of other scripts.
_SHARD = re.compile(r"([0-9]+)/([0-9]+)")


def parse_shard(text: str) -> tuple[int, int]:
    """
    Read ``K/N``.

    Args:
        text: The value of ``--shard`` or ``NOUST_TEST_SHARD``.

    Returns:
        The slice, counted from 1, and how many slices there are.

    Raises:
        pytest.UsageError: When it is not ``K/N`` with ``1 <= K <= N``.
    """
    found = _SHARD.fullmatch(text.strip())
    if found is not None:
        index, shards = int(found.group(1)), int(found.group(2))
        if 1 <= index <= shards:
            return index, shards
    raise pytest.UsageError(
        f"--shard takes K/N with 1 <= K <= N, for example 2/4: got {text!r}.\n"
        "K is the slice this run keeps and N how many the suite is cut into."
    )


def assign(counts: Mapping[str, int], shards: int) -> dict[str, int]:
    """
    Deal the test files out to the slices.

    Args:
        counts: The number of tests in each file.
        shards: How many slices.

    Returns:
        The slice (from 1) each file belongs to. It depends on the counts and the names only, never
        on the order they are given in.
    """
    held = [0] * shards
    owner: dict[str, int] = {}
    for name in sorted(counts, key=lambda each: (-counts[each], each)):
        lightest = min(range(shards), key=lambda slot: (held[slot], slot))
        held[lightest] += counts[name]
        owner[name] = lightest + 1
    return owner


def select(items: Sequence[Any], index: int, shards: int) -> tuple[list[Any], list[Any]]:
    """
    Split a collection into the slice to run and the rest.

    Args:
        items: The collected tests, anything with a ``nodeid``.
        index: The slice to keep, from 1.
        shards: How many slices there are.

    Returns:
        ``(kept, dropped)``, each in the order of ``items``.
    """
    files = [item.nodeid.split("::", 1)[0] for item in items]
    owner = assign(Counter(files), shards)
    kept: list[Any] = []
    dropped: list[Any] = []
    for item, name in zip(items, files, strict=True):
        (kept if owner[name] == index else dropped).append(item)
    return kept, dropped
