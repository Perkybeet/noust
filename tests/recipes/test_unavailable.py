# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A recipe that cannot be installed says why with a code a console can translate and filter by."""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.server_text import CATALOG, HINT_SUFFIX
from noust.recipes import RecipeError, list_recipes, parse_recipe, search_recipes
from noust.recipes.deploy import require_available
from noust.recipes.model import UNAVAILABLE_CODES
from tests.recipes.factory import compose_recipe, use_demo_assets


@pytest.fixture(autouse=True)
def demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the recipe named ``demo`` use the logo of a shipped recipe as its own."""
    use_demo_assets(monkeypatch)


def listed(**overrides: Any) -> dict[str, Any]:
    """A recipe that is listed and cannot be installed."""
    data = compose_recipe(
        available=False,
        unavailable_code="licence",
        unavailable_reason="Its licence forbids it (LICENSE, read 2026-10-28).",
    )
    for key in ("compose", "health", "variables", "architectures", "notes", "port"):
        data.pop(key, None)
    data.update(overrides)
    return data


def test_a_listed_recipe_carries_the_code_of_its_reason() -> None:
    """The code is what a filter and a translation hang on; the reason is the evidence."""
    recipe = parse_recipe("demo", listed())

    assert recipe.available is False and recipe.unavailable_code == "licence"
    summary = recipe.summary()
    assert summary["unavailable_code"] == "licence"
    assert summary["unavailable"] == {"code": "recipes.unavailable.licence", "params": {}}
    assert summary["unavailable_text"] == CATALOG["recipes.unavailable.licence"]
    assert summary["unavailable_hint"] == CATALOG["recipes.unavailable.licence" + HINT_SUFFIX]
    assert summary["unavailable_reason"].startswith("Its licence forbids it")


def test_a_recipe_that_installs_has_no_reason_to_give() -> None:
    """The fields are there and empty, so a client reads one shape."""
    summary = parse_recipe("demo", compose_recipe()).summary()

    assert summary["unavailable_code"] is None
    assert summary["unavailable"] is None
    assert summary["unavailable_text"] is None
    assert summary["replaced_by"] is None


def test_the_code_is_one_of_a_closed_list_and_a_listed_recipe_needs_one() -> None:
    """A free-text code would drift into several spellings of the same reason."""
    with pytest.raises(RecipeError, match="unavailable_code must be one of"):
        parse_recipe("demo", listed(unavailable_code="because"))
    data = listed()
    del data["unavailable_code"]
    with pytest.raises(RecipeError, match="unavailable_code"):
        parse_recipe("demo", data)


def test_a_recipe_that_installs_cannot_carry_a_reason_code() -> None:
    """A code on an installable recipe is a recipe somebody forgot to finish enabling."""
    with pytest.raises(RecipeError, match="unavailable_code is only for an unavailable recipe"):
        parse_recipe("demo", compose_recipe(unavailable_code="licence"))


def test_a_listed_recipe_may_point_to_what_replaces_it() -> None:
    """MinIO is archived and Garage is the catalogue's answer: the console can say so."""
    recipe = parse_recipe("demo", listed(unavailable_code="archived", replaced_by="garage"))

    assert recipe.replaced_by == "garage" and recipe.summary()["replaced_by"] == "garage"
    with pytest.raises(RecipeError, match="replaced_by"):
        parse_recipe("demo", listed(replaced_by="Not A Name"))


def test_every_code_has_a_sentence_and_a_fix_in_the_catalog_of_the_servers_words() -> None:
    """The console translates ``recipes.unavailable.<code>`` and its fix."""
    for code in UNAVAILABLE_CODES:
        assert f"recipes.unavailable.{code}" in CATALOG, code
        assert f"recipes.unavailable.{code}{HINT_SUFFIX}" in CATALOG, code


def test_every_listed_recipe_of_the_catalogue_has_a_code_and_a_replacement_that_exists() -> None:
    """The listing is data a filter reads: no recipe is left with a reason in prose only."""
    everything = {recipe.name: recipe for recipe in list_recipes()}
    for recipe in everything.values():
        if recipe.available:
            assert recipe.unavailable_code is None, recipe.name
            continue
        assert recipe.unavailable_code in UNAVAILABLE_CODES, recipe.name
        if recipe.replaced_by:
            assert everything[recipe.replaced_by].available, recipe.name


def test_the_catalogue_filters_by_whether_a_recipe_installs_and_by_the_reason_it_does_not() -> None:
    """``available=False`` is the listing of what is out; the code narrows it."""
    out = search_recipes(available=False)
    assert out and all(not recipe.available for recipe in out)
    assert all(recipe.available for recipe in search_recipes(available=True))
    code = out[0].unavailable_code
    assert code is not None
    narrowed = search_recipes(unavailable_code=code)
    assert narrowed and all(recipe.unavailable_code == code for recipe in narrowed)
    with pytest.raises(RecipeError, match="unavailable_code"):
        search_recipes(unavailable_code="because")


def test_installing_a_listed_recipe_is_refused_with_the_sentence_and_the_evidence() -> None:
    """The error carries the code for the console and the evidence the recipe holds."""
    recipe = parse_recipe("demo", listed())

    with pytest.raises(RecipeError) as refused:
        require_available(recipe)

    assert refused.value.coded is not None
    assert refused.value.coded.code == "recipes.unavailable.licence"
    assert "Its licence forbids it" in refused.value.details
