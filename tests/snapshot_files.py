# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Snapshots as plain files, with no dependency.

A snapshot test compares what a renderer produced with a file kept next to the
tests. When a text changes on purpose, the diff of that file *is* the review:
the person who owns the wording reads exactly what every channel now says.

    pytest tests/test_notification_snapshots.py                       # compare
    NOUST_UPDATE_SNAPSHOTS=1 pytest tests/test_notification_snapshots.py   # rewrite

A missing file is a failure, not a silent pass: the first run of a new event
must be a deliberate one.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

import pytest

SNAPSHOT_ROOT = Path(__file__).parent / "snapshots"


def updating() -> bool:
    """
    Returns:
        Whether this run rewrites the stored snapshots.
    """
    return os.environ.get("NOUST_UPDATE_SNAPSHOTS", "") not in ("", "0")


def assert_snapshot(relative: str, actual: str) -> None:
    """
    Compare a rendered output with its stored snapshot.

    Args:
        relative: The file's path under ``tests/snapshots``.
        actual: What the code produced.
    """
    path = SNAPSHOT_ROOT / relative
    if updating():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    if not path.exists():
        pytest.fail(
            f"No snapshot at tests/snapshots/{relative}. If this output is new and right, "
            "run with NOUST_UPDATE_SNAPSHOTS=1 and review the file."
        )
    expected = path.read_text(encoding="utf-8")
    if expected != actual:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"snapshots/{relative} (stored)",
                tofile=f"snapshots/{relative} (now)",
                n=2,
            )
        )
        pytest.fail(f"{relative} changed. Review, then NOUST_UPDATE_SNAPSHOTS=1 to accept:\n{diff}")
