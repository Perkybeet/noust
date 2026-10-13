#!/usr/bin/env python3
"""
Lint the recipes: no image may move, and every mistake is reported at once.

Usage:
    scripts/recipe_lint.py
        Lint the recipes the package ships. This is the CI gate: it fails when a
        Compose recipe has an image on a floating tag (``latest``, ``stable``,
        ``main``...), without a tag, without the digest the registry served for
        it, or named by a variable.
    scripts/recipe_lint.py DIR
        Lint a folder laid out as the catalogue is (``<name>.yaml`` and
        ``assets/<name>/``), for a recipe you are writing or a private catalogue.
    scripts/recipe_lint.py --warnings-as-errors
        Also fail on a recipe pinned by tag only (``digest_verified: false``).

It reads files and makes no network request: whether a digest is the one the
registry serves is ``scripts/recipe_pins.py check``'s question.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.recipes.lint import ERROR, lint_catalogue  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """
    Run the lint.

    Args:
        argv: Arguments, without the program name.

    Returns:
        0 when there is nothing to fail on, 1 when there is.
    """
    parser = argparse.ArgumentParser(description="Lint the recipes.")
    parser.add_argument("directory", nargs="?", type=Path, help="A folder of recipes to lint.")
    parser.add_argument(
        "--warnings-as-errors", action="store_true", help="Fail on a recipe pinned by tag only."
    )
    arguments = parser.parse_args(argv)
    findings = lint_catalogue(arguments.directory)
    for finding in findings:
        print(finding.render())
    failing = [
        finding for finding in findings if finding.severity == ERROR or arguments.warnings_as_errors
    ]
    if failing:
        print(f"{len(failing)} finding(s) fail the lint.", file=sys.stderr)
        return 1
    print(f"Recipes are clean ({len(findings)} warning(s)).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
