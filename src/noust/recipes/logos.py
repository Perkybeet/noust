# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A recipe's logo: an SVG shipped in the package, held to what the console's CSP allows (or, where a
project has no SVG, a PNG of 256 pixels or more with nothing in it but pixels: :mod:`noust.recipes.logo_png`).

The console's Content Security Policy forbids inline styles, scripts and every
fetch from another origin (``docs/DESIGN.md``, ``server.py``). A logo is served
from the same origin as an image, so it has to be an SVG that needs none of
those: no ``<style>``, no ``style=`` attribute, no ``<script>``, no event
handler, no ``href`` or ``xlink:href``, no ``data:``, no ``<foreignObject>``, no
entity, and a ``url(...)`` only as ``url(#id)`` to a gradient or a clip path the
file itself defines. The check is a closed list of elements and attributes rather
than a list of what is dangerous, because the list of what is dangerous is never
complete. :mod:`noust.recipes.logo_sanitize` turns a file somebody else drew into
one that passes it.

The logo is never fetched: it is a file that a person committed. It is one of two
things (``logo:`` in the recipe):

- ``logo.svg``, a file of the recipe in ``recipes/assets/<recipe>/``: the monogram
  drawn for Noust (two letters on a dark tile, under Noust's licence), or the
  project's own mark with a ``LOGO-NOTICE`` beside it that says where it was taken
  from, under what licence and what the project's own rules on its logo say
  (:func:`parse_logo_notice`: a mark without them is not shipped);
- ``simple-icons:<slug>``, the mark the simple-icons collection holds for the
  project, whose file is ``recipes/assets/_simple-icons/<slug>.svg``. The recipe
  names it and does not copy it: one file serves the recipes that share a mark, and
  ``index.json`` beside it records the release it comes from, the mark's colour and its
  licence (the collection is CC0, and says where a mark is under another).

A project's mark is the project's trademark, and a licence on the file is not a right
in the brand: a project whose own guidelines forbid a third party's use of its logo
(Plausible's, TREK's) is given the monogram, never its mark.
``docs/recipes-format.md`` says what a contributor may add.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from noust.recipes.assets import checked_asset, read_asset, read_asset_bytes, simple_icon_index
from noust.recipes.logo_png import validate_png
from noust.recipes.model import (
    SIMPLE_ICONS_LICENSE,
    SIMPLE_ICONS_PREFIX,
    LogoCredit,
    RecipeError,
    logo_source,
    recipe_error,
    simple_icon_slug,
)

__all__ = [
    "MAX_LOGO_BYTES",
    "NOTICE_FILE",
    "SIMPLE_ICONS_LICENSE",
    "SIMPLE_ICONS_PREFIX",
    "LogoNotice",
    "check_logo",
    "logo_credit",
    "logo_source",
    "parse_logo_notice",
    "simple_icon_brand",
    "simple_icon_slug",
    "validate_svg",
]

#: The largest logo shipped: a mark drawn with detail, after its numbers are rounded, fits.
MAX_LOGO_BYTES = 16 * 1024

#: The most elements and the deepest nesting a logo may have: a mark is a few dozen shapes.
MAX_LOGO_ELEMENTS = 250
MAX_LOGO_DEPTH = 12

#: What the collection calls a file: lower-case letters and digits (``nextdotjs``). The slug
#: becomes part of a path, so it is judged whole.
_SLUG = re.compile(r"[a-z0-9]{1,60}")

SVG_NAMESPACE = "http://www.w3.org/2000/svg"

ALLOWED_ELEMENTS = frozenset(
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
        "defs",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
    }
)

#: What only a gradient holds, and only a gradient or a clip path sits in ``<defs>`` for.
GRADIENTS = frozenset({"linearGradient", "radialGradient"})
PAINT_ATTRIBUTES = frozenset({"fill", "stroke"})

ALLOWED_ATTRIBUTES = frozenset(
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
        "stroke-miterlimit",
        "stroke-dasharray",
        "stroke-dashoffset",
        "stroke-opacity",
        "opacity",
        "transform",
        "id",
        "gradientUnits",
        "gradientTransform",
        "spreadMethod",
        "fx",
        "fy",
        "offset",
        "stop-color",
        "stop-opacity",
        "clip-path",
        "clipPathUnits",
        "font-family",
        "font-size",
        "font-weight",
        "text-anchor",
        "dominant-baseline",
    }
)

