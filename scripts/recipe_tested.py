#!/usr/bin/env python3
# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Write ``recipes/tested.json``: which recipes of the catalogue have been started for real.

``tests/integration/recipes_run.py`` writes every run to ``tests/integration/results/recipes.json``,
a test artifact that does not ship. The catalogue shows, for each recipe, whether it was started and
when (``noust.recipes.tested``); this tool takes from the results the runs that passed, for the
recipes that are in the catalogue now, and writes them where the package carries them.

    scripts/recipe_tested.py           Write src/noust/recipes/tested.json.
    scripts/recipe_tested.py --check   Fail if it is not what the results say (the tests run this).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noust.recipes import list_recipes  # noqa: E402

RESULTS = ROOT / "tests" / "integration" / "results" / "recipes.json"
TESTED = ROOT / "src" / "noust" / "recipes" / "tested.json"


def installable() -> set[str]:
    """
    Returns:
        The names of the recipes the package installs. A recipe that is only listed (Whoogle, started
        on 2026-10-30 and withdrawn when its project ended) keeps its runs in the results and is not
        carried as tested: a tested mark on a listing says the opposite of the listing.
    """
    return {recipe.name for recipe in list_recipes() if recipe.available}


def build(results: dict[str, Any], catalogue: set[str]) -> dict[str, Any]:
    """
    Args:
        results: ``recipes.json`` as loaded.
        catalogue: The names of the recipes the package installs.

    Returns:
        The document ``tested.json`` holds: the runs that passed, for recipes in the catalogue, by
        name, with the version started, the day and the seconds it took.
    """
    recipes = {}
    for entry in sorted(results.get("results", []), key=lambda item: item["recipe"]):
        if entry["result"] != "pass" or entry["recipe"] not in catalogue:
            continue
        recipes[entry["recipe"]] = {
            "version": entry["version"],
            "date": str(entry["date"])[:10],
            "seconds": int(entry["seconds"]),
        }
    return {
        "measured": "tests/integration/recipes_run.py: scripts/recipe_smoke.py in the harness "
        "container, one recipe at a time",
        "recipes": recipes,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the tool.

    Args:
        argv: The arguments; ``sys.argv`` when None.

    Returns:
        0, or 1 when ``--check`` finds the file out of date.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    results = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.is_file() else {}
    wanted = json.dumps(build(results, installable()), indent=1, sort_keys=True)
    wanted += "\n"
    current = TESTED.read_text(encoding="utf-8") if TESTED.is_file() else ""
    if args.check:
        if wanted != current:
            print("out of date, run scripts/recipe_tested.py")
            return 1
        return 0
    TESTED.write_text(wanted, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
