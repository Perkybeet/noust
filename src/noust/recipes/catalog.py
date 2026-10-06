# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The catalogue: finding the recipes the package ships, searching and filtering them.

Recipes are files in this package (``<name>.yaml``), loaded and validated on
first use and kept: they cannot change while the process runs. The API and the
command line both read the catalogue here, so a search means the same thing in
both.
"""

from __future__ import annotations

from functools import cache
from importlib import resources
from typing import Any

import yaml

from noust.recipes.assets import read_asset
from noust.recipes.model import (
    CATEGORIES,
    KINDS,
    NAME_PATTERN,
    Recipe,
    RecipeError,
    RecipeNotFoundError,
    recipe_error,
)
from noust.recipes.schema import parse_recipe


def _recipe_files() -> dict[str, Any]:
    """
    List the recipe files the package ships.

    Returns:
        Recipe name to resource.
    """
    return {
        entry.name.removesuffix(".yaml"): entry
        for entry in resources.files("noust.recipes").iterdir()
        if entry.name.endswith(".yaml") and entry.is_file()
    }


@cache
def _load(name: str) -> Recipe:
    """
    Read and validate one recipe file.

    Args:
        name: A name that is known to be shipped.

    Returns:
        The recipe.

    Raises:
        RecipeError: Its file is not valid. Errors are not cached: the next
            call reads the file again.
    """
    try:
        data = yaml.safe_load(_recipe_files()[name].read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise recipe_error(name, f"the YAML does not parse: {exc}") from exc
    return parse_recipe(name, data)


def get_recipe(name: str) -> Recipe:
    """
    Load one recipe.

    Args:
        name: The recipe's name.

    Returns:
        The recipe, validated.

    Raises:
        RecipeNotFoundError: When no recipe has that name.
        RecipeError: When its file is not valid.
    """
    files = _recipe_files()
    if not NAME_PATTERN.match(name or "") or name not in files:
        raise RecipeNotFoundError(
            f"No recipe named {name!r}",
            details=f"Available recipes: {', '.join(sorted(files))}. See: noust recipe list",
        )
    return _load(name)


def list_recipes() -> list[Recipe]:
    """
    Load every recipe the package ships.

    Returns:
        The recipes, the available ones first, each group by title.

    Raises:
        RecipeError: When a file is not valid.
    """
    recipes = [get_recipe(name) for name in _recipe_files()]
    return sorted(recipes, key=lambda recipe: (not recipe.available, recipe.title.lower()))


def search_recipes(
    *,
    query: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    kind: str | None = None,
) -> list[Recipe]:
    """
    Search the catalogue.

    Every filter given must hold. The text query matches the name, title,
    slogan, description, category and tags, case-insensitively, and each word
    of it must be found somewhere: ``password manager`` finds Vaultwarden.

    Args:
        query: Words to look for.
        category: One of :data:`~noust.recipes.model.CATEGORIES`.
        tag: One tag, exactly.
        kind: ``native`` or ``compose``.

    Returns:
        The matching recipes, in the order :func:`list_recipes` gives.

    Raises:
        RecipeError: The category or the kind is not one of the closed lists.
    """
    if category is not None and category not in CATEGORIES:
        raise recipe_error_for_filter("category", category, CATEGORIES)
    if kind is not None and kind not in KINDS:
        raise recipe_error_for_filter("kind", kind, KINDS)
    words = (query or "").lower().split()
    wanted_tag = tag.lower() if tag else None
    found = []
    for recipe in list_recipes():
        if category is not None and recipe.category != category:
            continue
        if kind is not None and recipe.kind != kind:
            continue
        if wanted_tag is not None and wanted_tag not in recipe.tags:
            continue
        haystack = " ".join(
            (
                recipe.name,
                recipe.title,
                recipe.slogan,
                recipe.description,
                recipe.category,
                " ".join(recipe.tags),
            )
        ).lower()
        if all(word in haystack for word in words):
            found.append(recipe)
    return found


def recipe_error_for_filter(field: str, value: str, allowed: tuple[str, ...]) -> RecipeError:
    """
    Build the error for a filter that is not one of a closed list.

    Args:
        field: ``category`` or ``kind``.
        value: What was asked for.
        allowed: The closed list.

    Returns:
        The error, naming what is allowed.
    """
    return RecipeError(
        f"{value!r} is not a recipe {field}",
        details=f"Choose one of: {', '.join(allowed)}.",
        field=field,
    )


def categories() -> list[dict[str, Any]]:
    """
    Count the catalogue's recipes per category.

    Returns:
        Every category that has at least one recipe, in the closed list's
        order, with how many.
    """
    counts: dict[str, int] = {}
    for recipe in list_recipes():
        counts[recipe.category] = counts.get(recipe.category, 0) + 1
    return [{"category": entry, "count": counts[entry]} for entry in CATEGORIES if entry in counts]


def logo_svg(name: str) -> str:
    """
    Read a recipe's logo.

    Args:
        name: The recipe.

    Returns:
        The SVG file.

    Raises:
        RecipeNotFoundError: No recipe has that name, or it has no logo.
    """
    recipe = get_recipe(name)
    if recipe.logo is None:
        raise RecipeNotFoundError(f"{recipe.title} has no logo")
    return read_asset(name, recipe.logo)