#: What an attribute value may hold: plain numbers, colours, keywords, path data.
PLAIN_VALUE = re.compile(r"[A-Za-z0-9#.,()%+\- _]*")
FORBIDDEN_IN_VALUE = re.compile(r"url\s*\(|javascript:|data:|expression\s*\(", re.IGNORECASE)

#: An id, and the one form a reference takes: ``url(#id)``, no spaces, no fallback.
PLAIN_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,47}")
REFERENCE = re.compile(r"url\(#([A-Za-z][A-Za-z0-9_-]{0,47})\)")

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
        RecipeError: It is too large or too busy, not well-formed, declares an entity or a
            doctype, uses an element or attribute outside the closed list, holds a value
            that could reach another resource, or refers to an id that is not a gradient
            or a clip path of its own.
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
    if root.tag != f"{{{SVG_NAMESPACE}}}svg":
        raise recipe_error(name, f"{where} must be an <svg> in the SVG namespace")
    if "viewBox" not in root.attrib:
        raise recipe_error(name, f"{where} needs a viewBox, so it scales")
    ids: dict[str, str] = {}
    references: list[tuple[str, str, str, bool]] = []
    count = 0
    stack: list[tuple[ET.Element, int, tuple[str, ...]]] = [(root, 1, ())]
    while stack:
        element, depth, ancestors = stack.pop()
        count += 1
        if count > MAX_LOGO_ELEMENTS:
            raise recipe_error(name, f"{where} has more than {MAX_LOGO_ELEMENTS} elements")
        if depth > MAX_LOGO_DEPTH:
            raise recipe_error(name, f"{where} is nested more than {MAX_LOGO_DEPTH} deep")
        local = _local_name(name, where, element)
        _check_place(name, where, local, ancestors)
        inside_definition = any(a in GRADIENTS or a == "clipPath" for a in ancestors) or (
            local in GRADIENTS or local in {"clipPath", "stop"}
        )
        for attribute, value in element.attrib.items():
            if attribute not in ALLOWED_ATTRIBUTES:
                raise recipe_error(name, f"{where} uses the attribute {attribute!r}")
            reference = REFERENCE.fullmatch(value)
            if reference is not None and attribute in PAINT_ATTRIBUTES | {"clip-path"}:
                if inside_definition:
                    raise recipe_error(
                        name,
                        f"{where}: {attribute}={value!r} refers to something from inside a "
                        "clip path or a gradient",
                    )
                references.append((attribute, value, reference.group(1), attribute == "clip-path"))
                continue
            if FORBIDDEN_IN_VALUE.search(value) or not PLAIN_VALUE.fullmatch(value):
                raise recipe_error(name, f"{where}: {attribute}={value!r} is not a plain value")
            if attribute == "id":
                if PLAIN_ID.fullmatch(value) is None:
                    raise recipe_error(name, f"{where}: id {value!r} is not a plain id")
                if value in ids:
                    raise recipe_error(name, f"{where}: more than one element has the id {value!r}")
                ids[value] = local
        if not _TEXT.fullmatch(element.text or "") or (element.tail or "").strip():
            raise recipe_error(name, f"{where} holds text other than a short plain label")
        stack.extend((child, depth + 1, (*ancestors, local)) for child in reversed(list(element)))
    for attribute, value, target, is_clip in references:
        kind = ids.get(target)
        if kind is None:
            raise recipe_error(name, f"{where}: {attribute}={value!r}, but no element has that id")
        if is_clip and kind != "clipPath":
            raise recipe_error(name, f"{where}: {attribute}={value!r} is not a clip path")
        if not is_clip and kind not in GRADIENTS:
            raise recipe_error(name, f"{where}: {attribute}={value!r} is not a gradient")
    return text


