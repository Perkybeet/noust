# Copyright (c) 2024-2026 Yago Lopez Prado
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
