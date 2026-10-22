# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The catalogue: finding the recipes the package ships, searching and filtering them.

Recipes are files in this package (``<name>.yaml``), loaded and validated on
first use and kept: they cannot change while the process runs. The API and the
command line both read the catalogue here, so a search means the same thing in
both.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

import yaml

from noust.recipes import catalog_package
from noust.recipes.assets import read_asset, set_asset_root
from noust.recipes.model import (
    CATEGORIES,
    KINDS,
    NAME_PATTERN,
    Recipe,
    RecipeError,
    RecipeNotFoundError,
    recipe_error,
    set_location,
)
from noust.recipes.schema import parse_recipe
from noust.recipes.sources import (
    Problem,
    RecipeFile,
    folder_files,
    operator_directory,
    shipped_files,
)

_logger = logging.getLogger(__name__)

#: Shipped recipes already read, by name: the package cannot change while Noust
#: runs. Recipes from the operator's folder are read each time, because an
#: operator edits them while Noust is up.
_SHIPPED: dict[str, Recipe] = {}


def catalogue_files() -> tuple[dict[str, RecipeFile], list[Problem]]:
    """
    Find every recipe file the catalogue offers, and the files it refused.

    The package comes first; a catalogue package (signed, imported by the
    operator) may supersede a shipped recipe; the operator's own folder adds
    recipes and may not take a name another source already holds.

    Returns:
        Recipe name to file, and one problem per rejected file.
    """
    files = shipped_files()
    problems: list[Problem] = []
    packaged, package_problems = folder_files(
        catalog_package.catalog_directory(), "catalog", taken={}
    )
    if packaged:
        # A recipe of the package replaces a shipped one only while it is newer.
        supplied = catalog_package.usable_names(files, load_file)
        files.update({name: entry for name, entry in packaged.items() if name in supplied})
    problems.extend(package_problems)
    taken = {name: entry.origin for name, entry in files.items()}
    private, private_problems = folder_files(operator_directory(), "operator", taken=taken)
    files.update(private)
    problems.extend(private_problems)
    for name, entry in files.items():
        set_asset_root(name, None if entry.origin == "shipped" else entry.root)
        set_location(name, None if entry.origin == "shipped" else entry.location)
    return files, problems


def load_file(entry: RecipeFile) -> Recipe:
    """
    Read and validate one recipe file.

    Args:
        entry: The file, as :func:`catalogue_files` found it.

    Returns:
        The recipe, with the origin it was read from.

    Raises:
        RecipeError: Its file is not valid. Errors are not cached: the next
            call reads the file again.
    """
    cached = _SHIPPED.get(entry.name) if entry.origin == "shipped" else None
    if cached is not None:
        return cached
    try:
        data = yaml.safe_load(entry.read())
    except yaml.YAMLError as exc:
        raise recipe_error(entry.name, f"the YAML does not parse: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise recipe_error(entry.name, f"the file cannot be read: {exc}") from exc
    recipe = parse_recipe(entry.name, data)
    if recipe.origin != entry.origin:
        recipe = dataclasses.replace(recipe, origin=entry.origin)
    if entry.origin == "shipped":
        _SHIPPED[entry.name] = recipe
    return recipe


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
    files, _ = catalogue_files()
    if not NAME_PATTERN.match(name or "") or name not in files:
        raise RecipeNotFoundError(
            f"No recipe named {name!r}",
            details=f"Available recipes: {', '.join(sorted(files))}. See: noust recipe list",
        )
    return load_file(files[name])


def list_recipes() -> list[Recipe]:
    """
    Load every recipe the catalogue offers.

    A shipped recipe that is not valid is a bug and raises. A recipe of the
    operator's or of a catalogue package that is not valid is left out and
    reported by :func:`catalogue_problems`: one bad private file must not
    take the catalogue away.

    Returns:
        The recipes, the available ones first, each group by title.

    Raises:
        RecipeError: When a shipped recipe is not valid.
    """
    recipes, _ = _load_all()
    return sorted(recipes, key=lambda recipe: (not recipe.available, recipe.title.lower()))


def catalogue_problems() -> list[Problem]:
    """
    List what the catalogue could not read from the operator's folders.

    Returns:
        The rejected files and the recipes that failed validation, each with
        why and what to do.
    """
    return _load_all()[1]


def _load_all() -> tuple[list[Recipe], list[Problem]]:
    """
    Load every recipe, collecting the ones from outside the package that fail.

    Returns:
        The recipes, and the problems.

    Raises:
        RecipeError: When a shipped recipe is not valid.
    """
    files, problems = catalogue_files()
    recipes: list[Recipe] = []
    problems = list(problems)
    for entry in files.values():
        try:
            recipes.append(load_file(entry))
        except RecipeError as exc:
            if entry.origin == "shipped":
                raise
            problems.append(
                Problem(entry.name, entry.origin, entry.location, str(exc), exc.details)
            )
            _logger.debug("recipe %s is not usable: %s", entry.location, exc)
    return recipes, problems


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