def _local_name(name: str, where: str, element: ET.Element) -> str:
    """
    Args:
        name: The recipe, for the message.
        where: What is being checked, for the message.
        element: One element of the document.

    Returns:
        Its name without the namespace.

    Raises:
        RecipeError: It is outside the SVG namespace or not in the closed list.
    """
    tag = element.tag
    if not isinstance(tag, str) or not tag.startswith(f"{{{SVG_NAMESPACE}}}"):
        raise recipe_error(name, f"{where} has an element outside the SVG namespace")
    local = tag.rsplit("}", 1)[1]
    if local not in ALLOWED_ELEMENTS:
        raise recipe_error(name, f"{where} uses <{local}>, which is not allowed")
    return local


#: What each element that holds only some others may hold.
_ONLY_INSIDE: dict[str, frozenset[str]] = {
    "defs": GRADIENTS | {"clipPath"},
    "linearGradient": frozenset({"stop"}),
    "radialGradient": frozenset({"stop"}),
    "stop": frozenset(),
    "clipPath": frozenset({"rect", "circle", "ellipse", "line", "polyline", "polygon", "path"}),
}


def _check_place(name: str, where: str, local: str, ancestors: tuple[str, ...]) -> None:
    """
    Check that an element sits where it means something.

    Args:
        name: The recipe, for the message.
        where: What is being checked, for the message.
        local: The element's name.
        ancestors: The names of its ancestors, the root first.

    Raises:
        RecipeError: A gradient or a clip path outside ``<defs>``, a ``<stop>`` outside a gradient,
            or an element where its parent does not take it.
    """
    parent = ancestors[-1] if ancestors else None
    if local in GRADIENTS | {"clipPath"} and parent != "defs":
        raise recipe_error(name, f"{where} has <{local}> outside <defs>")
    if local == "defs" and len(ancestors) != 1:
        raise recipe_error(name, f"{where} has <defs> anywhere but at the top")
    if local == "stop" and parent not in GRADIENTS:
        raise recipe_error(name, f"{where} has a <stop> outside a gradient")
    if parent in _ONLY_INSIDE and local not in _ONLY_INSIDE[parent]:
        raise recipe_error(name, f"{where} has <{local}> inside <{parent}>")


def check_logo(name: str, logo: Any) -> str:
    """
    Check a recipe's ``logo:`` whichever of the two things it names.

    Args:
        name: The recipe, for the message.
        logo: What the recipe wrote.

    Returns:
        The logo as written.

    Raises:
        RecipeError: A slug the collection would not call a file, a file that is not in the
            recipe's assets, or one the console's policy would refuse.
    """
    if isinstance(logo, str) and logo.startswith(SIMPLE_ICONS_PREFIX):
        slug = logo[len(SIMPLE_ICONS_PREFIX) :]
        if _SLUG.fullmatch(slug) is None:
            raise recipe_error(
                name,
                f"logo {logo!r} is not a simple-icons slug: lower-case letters and digits, "
                "as the collection names its files",
            )
        return logo
    asset = checked_asset(name, logo)
    if asset.endswith(".svg"):
        validate_svg(name, read_asset(name, asset))
    elif asset.endswith(".png"):
        validate_png(name, read_asset_bytes(name, asset))
    else:
        raise recipe_error(name, f"logo {asset!r} must be an .svg or a .png file")
    return asset


#: The file beside a logo that says where it comes from.
NOTICE_FILE = "LOGO-NOTICE"

