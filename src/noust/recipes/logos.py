# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A recipe's logo: an SVG shipped in the package, held to what the console's CSP allows.

The console's Content Security Policy forbids inline styles, scripts and every
fetch from another origin (``docs/DESIGN.md``, ``server.py``). A logo is served
from the same origin as an image, so it has to be an SVG that needs none of
those: no ``<style>``, no ``style=`` attribute, no ``<script>``, no event
handler, no link to anything (``href``, ``xlink:href``, ``url(...)``,
``data:``), no ``<foreignObject>``, no entity. The check is a closed list of
elements and attributes rather than a list of what is dangerous, because the
list of what is dangerous is never complete.

The logo is never fetched: it is a file in ``recipes/assets/<recipe>/`` that a
person committed. A logo that is the project's own mark is the project's
trademark, so the ones this release ships are plain monograms drawn for Noust;
``docs/recipes.md`` says what a contributor may add.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from noust.recipes.model import recipe_error

#: The largest logo shipped.
MAX_LOGO_BYTES = 8 * 1024

_SVG_NAMESPACE = "http://www.w3.org/2000/svg"

_ELEMENTS = frozenset(
    {
        "svg",
        "g",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "path",
        "text",
        "title",
    }
)

_ATTRIBUTES = frozenset(
    {
        "xmlns",
        "viewBox",
        "width",
        "height",
        "role",
        "aria-label",
        "aria-hidden",
        "focusable",
        "x",
        "y",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "x1",
        "y1",
        "x2",
        "y2",
        "d",
        "points",
        "fill",
        "fill-rule",
        "clip-rule",
        "fill-opacity",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-opacity",
        "opacity",
        "transform",
        "font-family",
        "font-size",
        "font-weight",
        "text-anchor",
        "dominant-baseline",
    }
)

#: What an attribute value may hold: plain numbers, colours, keywords, path data.
_VALUE = re.compile(r"^[A-Za-z0-9#.,()%+\- _]*$")
_FORBIDDEN_IN_VALUE = re.compile(r"url\s*\(|javascript:|data:|expression\s*\(", re.IGNORECASE)

#: The text of ``<text>`` and ``<title>``: a monogram or a name, nothing more.
_TEXT = re.compile(r"^\s*[A-Za-z0-9 .+-]{0,40}\s*$")


def validate_svg(name: str, text: str) -> str:
    """
    Check that a logo can be served under the console's CSP.

    Args:
        name: The recipe, for the message.
        text: The SVG file.

    Returns:
        The text, unchanged.

    Raises:
        RecipeError: It is too large, not well-formed, declares an entity or a
            doctype, uses an element or attribute outside the closed list,
            or holds a value that could reach another resource.
    """
    where = f"logo of {name}"
    if len(text.encode()) > MAX_LOGO_BYTES:
        raise recipe_error(name, f"{where} is larger than {MAX_LOGO_BYTES // 1024} KiB")
    if re.search(r"<!(?:DOCTYPE|ENTITY)|<\?xml-stylesheet", text, re.IGNORECASE):
        raise recipe_error(name, f"{where} declares a doctype, an entity or a stylesheet")
    try:
        # Package data, with doctypes and entities refused above: expat has
        # nothing to expand.
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError as exc:
        raise recipe_error(name, f"{where} is not well-formed XML: {exc}") from exc
    if root.tag != f"{{{_SVG_NAMESPACE}}}svg":
        raise recipe_error(name, f"{where} must be an <svg> in the SVG namespace")
    if "viewBox" not in root.attrib:
        raise recipe_error(name, f"{where} needs a viewBox, so it scales")
    for element in root.iter():
        tag = element.tag
        if not isinstance(tag, str) or not tag.startswith(f"{{{_SVG_NAMESPACE}}}"):
            raise recipe_error(name, f"{where} has an element outside the SVG namespace")
        local = tag.rsplit("}", 1)[1]
        if local not in _ELEMENTS:
            raise recipe_error(name, f"{where} uses <{local}>, which is not allowed")
        for attribute, value in element.attrib.items():
            if attribute not in _ATTRIBUTES:
                raise recipe_error(name, f"{where} uses the attribute {attribute!r}")
            if _FORBIDDEN_IN_VALUE.search(value) or not _VALUE.fullmatch(value):
                raise recipe_error(name, f"{where}: {attribute}={value!r} is not a plain value")
        if not _TEXT.fullmatch(element.text or "") or (element.tail or "").strip():
            raise recipe_error(name, f"{where} holds text other than a short plain label")
    return text
