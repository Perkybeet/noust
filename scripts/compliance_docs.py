#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Write and check docs/COMPLIANCE.md, the page of controls and frameworks.

The page is generated from the one mapping table
(``src/noust/core/compliance``), so it cannot say a framework asks for something the
code does not. ``--check`` (``tests/test_compliance_docs.py`` runs it) fails when the
page is out of date; ``--write`` regenerates it.

Usage:
    scripts/compliance_docs.py            Check that docs/COMPLIANCE.md is current.
    scripts/compliance_docs.py --write    Regenerate it.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "COMPLIANCE.md"

sys.path.insert(0, str(ROOT / "src"))


def render() -> str:
    """
    Returns:
        The page as the code says it should read.
    """
    from noust.core.compliance import docs, frameworks

    problems = frameworks.check_table()
    if problems:
        raise SystemExit("The mapping table is not sound:\n  " + "\n  ".join(problems))
    return docs.render()


def check() -> int:
    """
    Returns:
        0 when the page is current, 1 (with a diff) when it is not.
    """
    expected = render()
    current = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    if current == expected:
        print(f"{DOC.relative_to(ROOT)} is current")
        return 0
    diff = difflib.unified_diff(
        current.splitlines(), expected.splitlines(), "docs/COMPLIANCE.md", "generated", lineterm=""
    )
    print("\n".join(list(diff)[:60]))
    print("\ndocs/COMPLIANCE.md is out of date: run python scripts/compliance_docs.py --write")
    return 1


def main(argv: list[str] | None = None) -> int:
    """
    Command line entry point.

    Args:
        argv: Arguments, defaulting to ``sys.argv``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("--write", action="store_true", help="regenerate the page")
    args = parser.parse_args(argv)
    if args.write:
        DOC.write_text(render(), encoding="utf-8")
        print(f"wrote {DOC.relative_to(ROOT)}")
        return 0
    return check()


if __name__ == "__main__":
    sys.exit(main())
