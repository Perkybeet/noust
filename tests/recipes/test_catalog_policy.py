# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The catalogue's policy on other people's names, marks, licences and logos (checklist D-25).

``docs/recipes-format.md`` says what a recipe may call, show and ship of a project that is not Noust's.
These tests are where that is held: a logo is Noust's own monogram unless a notice records the
project's mark, a file copied from a project's repository carries its licence, a licence that
is not an open source one is said in the description, and a recipe that is listed instead of
installed says where its reason was read and when.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date
from importlib import resources
from pathlib import Path

import pytest

from noust.recipes import get_recipe, list_recipes, logo_svg
from noust.recipes.logos import parse_logo_notice
from noust.recipes.model import Recipe

#: The catalogue as it ships (generated), and the policy and format a contributor follows.
CATALOG = Path(__file__).resolve().parents[2] / "docs" / "recipes-catalog.md"
FORMAT = Path(__file__).resolve().parents[2] / "docs" / "recipes-format.md"

#: The recipes this release lists with the reason a person can check, written in the 4.0 wave.
LISTED_IN_40 = {
    "appwrite",
    "cal-com",
    "hermes-agent",
    "joplin-server",
    "minio",
    "strapi",
    "supabase",
}
#: The forms a licence that is not free software takes in the catalogue: a name the project wrote
#: (`LicenseRef-...`, said in the project's words in the recipe's header), a Business Source
#: License, a Functional Source License, the Server Side Public License.
NOT_FREE_FORMS = re.compile(r"LicenseRef-|BUSL-1\.1|FSL-1\.1|SSPL-1\.0|Elastic-2\.0|FCL-1\.0")


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


#: Projects whose own text does not let a third party show their logo: whatever a collection holds, the
#: recipe carries the monogram (scripts/recipe_logos.py, FORBIDDEN, which says where each was read).
NO_THIRD_PARTY_LOGO = {"plausible", "trek", "leantime", "owncast", "typebot", "joplin-server"}

ICONS = Path(str(resources.files("noust.recipes").joinpath("assets").joinpath("_simple-icons")))


def _is_monogram(recipe: Recipe) -> bool:
    """Whether the logo is the one drawn for Noust: a picture (a PNG) never is."""
    return (
        recipe.logo is not None
        and recipe.logo.endswith(".svg")
        and monogram_letters(logo_svg(recipe.name)) is not None
    )


@pytest.mark.parametrize("recipe", list_recipes(), ids=lambda recipe: recipe.name)
def test_a_logo_is_a_mark_with_its_licence_and_source_or_noust_s_monogram(recipe: Recipe) -> None:
    """The mark of a project is its trademark: shipping it needs a record of why it may be."""
    if recipe.logo_icon is not None:
        assert recipe.name not in NO_THIRD_PARTY_LOGO, f"{recipe.name}'s own text forbids it"
        assert (ICONS / f"{recipe.logo_icon}.svg").is_file(), recipe.logo_icon
        assert recipe.logo_license, "the collection's index says what licence the mark is under"
        assert (recipe.logo_origin or "").startswith(
            "https://github.com/simple-icons/simple-icons/"
        )
        return
    notice = assets_of(recipe) / "LOGO-NOTICE"
    if _is_monogram(recipe):
        assert not notice.exists(), f"{recipe.name} has the monogram and a notice for another mark"
        assert recipe.logo_license is None and recipe.logo_origin is None
        return
    assert recipe.name not in NO_THIRD_PARTY_LOGO, f"{recipe.name}'s own text forbids its mark"
    assert notice.is_file(), f"{recipe.name}'s logo is not the monogram and has no LOGO-NOTICE"
    parsed = parse_logo_notice(recipe.name, notice.read_text())
    assert recipe.logo_license == parsed.license and recipe.logo_origin == parsed.source
    assert parsed.retrieved <= date.today() and parsed.terms, (
        "what the project says of its logo, read"
    )
    assert parsed.mark and parsed.changes


def test_a_project_that_forbids_a_third_party_s_logo_is_given_the_monogram() -> None:
    """Each of these has said so in a text a person can read (``FORBIDDEN`` in the tool says where)."""
    for name in NO_THIRD_PARTY_LOGO:
        recipe = get_recipe(name)
        assert recipe.logo_icon is None and recipe.logo_license is None
        assert monogram_letters(logo_svg(name)) is not None, name


def test_the_tool_and_the_test_forbid_the_same_projects() -> None:
    """One list in the tool that adds logos, held to the one a test refuses a mark for."""
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[2] / "scripts" / "recipe_logos.py"
    spec = importlib.util.spec_from_file_location("recipe_logos_policy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_logos_policy"] = module
    spec.loader.exec_module(module)

    assert set(module.FORBIDDEN) == NO_THIRD_PARTY_LOGO


