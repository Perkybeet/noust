# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which recipes have been started for real, and when.

A recipe that loads has been read by a closed schema; whether it starts is only known when it
runs. ``tests/integration/recipes_run.py`` starts the catalogue's Compose recipes one at a time in
the harness container's own Docker and writes what happened to
``tests/integration/results/recipes.json``. That file is a test artifact and does not ship; this
module reads the part of it the catalogue shows, ``recipes/tested.json``, which
``scripts/recipe_tested.py`` writes from it: for each recipe whose run passed, the version that was
started, the day and how long it took.

A recipe is *tested* when the catalogue has such an entry for it, and the entry is *current* when
it was made for the version the recipe pins now: a recipe revised since its run still says it was
tested, and says for which version. Only the package's own recipes are looked up: a recipe of the
operator's with the same name is not the one that was started.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any


@lru_cache(maxsize=1)
def tested_recipes() -> dict[str, dict[str, Any]]:
    """
    Read the entries the package carries.

    Returns:
        Recipe name to ``version``, ``date`` (ISO, to the day) and ``seconds``; empty when the
        package carries none.
    """
    resource = resources.files("noust.recipes").joinpath("tested.json")
    if not resource.is_file():
        return {}
    document = json.loads(resource.read_text(encoding="utf-8"))
    recipes = document.get("recipes") if isinstance(document, dict) else None
    return {str(name): dict(entry) for name, entry in (recipes or {}).items()}


def tested_view(name: str, version: str, origin: str = "shipped") -> dict[str, Any] | None:
    """
    Say whether a recipe was started for real.

    Args:
        name: The recipe.
        version: The version it pins now.
        origin: Where the recipe comes from; only the package's own are looked up.

    Returns:
        ``version`` and ``date`` of the run, how many ``seconds`` it took and whether it was
        ``current`` (made for the version pinned now); None when no run is recorded.
    """
    if origin != "shipped":
        return None
    entry = tested_recipes().get(name)
    if entry is None:
        return None
    return {
        "version": entry["version"],
        "date": entry["date"],
        "seconds": entry["seconds"],
        "current": entry["version"] == version,
    }
