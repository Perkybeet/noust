# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A logo on the console's light stone and on its dark stone.

A logo is an image, drawn by its designer for a background of their choosing, and the console has two:
the light stone and the dark one. A black mark is lost on the second, a white one on the first. The
console asks for the logo for the theme it is showing (``GET /api/recipes/{name}/logo?scheme=dark``)
and this is what answers: the file, with the colours that would not read on that stone changed and
nothing else.

What changes, and what does not:

- A mark of **one colour** keeps it when it reads on the stone (a contrast of 2 to 1 against the
  darkest and the lightest surface of the theme), and otherwise is drawn in the stone's own ink or paper. The marks of the simple-icons collection have no
  colour of their own, and take the brand's.
- A mark of **black, white and greys** is inverted on the theme that would lose its figure.
- A mark with **dark ink beside a colour** (nothing light in it) has its ink turned to paper on the
  dark stone, and the other way about on the light one: the ink is the figure, drawn on the page.
- Anything else is left as its designer drew it: a mark on a tile of its own (a shape that covers the
  picture: the monogram), a gradient, several colours. A logo whose
  guidelines say "do not recolour it" is a logo of that kind, or is shown in the one colour its
  brand uses; the colour is never invented, only the stone's ink or paper stands in for it.

The result goes through :func:`noust.recipes.logos.validate_svg` again: the guard is at the one place
a logo leaves the package, and what this writes is a colour in an attribute the guard already knew.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from noust.recipes.logos import SVG_NAMESPACE, validate_svg

__all__ = [
    "DARK_SURFACES",
    "INK",
    "LIGHT_SURFACES",
    "PAPER",
    "SCHEMES",
    "adapt_to_scheme",
    "contrast",
]

ET.register_namespace("", SVG_NAMESPACE)

#: What the console draws behind a logo, the darkest and the lightest of each theme (``tokens.css``).
LIGHT_SURFACES = ("#d9ddd5", "#fbfcfa")
DARK_SURFACES = ("#1b201e", "#323a36")
#: The stone's own ink and paper: the page of the dark theme and the page of the light one.
INK = "#1d2220"
PAPER = "#e5e8e3"
SCHEMES = ("light", "dark")

#: A logo is exempt from the 3 to 1 WCAG 2.2 asks of a graphic (1.4.11, "logotypes"), but a mark nobody
#: can see is no logo: black on the dark stone is 1.1 to 1, pale yellow on the light one 1.2, and
#: the brand colours that are meant to be seen (a green, an orange) are 2.2 and more.
MIN_CONTRAST = 2.0

SHAPES = frozenset({"path", "rect", "circle", "ellipse", "polyline", "polygon", "line", "text"})
_NAMED = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "silver": (192, 192, 192),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "currentcolor": (0, 0, 0),
}
_HEX = re.compile(r"#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_RGB = re.compile(
    r"rgba?\(\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*,\s*([\d.]+%?)\s*(?:,\s*[\d.]+%?\s*)?\)",
    re.IGNORECASE,
)
_NONE = {"none", "transparent"}
_IMPLICIT = "implicit"


def _parse_colour(value: str) -> tuple[int, int, int] | None:
    """
    Args:
        value: A ``fill`` or ``stroke`` as the file writes it.

    Returns:
        Its red, green and blue, or None when it is not a colour this understands.
    """
    text = value.strip().lower()
    named = _NAMED.get(text)
    if named is not None:
        return named
    matched = _HEX.fullmatch(text)
    if matched is not None:
        digits = matched.group(1)
        if len(digits) in (3, 4):
            digits = "".join(character * 2 for character in digits[:3])
        return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)
    matched = _RGB.fullmatch(text)
    if matched is not None:
        parts: list[int] = []
        for part in matched.groups():
            number = float(part.rstrip("%"))
            parts.append(round(number * 2.55) if part.endswith("%") else round(number))
        return parts[0], parts[1], parts[2]
    return None


def _hex(colour: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, part)) for part in colour))


