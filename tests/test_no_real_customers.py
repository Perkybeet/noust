# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
No real customer or server appears in the tree (item 84, the audit of the history).

Fixtures, examples and tests are written from real cases, and a real case brings its names with
it: the domain of a customer's application, the account of a hosting server, a subdomain of a
product somebody else sells. None of them is a secret, but none of them is ours to publish, and
the examples are better for being invented (``example.com``, ``example.net``, ``example.org``,
or a name made up for the purpose). The shape of the case is what a test needs, and it survives
the change of name.

The names are written backwards below so that this file does not hold them itself. A new name
joins the list when one is found; the match ignores case.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

#: Names of customers, of their products and of the servers that hosted them, backwards.
REVERSED = (
    "sbalannera",
    "sracirfa",
    "robmatopurg",
    "fdpedroditrevnoc",
    "seronemtc",
    "sadaipmiloruen",
    "ayalatta",
    "paau",
    "senoicaticil",
    "aropmit",
    "codrotseg",
    "ojabartsetrap",
    "kettic",
    "adobrq",
)
NAMES = re.compile("|".join(name[::-1] for name in REVERSED), re.IGNORECASE)

#: Not read: tooling, other people's code, working material that is not published, and the
#: console's generated build, which is compiled from ``panel/`` (whose sources are read).
SKIPPED_DIRECTORIES = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        ".venv",
        ".ruff_cache",
        ".mypy_cache",
        ".pytest_cache",
        "superpowers",
        ".claude",
        "dist",
        ".build",
        "test-results",
        "playwright-report",
        "__screens__",
    }
)
SKIPPED_PATHS = frozenset({"src/noust/web/static", "docs/llms-full.txt"})


def _files() -> list[Path]:
    """Every file worth reading, found without descending into the directories that are skipped."""
    found: list[Path] = []
    for directory, subdirectories, names in os.walk(ROOT):
        subdirectories[:] = sorted(d for d in subdirectories if d not in SKIPPED_DIRECTORIES)
        here = Path(directory)
        for name in sorted(names):
            path = here / name
            posix = path.relative_to(ROOT).as_posix()
            if any(posix == s or posix.startswith(s + "/") for s in SKIPPED_PATHS):
                continue
            found.append(path)
    return found


def test_no_real_customer_or_server_is_named() -> None:
    """A fixture, a test, a doc or a seed uses an invented name instead."""
    offenders: list[str] = []
    for path in _files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, start=1):
            if NAMES.search(line):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    assert not offenders, (
        "a real customer or server is named; use an invented name (example.com, example.net, "
        "example.org) and keep the shape of the case:\n" + "\n".join(offenders[:40])
    )


def test_the_guard_reads_the_tree_it_claims_to() -> None:
    """A guard that scans nothing passes forever."""
    files = {path.relative_to(ROOT).as_posix() for path in _files()}
    assert "tests/test_no_real_customers.py" in files
    assert "scripts/console_server.py" in files
    assert any(name.startswith("panel/src/") for name in files)
    assert not any(name.startswith("src/noust/web/static/") for name in files)
