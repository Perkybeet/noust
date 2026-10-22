# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``hello`` recipe: the demo behind "Try a demo" on a server that runs nothing yet.

It has to be the smallest thing that can answer on an address, because the first deployment of
a person with no domain is this one: one static page, no data, no database, no build, nothing
fetched, no Docker. What is pinned is that it stays that, and that it still passes the lint every
recipe passes.
"""

from __future__ import annotations

import re
from pathlib import Path

from noust.core.logger import Logger
from noust.core.store import NoustStore
from noust.recipes import get_recipe
from noust.recipes.assets import read_asset
from noust.recipes.deploy import plan_recipe
from noust.recipes.lint import lint_catalogue

DOMAIN = "hello.203-0-113-10.sslip.io"


def test_it_is_a_native_static_recipe_with_nothing_to_install() -> None:
    recipe = get_recipe("hello")

    assert (recipe.kind, recipe.app_type) == ("native", "static")
    assert recipe.available is True
    assert recipe.requires == ()
    assert recipe.database_choices == ()
    assert recipe.source is not None and recipe.source.kind == "template"
    assert recipe.source.files == {"index.html": "index.html"}


def test_it_asks_for_no_variable_and_no_port() -> None:
    recipe = get_recipe("hello")

    assert recipe.variables == ()
    assert recipe.port is None


def test_it_has_no_finding_in_the_lint() -> None:
    assert [finding for finding in lint_catalogue() if finding.recipe == "hello"] == []


def test_it_is_in_the_catalogue_a_person_can_search() -> None:
    from noust.recipes.catalog import search_recipes

    assert "hello" in [recipe.name for recipe in search_recipes(query="demo")]


def test_its_page_needs_nothing_from_anywhere_else() -> None:
    page = read_asset("hello", "index.html")

    # A page that loads a script, a font or an image from another host would be a demo that can
    # fail on a server whose outbound traffic is shut, and a page that tracks whoever opens it.
    assert not re.search(r"(?:src|href)\s*=\s*[\"']?(?:https?:)?//", page)
    assert "<script" not in page
    assert "@import" not in page and "url(" not in page
    assert '<html lang="en">' in page and "<title>" in page
    assert len(page.encode()) < 4096


def test_its_page_is_the_only_file_the_plan_writes(recipe_store: NoustStore) -> None:
    plan = plan_recipe("hello", DOMAIN, port=None, ssl=True, logger=Logger(verbose=False))

    written = sorted(path.name for path in Path(plan.source).iterdir())
    assert written == ["index.html"]
    assert plan.env_vars == {}
    assert plan.database is None and plan.keyed == ()
    assert (plan.app_type, plan.port) == ("static", None)
