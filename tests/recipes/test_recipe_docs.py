# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""scripts/recipe_docs.py: the catalogue's table in docs/recipes.md cannot drift from the recipes."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from noust.recipes import list_recipes

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "recipe_docs.py"


@pytest.fixture(scope="module")
def docs() -> ModuleType:
    """The script, loaded as a module."""
    spec = importlib.util.spec_from_file_location("recipe_docs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_docs"] = module
    spec.loader.exec_module(module)
    return module


def test_the_page_holds_exactly_what_the_script_writes(docs: ModuleType) -> None:
    """A recipe added, removed or re-pinned without running the script fails here, not in a review."""
    assert docs.current() == docs.generate(), "run scripts/recipe_docs.py"


def test_every_installable_recipe_has_a_row_with_its_version(docs: ModuleType) -> None:
    """The table is the list of what ships: a title and a version for each recipe, none for a listed one."""
    table = docs.generate()
    for recipe in list_recipes():
        row = f"| {recipe.title} | {recipe.version} |"
        assert (row in table) == recipe.available, recipe.name


def test_the_script_check_passes_on_a_current_page(docs: ModuleType) -> None:
    """``--check`` is what a pipeline runs: it exits 0 when nothing would change."""
    assert docs.main(["--check"]) == 0


def test_a_recipe_that_names_coolify_credits_its_licence_and_the_template(
    docs: ModuleType,
) -> None:
    """Coolify's templates are Apache-2.0: a recipe derived from one names the licence and the file."""
    for recipe in list_recipes():
        head = (docs.RECIPES / f"{recipe.name}.yaml").read_text(encoding="utf-8")
        head = head.split("schema:", 1)[0]
        if "coolify" not in head.lower():
            continue
        assert "Apache-2.0" in head, recipe.name
        assert re.search(
            r"https://github\.com/coollabsio/coolify/blob/next/templates/compose/[\w.-]+\.ya?ml",
            head,
        ), recipe.name
        assert docs.provenance(recipe) == "coolify", recipe.name