#: An SPDX expression, or a reference to terms the project gave in writing (``LicenseRef-brand-kit``).
_LICENCE = re.compile(
    r"(?:LicenseRef-[A-Za-z0-9.-]+|[A-Za-z0-9.+-]+)"
    r"(?: (?:AND|OR|WITH) (?:LicenseRef-[A-Za-z0-9.-]+|[A-Za-z0-9.+-]+))*"
)
#: The line a GPL or AGPL text begins with. It is the licence's authorship, copied into a project's
#: LICENSE file, and not who drew the project's logo.
_LICENCE_AUTHOR = re.compile(r"Free Software Foundation|\bFSF\b", re.IGNORECASE)
_NOTICE_LINE = re.compile(r"([A-Za-z][A-Za-z ]*): (.+)")
_NOTICE_FIELDS = {
    "Mark of": "mark",
    "Source": "source",
    "Licence": "license",
    "Copyright": "copyright",
    "Terms": "terms",
    "Retrieved": "retrieved",
    "Changes": "changes",
    "Repaint": "repaint",
}

#: What a project's rules let the console do with the colours of its mark (the ``Repaint`` field):
#: nothing at all, or only draw a one-colour mark in pure black on the light stone and pure white
#: on the dark one. Without the field the console may use the stone's own ink and paper.
REPAINT_RULES = ("no", "white-black")

