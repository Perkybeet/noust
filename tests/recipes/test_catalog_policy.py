# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The catalogue's policy on other people's names, marks, licences and logos (checklist D-25).

``docs/recipes.md`` says what a recipe may call, show and ship of a project that is not Noust's.
These tests are where that is held: a logo is Noust's own monogram unless a notice records the
project's mark, a file copied from a project's repository carries its licence, a licence that
is not an open source one is said in the description, and a recipe that is listed instead of
installed says where its reason was read and when.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from importlib import resources
from pathlib import Path

import pytest

from noust.recipes import get_recipe, list_recipes, logo_svg
from noust.recipes.model import Recipe

DOCS = Path(__file__).resolve().parents[2] / "docs" / "recipes.md"

#: The recipes this release lists with the reason a person can check, written in the 3.4 wave.
LISTED_IN_34 = {
    "appwrite",
    "bookstack",
    "cal-com",
    "coder",
    "hermes-agent",
    "home-assistant",
    "joplin-server",
    "minio",
    "openclaw",
    "portainer",
    "stalwart",
    "strapi",
    "supabase",
    "typebot",
}
#: Licences that are readable but not open source, and so are said in the description.
NOT_OPEN_SOURCE = re.compile(r"LicenseRef|BUSL|FSL|SSPL", re.IGNORECASE)


def assets_of(recipe: Recipe) -> Path:
    """The recipe's assets directory in the package."""
    return Path(str(resources.files("noust.recipes").joinpath("assets").joinpath(recipe.name)))


def monogram_letters(svg: str) -> str | None:
    """
    Read the letters of a Noust monogram.

    Args:
        svg: A logo.

    Returns:
        The one or two characters on the dark tile, or None when the logo is anything else
        (a path, a circle, more than one piece of text).
    """
    root = ET.fromstring(svg)  # noqa: S314 - a logo the package ships, read in a test
    names = [element.tag.rsplit("}", 1)[-1] for element in root.iter()]
    if sorted(names) != ["rect", "svg", "text", "title"]:
        return None
    tile = next(element for element in root.iter() if element.tag.endswith("rect"))
    text = next(element for element in root.iter() if element.tag.endswith("text"))
    letters = (text.text or "").strip()
    if tile.get("fill") != "#24292f" or not 1 <= len(letters) <= 2 or not letters.isalnum():
        return None
    return letters


@pytest.mark.parametrize("recipe", list_recipes(), ids=lambda recipe: recipe.name)
def test_a_logo_is_noust_s_monogram_unless_a_notice_records_the_project_s_mark(
    recipe: Recipe,
) -> None:
    """The mark of a project is its trademark: shipping it needs a record of why it may be."""
    letters = monogram_letters(logo_svg(recipe.name))

    if letters is None:
        notice = assets_of(recipe) / "LOGO-NOTICE"
        assert notice.is_file(), f"{recipe.name}'s logo is not the monogram and has no LOGO-NOTICE"
        text = notice.read_text()
        assert "http" in text and "licen" in text.lower(), "the source and the licence"
    else:
        assert letters.upper() == letters.upper().strip()


def test_the_monogram_check_refuses_a_real_mark() -> None:
    """A path, a circle or a second piece of text is not a monogram."""
    tile = '<rect width="64" height="64" rx="14" fill="#24292f"/>'
    word = '<text x="32" y="41" fill="#ffffff">GR</text>'
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><title>X</title>'

    assert monogram_letters(svg + tile + word + "</svg>") == "GR"
    assert monogram_letters(svg + tile + word + '<path d="M0 0h8v8z"/></svg>') is None
    assert monogram_letters(svg + tile + word + word + "</svg>") is None
    assert monogram_letters(svg + tile + '<text fill="#fff">LONG</text></svg>') is None
    assert monogram_letters(svg + tile.replace("#24292f", "#ff0000") + word + "</svg>") is None


def test_the_clickhouse_files_copied_from_plausible_carry_the_licence_they_came_with() -> None:
    """MIT asks that its notice travel with the files: a NOTICE names the source and holds the text."""
    directory = assets_of(get_recipe("plausible"))
    notice = (directory / "NOTICE").read_text()

    assert "github.com/plausible/community-edition" in notice and "v3.2.1" in notice
    assert "MIT License" in notice and "Copyright (c) 2024 Plausible Analytics" in notice
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in notice
    copied = {path.name for path in (directory / "clickhouse").iterdir()}
    assert all(name in notice for name in copied), copied


@pytest.mark.parametrize(
    "recipe",
    [
        recipe
        for recipe in list_recipes()
        if recipe.available and NOT_OPEN_SOURCE.search(recipe.license or "")
    ],
    ids=lambda recipe: recipe.name,
)
def test_a_licence_that_is_not_open_source_is_said_in_the_description(recipe: Recipe) -> None:
    """The contribution rule: a source-available or open-core recipe says so where the operator chooses it."""
    assert re.search(r"source-available|open core", recipe.description, re.IGNORECASE), recipe.name


def test_the_licences_that_are_not_open_source_are_the_ones_the_policy_names() -> None:
    """n8n, NocoDB, Directus, Open WebUI and Stirling PDF: the ones the docs name, and no other."""
    found = {
        recipe.name
        for recipe in list_recipes()
        if recipe.available and NOT_OPEN_SOURCE.search(recipe.license or "")
    }

    assert found == {"directus", "n8n", "nocodb", "open-webui", "stirling-pdf"}


@pytest.mark.parametrize("name", sorted(LISTED_IN_34))
def test_a_listed_recipe_says_where_its_reason_was_read_and_when(name: str) -> None:
    """A claim about someone else's project names the file or page and the day, or the tag."""
    recipe = get_recipe(name)
    reason = recipe.unavailable_reason or ""

    assert recipe.available is False
    assert reason.endswith("Not available in this release.")
    assert re.search(r"read (on |at )?(20\d\d-\d\d-\d\d|the v[\d.]+ tag)", reason), reason
    assert recipe.homepage.startswith("https://") and recipe.repository


def test_the_docs_say_how_many_recipes_there_are_and_list_the_ones_that_are_not_installable() -> (
    None
):
    """The count and the list are the catalogue's, so they cannot drift from it."""
    text = DOCS.read_text()
    recipes = list_recipes()
    installable = sum(recipe.available for recipe in recipes)
    listed = [recipe for recipe in recipes if not recipe.available]

    assert f"has {installable} recipes you can install and {len(listed)} that are listed" in text
    section = text.split("Listed, not installable.", 1)[1]
    for recipe in listed:
        assert f"| {recipe.title} |" in section, recipe.title
    for recipe in recipes:
        if recipe.available:
            assert f"| {recipe.title} | {recipe.version} |" in text, recipe.title


def test_the_docs_hold_the_policy_on_names_marks_licences_and_logos() -> None:
    """Checklist D-25: names, logos with attribution, licences, and the criterion of withdrawal."""
    text = DOCS.read_text()
    section = text.split("## Names, marks, licences and logos of the projects", 1)[1].split(
        "## Contributing a recipe", 1
    )[0]

    for needle in (
        "**Names.**",
        "**Logos.**",
        "**Licences.**",
        "**Withdrawal.**",
        "LOGO-NOTICE",
        "NOTICE",
        "source-available",
        "plausible.io/trademark",
        "not legal advice",
    ):
        assert needle in section, needle
