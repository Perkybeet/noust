# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A recipe's logo is held to what the console's CSP lets an image be."""

from __future__ import annotations

import pytest

from noust.recipes import RecipeError
from noust.recipes.logos import MAX_LOGO_BYTES, validate_svg

OK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Demo">'
    "<title>Demo</title>"
    '<rect width="64" height="64" rx="14" fill="#24292f"/>'
    '<text x="32" y="41" text-anchor="middle" font-family="sans-serif" font-size="26" '
    'font-weight="700" fill="#ffffff">DM</text>'
    '<path d="M4 4 L60 60" stroke="#fff" stroke-width="2"/>'
    "</svg>"
)


def test_a_plain_monogram_is_accepted() -> None:
    """Shapes, a short text, presentation attributes."""
    assert validate_svg("demo", OK) == OK


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (OK.replace("</svg>", "<script>alert(1)</script></svg>"), "<script>"),
        (OK.replace("</svg>", "<style>rect{fill:red}</style></svg>"), "<style>"),
        (OK.replace("<rect ", '<rect style="fill:red" '), "attribute 'style'"),
        (OK.replace("<rect ", '<rect onload="x()" '), "attribute 'onload'"),
        (OK.replace("<rect ", '<rect class="a" '), "attribute 'class'"),
        (OK.replace("</svg>", '<image href="https://evil.example/x.png"/></svg>'), "<image>"),
        (OK.replace("</svg>", '<use href="#a"/></svg>'), "<use>"),
        (OK.replace("</svg>", "<foreignObject><div/></foreignObject></svg>"), "<foreignObject>"),
        (OK.replace('fill="#24292f"', 'fill="url(#g)"'), "is not a plain value"),
        (OK.replace('fill="#24292f"', 'fill="javascript:alert(1)"'), "is not a plain value"),
        (OK.replace('fill="#24292f"', 'fill="data:image/png;base64,AAAA"'), "is not a plain value"),
        (OK.replace('fill="#24292f"', 'fill="red; x"'), "is not a plain value"),
        (OK.replace("DM", "<b>bold</b>"), "is not allowed"),
        (OK.replace("DM", "a" * 200), "short plain label"),
        (OK.replace("DM", "x</text>tail<text>"), "short plain label"),
        ('<!DOCTYPE svg [<!ENTITY a "b">]>' + OK, "doctype"),
        ('<?xml-stylesheet href="a.css"?>' + OK, "stylesheet"),
        (
            OK.replace('xmlns="http://www.w3.org/2000/svg"', 'xmlns="http://example.com/x"'),
            "SVG namespace",
        ),
        (OK.replace(' viewBox="0 0 64 64"', ""), "needs a viewBox"),
        ("<svg", "not well-formed"),
        ("not xml", "not well-formed"),
        (OK + " " * MAX_LOGO_BYTES, "larger than"),
    ],
)
def test_what_could_reach_another_resource_or_run_is_refused(bad: str, message: str) -> None:
    """A closed list of elements and attributes, and plain values."""
    with pytest.raises(RecipeError, match=message):
        validate_svg("demo", bad)


# ---------------------------------------------------------------------------
# A logo that is a mark of the simple-icons collection (CC0), not a file of the recipe
# ---------------------------------------------------------------------------

from typing import Any  # noqa: E402

from noust.recipes import RecipeNotFoundError, parse_recipe  # noqa: E402
from noust.recipes.logos import (  # noqa: E402
    SIMPLE_ICONS_LICENSE,
    check_logo,
    logo_license,
    logo_source,
    simple_icon_slug,
)
from tests.recipes.factory import compose_recipe, use_demo_assets  # noqa: E402


@pytest.fixture
def demo_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the recipe named ``demo`` use the logo of a shipped recipe as its own."""
    use_demo_assets(monkeypatch)


def test_a_slug_names_the_mark_and_a_file_name_is_the_recipe_s_own(demo_assets: None) -> None:
    """``simple-icons:gitea`` is the collection's mark; ``logo.svg`` a file of the recipe."""
    assert simple_icon_slug("simple-icons:gitea") == "gitea"
    assert simple_icon_slug("logo.svg") is None
    assert simple_icon_slug(None) is None
    assert logo_source("simple-icons:gitea") == "simple-icons"
    assert logo_source("logo.svg") == "asset"
    assert logo_source(None) is None
    assert logo_license("simple-icons:gitea") == SIMPLE_ICONS_LICENSE == "CC0-1.0"
    assert logo_license("logo.svg") is None


@pytest.mark.parametrize(
    "slug",
    ["", "Gitea", "git ea", "git-ea", "../gitea", "gitea.svg", "gitea/x", "a" * 61, "gitea\n"],
)
def test_a_slug_is_what_the_collection_calls_its_files_and_nothing_else(slug: str) -> None:
    """Lower-case letters and digits: the slug becomes part of a path, so it is judged whole."""
    with pytest.raises(RecipeError, match="simple-icons slug"):
        check_logo("demo", f"simple-icons:{slug}")


def test_a_recipe_may_name_a_mark_instead_of_shipping_a_file(demo_assets: None) -> None:
    """The recipe loads without an SVG of its own, and says where its logo comes from."""
    recipe = parse_recipe("demo", compose_recipe(logo="simple-icons:gotify"))

    assert recipe.logo == "simple-icons:gotify"
    assert recipe.logo_icon == "gotify"
    summary: dict[str, Any] = recipe.summary()
    assert summary["logo_source"] == "simple-icons"
    assert summary["logo_icon"] == "gotify"
    assert summary["logo_license"] == "CC0-1.0"


def test_a_recipe_with_a_file_of_its_own_says_so(demo_assets: None) -> None:
    """The monogram drawn for Noust is a file of the recipe, under Noust's licence."""
    summary = parse_recipe("demo", compose_recipe()).summary()

    assert summary["logo_source"] == "asset"
    assert summary["logo_icon"] is None
    assert summary["logo_license"] is None


def test_a_slug_that_is_not_packaged_is_a_logo_that_is_not_found(demo_assets: None) -> None:
    """The file is the package's to carry: until it does, the API answers 404, not a guess."""
    from noust.recipes.assets import read_simple_icon

    with pytest.raises(RecipeNotFoundError, match="not-a-mark"):
        read_simple_icon("not-a-mark")