def _luminance(colour: tuple[int, int, int]) -> float:
    """
    Args:
        colour: Red, green and blue.

    Returns:
        Its relative luminance (WCAG 2.2).
    """
    linear = [
        (part / 255 / 12.92) if part / 255 <= 0.04045 else ((part / 255 + 0.055) / 1.055) ** 2.4
        for part in colour
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    """
    Args:
        first: A colour as ``#rrggbb``.
        second: Another.

    Returns:
        Their contrast ratio, from 1 to 21 (WCAG 2.2).

    Raises:
        ValueError: One is not a colour.
    """
    one, other = _parse_colour(first), _parse_colour(second)
    if one is None or other is None:
        raise ValueError("contrast takes two colours")
    high, low = sorted((_luminance(one), _luminance(other)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _reads(colour: tuple[int, int, int], scheme: str) -> bool:
    surfaces = LIGHT_SURFACES if scheme == "light" else DARK_SURFACES
    return min(contrast(_hex(colour), surface) for surface in surfaces) >= MIN_CONTRAST


def _saturation(colour: tuple[int, int, int]) -> float:
    high, low = max(colour), min(colour)
    return 0.0 if high == 0 else (high - low) / high


def _lightness(colour: tuple[int, int, int]) -> float:
    return sum(colour) / 765


def _mix(light: str, dark: str, amount: float) -> str:
    """
    Args:
        light: A colour as ``#rrggbb``.
        dark: Another.
        amount: 0 gives the first, 1 the second.

    Returns:
        The colour between them.
    """
    one, other = _parse_colour(light), _parse_colour(dark)
    if one is None or other is None:
        raise ValueError("mix takes two colours")
    red, green, blue = (round(a + (b - a) * amount) for a, b in zip(one, other, strict=True))
    return _hex((red, green, blue))


class _Paints:
    """What a logo is painted with: each colour in the order it is first drawn, and how often."""

    def __init__(self) -> None:
        self.counts: dict[tuple[int, int, int], int] = {}
        self.implicit_black = False
        self.complex = False
        #: Whether a shape fills the whole picture: the logo brings its own tile, so what is drawn
        #: on it is not a figure on the page and is not recoloured.
        self.backdrop = False

    def add(self, value: str, *, implicit: bool = False) -> None:
        """
        Args:
            value: A paint of a shape that is drawn.
            implicit: Whether it is the default black, which nothing in the file says.
        """
        if value.strip().lower() in _NONE:
            return
        colour = _parse_colour(value)
        if colour is None:
            self.complex = True  # a gradient, a variable, a name this does not know
            return
        self.counts[colour] = self.counts.get(colour, 0) + 1
        if implicit:
            self.implicit_black = True


def _collect(root: ET.Element) -> _Paints:
    """
    Args:
        root: The logo's ``<svg>``.

    Returns:
        The paints of every shape that is drawn (not those of ``<defs>``), with the fill and the
        stroke each shape inherits applied.
    """
    paints = _Paints()
    box = _view_box(root)
    # Each element with the fill and the stroke it inherits, and whether that fill is the default.
    stack: list[tuple[ET.Element, str, bool, str]] = [(root, "#000000", True, "none")]
    while stack:
        element, inherited_fill, default_fill, inherited_stroke = stack.pop()
        local = element.tag.rsplit("}", 1)[-1]
        if local == "defs":
            continue
        own_fill, own_stroke = element.get("fill"), element.get("stroke")
        sets_fill = own_fill not in (None, "inherit")
        fill = own_fill if sets_fill and own_fill is not None else inherited_fill
        default_fill = default_fill and not sets_fill
        stroke = (
            own_stroke
            if own_stroke not in (None, "inherit") and own_stroke is not None
            else inherited_stroke
        )
        if local in SHAPES:
            # A tile is something painted: an unfilled frame as big as the picture (Heimdall's file
            # begins with one) draws nothing, and the figure on it is on the page.
            if (
                box is not None
                and fill.strip().lower() not in _NONE
                and _fills_the_picture(element, local, box)
            ):
                paints.backdrop = True
            if local != "line":
                paints.add(fill, implicit=default_fill)
            paints.add(stroke)
        stack.extend((child, fill, default_fill, stroke) for child in reversed(list(element)))
    return paints


def _view_box(root: ET.Element) -> tuple[float, float, float, float] | None:
    """
    Args:
        root: The logo's ``<svg>``.

    Returns:
        Its viewBox as x, y, width and height, or None.
    """
    try:
        x, y, width, height = (
            float(part) for part in re.split(r"[\s,]+", root.get("viewBox", "").strip())
        )
    except ValueError:
        return None
    return (x, y, width, height) if width > 0 and height > 0 else None


def _number(value: str | None, whole: float) -> float | None:
    """
    Args:
        value: A length of a shape: a number, or a percentage of ``whole``.
        whole: What a hundred per cent is.

    Returns:
        The number, or None when it is not one.
    """
    if value is None:
        return None
    text = value.strip()
    try:
        return float(text[:-1]) * whole / 100 if text.endswith("%") else float(text)
    except ValueError:
        return None


def _fills_the_picture(
    element: ET.Element, local: str, box: tuple[float, float, float, float]
) -> bool:
    """
    Args:
        element: A shape of the logo.
        local: Its name.
        box: The logo's viewBox.

    Returns:
        Whether a rectangle, a circle or an ellipse covers nine tenths of the picture and sits in
        it: a tile the mark is drawn on. A shape with a transform is not judged.
    """
    left, top, width, height = box
    if element.get("transform"):
        return False
    if local == "rect":
        x = _number(element.get("x", "0"), width) or 0.0
        y = _number(element.get("y", "0"), height) or 0.0
        w, h = _number(element.get("width"), width), _number(element.get("height"), height)
        return (
            w is not None
            and h is not None
            and w >= 0.9 * width
            and h >= 0.9 * height
            and abs(x - left) <= 0.05 * width
            and abs(y - top) <= 0.05 * height
        )
    if local in ("circle", "ellipse"):
        cx, cy = _number(element.get("cx", "0"), width), _number(element.get("cy", "0"), height)
        rx = _number(element.get("r" if local == "circle" else "rx"), width)
        ry = _number(element.get("r" if local == "circle" else "ry"), height)
        return (
            None not in (cx, cy, rx, ry)
            and 2 * (rx or 0) >= 0.9 * width
            and 2 * (ry or 0) >= 0.9 * height
            and abs((cx or 0) - (left + width / 2)) <= 0.05 * width
            and abs((cy or 0) - (top + height / 2)) <= 0.05 * height
        )
    return False


def _plan(
    paints: _Paints,
    scheme: str,
    brand: str | None,
    ink: str = INK,
    paper: str = PAPER,
) -> dict[tuple[int, int, int], str]:
    """
    Decide which colours change, and to what.

    Args:
        paints: What the logo is painted with.
        scheme: ``light`` or ``dark``.
        brand: The brand's own colour, for a mark that has none, as six hex digits.
        ink: The colour a dark figure becomes on the light stone.
        paper: The colour a light figure becomes on the dark stone.

    Returns:
        Each colour that is replaced and its replacement; empty when the logo stays as it is.
    """
    colours = list(paints.counts)
    if not colours:
        return {}
    stone = ink if scheme == "light" else paper
    if len(colours) == 1:
        only = colours[0]
        if brand is not None and only == (0, 0, 0):
            parsed = _parse_colour(f"#{brand}")
            if parsed is not None:
                only_target = parsed
                return {only: _hex(only_target) if _reads(only_target, scheme) else stone}
        return {} if _reads(only, scheme) else {only: stone}
    luminance = {colour: _luminance(colour) for colour in colours}
    neutral = all(_saturation(colour) < 0.15 for colour in colours)
    dominant = max(colours, key=lambda colour: paints.counts[colour])
    if neutral:
        dark_figure = luminance[dominant] < 0.18
        if (scheme == "dark" and dark_figure) or (
            scheme == "light" and not dark_figure and luminance[dominant] > 0.6
        ):
            return {colour: _mix(paper, ink, _lightness(colour)) for colour in colours}
        return {}
    if scheme == "dark":
        if any(value > 0.45 for value in luminance.values()):
            return {}
        return {
            colour: paper
            for colour in colours
            if luminance[colour] < 0.05 and _saturation(colour) < 0.8
        }
    if any(value < 0.12 for value in luminance.values()):
        return {}
    return {
        colour: ink for colour in colours if luminance[colour] > 0.6 and _saturation(colour) < 0.3
    }


def adapt_to_scheme(
    name: str, svg: str, scheme: str, brand: str | None = None, repaint: str = ""
) -> str:
    """
    Change the colours of a logo that would not read on the stone of a theme.

    Args:
        name: The recipe, for the messages.
        svg: The logo, as the package holds it.
        scheme: ``light`` or ``dark``: the theme the console is showing.
        brand: The brand's colour as six hex digits, for a mark drawn without one (the
            simple-icons collection's); it is the colour of such a mark when it reads.
        repaint: What the project's rules let the console do: ``no`` leaves the file as it is,
            ``white-black`` draws a one-colour mark in pure black on the light stone and pure
            white on the dark one (and does not use the brand's colour); empty is the stone's own
            ink and paper.

    Returns:
        The SVG: the same text when nothing needs to change, else the same document with some
        colours replaced, checked again by :func:`noust.recipes.logos.validate_svg`.

    Raises:
        ValueError: ``scheme`` is not one of :data:`SCHEMES`, or ``brand`` is not a colour.
        RecipeError: The file does not pass the policy.
    """
    if scheme not in SCHEMES:
        raise ValueError(f"scheme must be one of {', '.join(SCHEMES)}, not {scheme!r}")
    if brand is not None and re.fullmatch(r"[0-9A-Fa-f]{6}", brand) is None:
        raise ValueError("brand must be six hex digits")
    validate_svg(name, svg)
    if repaint == "no":
        return svg
    root = ET.fromstring(svg)  # noqa: S314 - validated just above
    paints = _collect(root)
    if paints.complex or paints.backdrop:
        return svg
    black_white = repaint == "white-black"
    # A colour that is replaced by itself is not a change: a black mark on light stone is the file.
    plan = {
        old: new
        for old, new in _plan(
            paints,
            scheme,
            None if black_white else brand,
            "#000000" if black_white else INK,
            "#ffffff" if black_white else PAPER,
        ).items()
        if _parse_colour(new) != old
    }
    if not plan:
        return svg
    for element in root.iter():
        for attribute in ("fill", "stroke"):
            value = element.get(attribute)
            colour = _parse_colour(value) if value is not None else None
            if colour in plan:
                element.set(attribute, plan[colour])
    if paints.implicit_black and (0, 0, 0) in plan and root.get("fill") is None:
        root.set("fill", plan[(0, 0, 0)])
    return validate_svg(name, ET.tostring(root, encoding="unicode").replace(" />", "/>"))
