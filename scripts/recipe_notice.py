#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Keep the credit of the recipes that come from another catalogue where the licences ask for it.

A recipe whose header says "Derived in part from Coolify's template ..." (Apache-2.0) or "Derived in
part from Dokploy's template ..." (MIT) carries that project's licence terms: its authors' copyright
line and the licence have to travel with every copy. ``NOTICE`` lists the recipes under each, the
Debian copyright file names them in a ``Files:`` paragraph of their own, and
``tests/test_third_party_notice.py`` fails when a list is not the recipes that say so. With hundreds
of recipes the lists are written by this tool from the headers, never by hand:

    scripts/recipe_notice.py           Rewrite the lists in NOTICE and obs/debian.copyright.
    scripts/recipe_notice.py --check   Fail if either is out of date (the tests run this).

The marks of the simple-icons collection (CC0-1.0) are declared in the same files in a paragraph of
their own: the licence asks for nothing to travel, and the paragraph says where they come from.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "src" / "noust" / "recipes"
NOTICE = ROOT / "NOTICE"
COPYRIGHT = ROOT / "obs" / "debian.copyright"

#: The sentence a recipe's header starts a credit with, by the catalogue it comes from.
COOLIFY_MARK = "Derived in part from Coolify's template"
DOKPLOY_MARK = "Derived in part from Dokploy's template"

#: What NOTICE and the Debian copyright file say about each, and the markers around the list.
NOTICE_COOLIFY = re.compile(
    r"(header which template it comes from and what was changed:\n\n)(.*?)(\n\nPlausible Community Edition)",
    re.DOTALL,
)
NOTICE_DOKPLOY = re.compile(
    r"(Dokploy templates\n-----------------\n.*?which template it comes from\s+and what was "
    r"changed:\n\n)(.*?)(\n\nThe tests of the Dokploy template converter)",
    re.DOTALL,
)
COPYRIGHT_COOLIFY = re.compile(
    r"(\nFiles:\n)((?:(?!\n\n).)*?)(\nCopyright: 2025 Andras Bacsai\n)", re.DOTALL
)
COPYRIGHT_DOKPLOY = re.compile(
    r"(\nFiles:\n)((?:(?!\n\n).)*?)(\nCopyright: 2024 Dokploy and Carlos Ortiz\n)", re.DOTALL
)


def derived(mark: str) -> list[str]:
    """
    Args:
        mark: One of the marks a header starts a credit with.

    Returns:
        The recipes whose file says it, as repository paths, sorted.
    """
    return sorted(
        f"src/noust/recipes/{path.name}"
        for path in RECIPES.glob("*.yaml")
        if mark in path.read_text(encoding="utf-8")
    )


def indented(paths: Sequence[str], indent: str) -> str:
    """
    Args:
        paths: Repository paths.
        indent: What starts each line.

    Returns:
        One line per path.
    """
    return "\n".join(f"{indent}{path}" for path in paths)


def render_notice(text: str) -> str:
    """
    Args:
        text: NOTICE as it is.

    Returns:
        NOTICE with the lists of derived recipes written from the recipes' headers.

    Raises:
        SystemExit: A paragraph the lists belong to is not where it is looked for.
    """
    for pattern, mark in ((NOTICE_COOLIFY, COOLIFY_MARK), (NOTICE_DOKPLOY, DOKPLOY_MARK)):
        found = pattern.search(text)
        if found is None:
            raise SystemExit(f"NOTICE has no paragraph for {mark!r}")
        paths = derived(mark)
        body = indented(paths, "  ") if paths else "  (none yet)"
        text = text[: found.start(2)] + body + text[found.end(2) :]
    return text


def render_copyright(text: str) -> str:
    """
    Args:
        text: The Debian copyright file as it is.

    Returns:
        The file with the ``Files:`` paragraphs of the derived recipes written from the headers.

    Raises:
        SystemExit: A paragraph the lists belong to is not where it is looked for.
    """
    for pattern, mark in ((COPYRIGHT_COOLIFY, COOLIFY_MARK), (COPYRIGHT_DOKPLOY, DOKPLOY_MARK)):
        found = pattern.search(text)
        if found is None:
            raise SystemExit(f"obs/debian.copyright has no paragraph for {mark!r}")
        paths = derived(mark)
        body = indented(paths, " ") if paths else " src/noust/recipes/none-yet.yaml"
        text = text[: found.start(2)] + body + text[found.end(2) :]
    return text


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the tool.

    Args:
        argv: The arguments; ``sys.argv`` when None.

    Returns:
        0, or 1 when ``--check`` finds a file out of date.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = []
    for path, render in ((NOTICE, render_notice), (COPYRIGHT, render_copyright)):
        current = path.read_text(encoding="utf-8")
        wanted = render(current)
        if wanted != current:
            stale.append(path.relative_to(ROOT))
            if not args.check:
                path.write_text(wanted, encoding="utf-8")
    if args.check and stale:
        print("out of date, run scripts/recipe_notice.py:", *stale)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