def test_a_notice_stands_beside_a_mark_and_nowhere_else() -> None:
    """A LOGO-NOTICE of a recipe whose logo is the collection's or the monogram says nothing true."""
    packaged = Path(str(resources.files("noust.recipes").joinpath("assets")))
    with_notice = {entry.parent.name for entry in packaged.glob("*/LOGO-NOTICE")}
    marks = {
        recipe.name
        for recipe in list_recipes()
        if recipe.logo_icon is None and not _is_monogram(recipe)
    }

    assert with_notice == marks


def test_no_mark_is_kept_that_no_recipe_names() -> None:
    """A file nobody asked for is a mark shown for no reason."""
    named = {recipe.logo_icon for recipe in list_recipes()} - {None}
    kept = {entry.stem for entry in ICONS.glob("*.svg")}

    assert kept == named


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


def not_free() -> list[Recipe]:
    """The installable recipes whose project's licence is not free software."""
    return [r for r in list_recipes() if r.available and r.license and not r.free_software]


@pytest.mark.parametrize("recipe", not_free(), ids=lambda recipe: recipe.name)
def test_a_licence_that_is_not_free_software_is_said_in_the_description(recipe: Recipe) -> None:
    """The contribution rule: a source-available or open core recipe says so where the operator chooses it."""
    assert re.search(r"source-available|open core", recipe.description, re.IGNORECASE), recipe.name


@pytest.mark.parametrize("recipe", not_free(), ids=lambda recipe: recipe.name)
def test_a_licence_that_is_not_free_software_takes_one_of_the_forms_the_policy_names(
    recipe: Recipe,
) -> None:
    """Not an SPDX identifier of a free licence misspelt: a name the project wrote, or a licence of the family."""
    assert NOT_FREE_FORMS.search(recipe.license or ""), (recipe.name, recipe.license)


def test_the_projects_the_docs_name_as_source_available_are_not_free_software() -> None:
    """The Licences paragraph of docs/recipes-format.md names them; the label follows."""
    named = {
        "affine",
        "bugsink",
        "chatwoot",
        "directus",
        "infisical",
        "n8n",
        "nocodb",
        "open-webui",
        "outline",
        "planka",
        "stirling-pdf",
        "tandoor",
        "typebot",
        "vault",
    }

    assert named <= {recipe.name for recipe in not_free()}


@pytest.mark.parametrize("name", sorted(LISTED_IN_40))
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
    text = CATALOG.read_text()
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
    text = FORMAT.read_text()
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
        "Retrieved",
        "not legal advice",
    ):
        assert needle in section, needle


#: What a variable's name says it holds a credential in, and what a literal there may be without
#: being one: a switch, a duration, a count, a path or an address.
CREDENTIAL_NAME = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|_KEY|_PASS$|_PWD$|SALT)", re.IGNORECASE
)
NOT_A_CREDENTIAL = re.compile(
    r"(true|false|none|null|disabled|enabled|yes|no|on|off|\d+|\d+[smhdw]|\d+/\w+|file:.*"
    r"|https?://.*|/.*|user_provided)",
    re.IGNORECASE,
)


def declared_variables(recipe: Recipe) -> dict[str, dict[str, object]]:
    """The variables a recipe's file declares, as it wrote them (before the install fills them)."""
    import yaml

    text = Path(str(resources.files("noust.recipes").joinpath(f"{recipe.name}.yaml"))).read_text()
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    return dict(data.get("variables") or {})


@pytest.mark.parametrize(
    "recipe",
    [recipe for recipe in list_recipes() if recipe.available and recipe.kind == "compose"],
    ids=lambda recipe: recipe.name,
)
def test_no_recipe_ships_a_credential_or_the_sample_domain_as_a_default(recipe: Recipe) -> None:
    """The install makes every password (`generate`) and an address follows the deployment's domain.

    A default such as `postgres`, `AdminPassword123!` or `admin@app.example.com` is a way into a
    server that everybody who read the recipe knows: Chatto, Discourse, EverShop, Joomla, Trek,
    Authentik, GlitchTip, Kimai and Lavalink all had one.
    """
    for name, entry in declared_variables(recipe).items():
        literal = entry.get("default")
        if literal and CREDENTIAL_NAME.search(name):
            assert NOT_A_CREDENTIAL.fullmatch(str(literal)), (recipe.name, name, literal)
        for field in ("default", "value"):
            assert "example.com" not in str(entry.get(field, "")), (recipe.name, name)
            assert "example.org" not in str(entry.get(field, "")), (recipe.name, name)