#: Words in a project's rules that say its mark is not to be changed: a notice that has them says
#: what the console may do about it (``Repaint``) instead of promising a file "shown unmodified"
#: that the console would then recolour.
FORBIDS_ALTERING = re.compile(
    r"unmodified|never altered|not be (?:changed|altered)|not to be (?:changed|altered)|as provided"
    r"|as it is drawn|as its designer drew|only (?:in )?(?:the )?white|non-approved colou?rs?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LogoNotice:
    """
    What a ``LOGO-NOTICE`` says.

    Attributes:
        source: The address of the file, at the commit or release it was read at (https).
        license: The licence of the file: an SPDX expression or ``LicenseRef-...``.
        retrieved: The day it was taken.
        mark: The project it is the mark of.
        copyright: The copyright line the licence asks to travel with the file.
        terms: What the project's own rules on its logo say (a trademark policy, a brand kit),
            and where and when they were read.
        changes: What was done to the file.
        repaint: What the console may do with the mark's colours on the stone of a theme: ``no``
            (draw it as it is) or ``white-black`` (pure black on the light stone, pure white on
            the dark one); empty when the project's rules say nothing and the stone's own ink and
            paper may stand in for a colour that would not read.
    """

    source: str
    license: str
    retrieved: date
    mark: str = ""
    copyright: str = ""
    terms: str = ""
    changes: str = ""
    repaint: str = ""


def parse_logo_notice(name: str, text: str) -> LogoNotice:
    """
    Read a ``LOGO-NOTICE``: one ``Field: value`` per line.

    Args:
        name: The recipe, for the message.
        text: The file.

    Returns:
        What it says.

    Raises:
        RecipeError: A line that is not a known field, a field twice, or no ``Source`` (https),
            ``Licence`` (SPDX) or ``Retrieved`` (a date): a mark is shipped only with the three.
    """
    where = f"{NOTICE_FILE} of {name}"
    found: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        matched = _NOTICE_LINE.fullmatch(line.strip())
        key = matched.group(1) if matched else ""
        if matched is None or key not in _NOTICE_FIELDS:
            raise recipe_error(
                name,
                f"{where} has a line that is not a field: {line.strip()[:40]!r}"
                + (f" ({key!r})" if key else ""),
            )
        if key in found:
            raise recipe_error(name, f"{where} has {key} twice")
        found[key] = matched.group(2).strip()
    for required in ("Source", "Licence", "Retrieved"):
        if required not in found:
            raise recipe_error(name, f"{where} has no {required} line")
    source = urlsplit(found["Source"])
    if (
        source.scheme != "https"
        or not source.hostname
        or "@" in source.netloc
        or re.search(r"[\s\x00-\x1f\x7f]", found["Source"])
    ):
        raise recipe_error(name, f"{where}: Source must be an https address with no credentials")
    if _LICENCE.fullmatch(found["Licence"]) is None:
        raise recipe_error(
            name,
            f"{where}: the licence {found['Licence']!r} is not an SPDX expression "
            "(MIT, Apache-2.0, CC-BY-4.0, or LicenseRef-<name> for terms the project gave in writing)",
        )
    if found.get("Repaint", "") not in ("", *REPAINT_RULES):
        raise recipe_error(
            name, f"{where}: Repaint is one of {', '.join(REPAINT_RULES)}, not {found['Repaint']!r}"
        )
    if FORBIDS_ALTERING.search(found.get("Terms", "")) and not found.get("Repaint"):
        raise recipe_error(
            name,
            f"{where}: the Terms say the mark is not to be changed, so the notice must say what the "
            f"console may do with its colours: Repaint: {' or '.join(REPAINT_RULES)}",
        )
    if _LICENCE_AUTHOR.search(found.get("Copyright", "")):
        raise recipe_error(
            name,
            f"{where}: the Copyright line names the Free Software Foundation, which wrote the "
            "text of the GPL and the AGPL and drew no logo; credit the authors or contributors "
            "of the project the mark comes from",
        )
    try:
        retrieved = date.fromisoformat(found["Retrieved"])
    except ValueError as exc:
        raise recipe_error(name, f"{where}: Retrieved is not a date (YYYY-MM-DD)") from exc
    return LogoNotice(
        source=found["Source"],
        license=found["Licence"],
        retrieved=retrieved,
        mark=found.get("Mark of", ""),
        copyright=found.get("Copyright", ""),
        terms=found.get("Terms", ""),
        changes=found.get("Changes", ""),
        repaint=found.get("Repaint", ""),
    )


def logo_credit(name: str, logo: str | None) -> LogoCredit | None:
    """
    Say where a recipe's logo comes from.

    Args:
        name: The recipe.
        logo: Its ``logo:`` as written.

    Returns:
        The licence and the address of a mark of the collection (from the package's index) or of
        a file with a ``LOGO-NOTICE``; None for the monogram, which is Noust's own, and for a
        notice that does not read (the package's tests refuse one, an operator's own recipe is
        its own business).
    """
    if logo is None:
        return None
    slug = simple_icon_slug(logo)
    if slug is not None:
        mark = simple_icon_index().get("marks", {}).get(slug)
        if not isinstance(mark, dict):
            return None
        version = simple_icon_index().get("version", "")
        return LogoCredit(
            license=str(mark.get("license", SIMPLE_ICONS_LICENSE)),
            origin=f"https://github.com/simple-icons/simple-icons/blob/{version}/icons/{slug}.svg",
        )
    try:
        notice = parse_logo_notice(name, read_asset(name, NOTICE_FILE))
    except RecipeError:
        return None
    return LogoCredit(license=notice.license, origin=notice.source)


def logo_repaint(name: str, logo: str | None) -> str:
    """
    Say what the console may do with the colours of a recipe's mark.

    Args:
        name: The recipe.
        logo: Its ``logo:`` as written.

    Returns:
        ``no``, ``white-black``, or an empty string when the project's rules say nothing (and for
        a mark of the collection, or the monogram, which have no notice).
    """
    if logo is None or simple_icon_slug(logo) is not None:
        return ""
    try:
        return parse_logo_notice(name, read_asset(name, NOTICE_FILE)).repaint
    except RecipeError:
        return ""


def simple_icon_brand(slug: str) -> str | None:
    """
    Args:
        slug: A mark of the collection.

    Returns:
        The brand's colour as six hex digits, from the package's index; None when it has none.
    """
    mark = simple_icon_index().get("marks", {}).get(slug)
    brand = mark.get("hex") if isinstance(mark, dict) else None
    return brand if isinstance(brand, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", brand) else None
