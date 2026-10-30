# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How much of its picture a mark draws, and a picture cropped to the mark.

A file drawn for a poster, a banner or a social card has a ``viewBox`` far larger than the figure
in it: Headscale's three dots sit in a canvas of 1280 by 640, VersityGW's symbol in a 150 by 53
banner. Drawn at 32 pixels in a list that is a point, and no rule of the console's policy says
so, because the file is valid. This module measures what the shapes of a file cover and, when it
is a small part of the picture, gives the picture the figure's own frame. Nothing of the artwork
changes: only the window it is seen through.

The measure is the extent of the points a shape is made of (the control points of a curve
included, so a curve that bulges is never cut) after the transforms that apply to it. A shape
that paints nothing (an unfilled frame as large as the picture) is not part of the figure.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET

__all__ = ["FIGURE_SHARE", "Box", "crop_to_figure", "figure_bounds", "figure_share"]

#: A box as ``(left, top, right, bottom)``.
Box = tuple[float, float, float, float]

#: The least a mark's figure covers of its picture (the area of its box over the area of the
#: picture) for the file to be left as it is: below this the picture is cropped to the figure.
FIGURE_SHARE = 0.6

#: Room left around the figure, as a share of its longer side.
MARGIN = 0.04

_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_PATH_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
_NONE = {"none", "transparent"}
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_ARGUMENTS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def _local(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _floats(text: str) -> list[float]:
    return [float(item) for item in _NUMBER.findall(text)]


def _multiply(
    first: tuple[float, float, float, float, float, float],
    second: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    """The matrix that applies ``second`` and then ``first`` (SVG's order of nesting)."""
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _transform_of(value: str | None) -> tuple[float, float, float, float, float, float] | None:
    """The matrix of a ``transform`` attribute; None when it holds something this does not read."""
    if not value or not value.strip():
        return _IDENTITY
    matrix = _IDENTITY
    consumed = 0
    for found in _TRANSFORM.finditer(value):
        consumed += len(found.group(0))
        kind, numbers = found.group(1), _floats(found.group(2))
        step: tuple[float, float, float, float, float, float]
        if kind == "matrix" and len(numbers) == 6:
            step = (numbers[0], numbers[1], numbers[2], numbers[3], numbers[4], numbers[5])
        elif kind == "translate" and numbers:
            step = (1.0, 0.0, 0.0, 1.0, numbers[0], numbers[1] if len(numbers) > 1 else 0.0)
        elif kind == "scale" and numbers:
            step = (numbers[0], 0.0, 0.0, numbers[1] if len(numbers) > 1 else numbers[0], 0.0, 0.0)
        elif kind == "rotate" and numbers:
            angle = math.radians(numbers[0])
            cos, sin = math.cos(angle), math.sin(angle)
            step = (cos, sin, -sin, cos, 0.0, 0.0)
            if len(numbers) == 3:
                about = (1.0, 0.0, 0.0, 1.0, numbers[1], numbers[2])
                back = (1.0, 0.0, 0.0, 1.0, -numbers[1], -numbers[2])
                step = _multiply(_multiply(about, step), back)
        elif kind == "skewX" and numbers:
            step = (1.0, 0.0, math.tan(math.radians(numbers[0])), 1.0, 0.0, 0.0)
        elif kind == "skewY" and numbers:
            step = (1.0, math.tan(math.radians(numbers[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            return None
        matrix = _multiply(matrix, step)
    leftover = _TRANSFORM.sub("", value)
    if consumed == 0 or leftover.strip(" ,\t\n"):
        return None
    return matrix


def _path_points(data: str) -> list[tuple[float, float]] | None:
    """Every point a path is made of: its ends and its control points; None when it is not readable."""
    tokens = _PATH_TOKEN.findall(data)
    points: list[tuple[float, float]] = []
    x = y = start_x = start_y = 0.0
    command = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in "Zz":
                x, y = start_x, start_y
                continue
        elif not command:
            return None
        upper = command.upper()
        relative = command.islower()
        count = _ARGUMENTS[upper]
        if upper == "A":
            # An arc's two flags may be written with no separator: "a1 1 0 011 1".
            args: list[float] = []
            try:
                args = [float(tokens[index + i]) for i in range(3)]
                index += 3
                for _flag in range(2):
                    text = tokens[index]
                    flag = text[0]
                    if flag not in "01":
                        return None
                    args.append(float(flag))
                    if len(text) > 1:
                        tokens[index] = text[1:]
                    else:
                        index += 1
                args.extend(float(tokens[index + i]) for i in range(2))
                index += 2
            except (IndexError, ValueError):
                return None
        else:
            if index + count > len(tokens) or any(
                t.isalpha() for t in tokens[index : index + count]
            ):
                return None
            args = [float(t) for t in tokens[index : index + count]]
            index += count
        if upper == "M":
            x, y = (x + args[0], y + args[1]) if relative else (args[0], args[1])
            start_x, start_y = x, y
            points.append((x, y))
            command = "l" if relative else "L"
        elif upper in ("L", "T"):
            x, y = (x + args[0], y + args[1]) if relative else (args[0], args[1])
            points.append((x, y))
        elif upper == "H":
            x = x + args[0] if relative else args[0]
            points.append((x, y))
        elif upper == "V":
            y = y + args[0] if relative else args[0]
            points.append((x, y))
        elif upper in ("C", "S", "Q"):
            pairs = [(args[i], args[i + 1]) for i in range(0, len(args), 2)]
            absolute = [(x + px, y + py) if relative else (px, py) for px, py in pairs]
            points.extend(absolute)
            x, y = absolute[-1]
        elif upper == "A":
            end = (x + args[5], y + args[6]) if relative else (args[5], args[6])
            # The arc can bulge past its ends by at most its radii: take them in.
            radius = max(abs(args[0]), abs(args[1]))
            points.extend(
                [(end[0] - radius, end[1] - radius), (end[0] + radius, end[1] + radius)]
                if radius < 2 * max(abs(args[5]), abs(args[6]), 1e-9)
                else [end]
            )
            points.append((x, y))
            x, y = end
    return points


def _shape_points(element: ET.Element, name: str) -> list[tuple[float, float]] | None:
    """The points of one shape in its own coordinates; None when it is not readable."""

    def numbers(*attributes: str) -> list[float] | None:
        found: list[float] = []
        for attribute in attributes:
            text = element.get(attribute)
            if text is None:
                found.append(0.0)
                continue
            match = _NUMBER.match(text.strip())
            if match is None:
                return None
            found.append(float(match.group(0)))
        return found

    if name == "path":
        return _path_points(element.get("d", ""))
    if name == "rect":
        box = numbers("x", "y", "width", "height")
        return None if box is None else [(box[0], box[1]), (box[0] + box[2], box[1] + box[3])]
    if name == "circle":
        circle = numbers("cx", "cy", "r")
        if circle is None:
            return None
        cx, cy, r = circle
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if name == "ellipse":
        ellipse = numbers("cx", "cy", "rx", "ry")
        if ellipse is None:
            return None
        cx, cy, rx, ry = ellipse
        return [(cx - rx, cy - ry), (cx + rx, cy + ry)]
    if name == "line":
        line = numbers("x1", "y1", "x2", "y2")
        return None if line is None else [(line[0], line[1]), (line[2], line[3])]
    if name in ("polyline", "polygon"):
        values = _floats(element.get("points", ""))
        if len(values) < 2 or len(values) % 2:
            return None
        return [(values[i], values[i + 1]) for i in range(0, len(values), 2)]
    return None


def _paints(element: ET.Element, fill: str, stroke: str) -> tuple[str, str]:
    own_fill, own_stroke = element.get("fill"), element.get("stroke")
    return (
        own_fill if own_fill not in (None, "inherit") and own_fill is not None else fill,
        own_stroke if own_stroke not in (None, "inherit") and own_stroke is not None else stroke,
    )


def figure_bounds(root: ET.Element) -> Box | None:
    """
    Measure what the shapes of a mark cover.

    Args:
        root: The mark's ``<svg>``.

    Returns:
        The box that holds every shape that paints something (a fill, or a stroke), or None when
        there is none, or one is made of something this does not read (text, a transform it does
        not know): a measure that might be wrong is not made.
    """
    left = top = math.inf
    right = bottom = -math.inf
    stack: list[tuple[ET.Element, tuple[float, float, float, float, float, float], str, str]] = [
        (root, _IDENTITY, "#000000", "none")
    ]
    while stack:
        element, matrix, fill, stroke = stack.pop()
        name = _local(element)
        if name in ("defs", "title", "desc", "metadata", "clipPath", "mask"):
            continue
        own = _transform_of(element.get("transform"))
        if own is None:
            return None
        matrix = _multiply(matrix, own)
        fill, stroke = _paints(element, fill, stroke)
        if name == "text":
            return None
        if name in ("path", "rect", "circle", "ellipse", "line", "polyline", "polygon"):
            paints_fill = fill.strip().lower() not in _NONE and name != "line"
            paints_stroke = stroke.strip().lower() not in _NONE
            if paints_fill or paints_stroke:
                points = _shape_points(element, name)
                if points is None or not points:
                    return None
                reach = 0.0
                if paints_stroke:
                    width = _floats(element.get("stroke-width", "1"))
                    reach = (width[0] if width else 1.0) / 2
                a, b, c, d, e, f = matrix
                scale = max(math.hypot(a, b), math.hypot(c, d))
                for px, py in points:
                    x, y = a * px + c * py + e, b * px + d * py + f
                    left, right = min(left, x - reach * scale), max(right, x + reach * scale)
                    top, bottom = min(top, y - reach * scale), max(bottom, y + reach * scale)
        stack.extend((child, matrix, fill, stroke) for child in reversed(list(element)))
    if left == math.inf:
        return None
    return (left, top, right, bottom)


def _view_box(root: ET.Element) -> Box | None:
    values = _floats(root.get("viewBox", ""))
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        return None
    return (values[0], values[1], values[0] + values[2], values[1] + values[3])


def figure_share(svg: str) -> float | None:
    """
    Say how much of its picture a mark's figure covers.

    Args:
        svg: The mark.

    Returns:
        The area of the box that holds the figure over the area of the picture, from 0 to 1 (a
        figure drawn past the picture counts as 1); None when the file has no ``viewBox`` or the
        figure cannot be measured.
    """
    root = ET.fromstring(svg)  # noqa: S314 - a mark that already passed the policy
    box, frame = figure_bounds(root), _view_box(root)
    if box is None or frame is None:
        return None
    width, height = box[2] - box[0], box[3] - box[1]
    if width <= 0 or height <= 0:
        return None
    share = (width * height) / ((frame[2] - frame[0]) * (frame[3] - frame[1]))
    return min(1.0, share)


def crop_to_figure(svg: str, *, minimum: float = FIGURE_SHARE) -> str:
    """
    Give a mark whose figure is a small part of its picture the figure's own frame.

    Args:
        svg: The mark, as the policy accepts it.
        minimum: The share of the picture's area the figure's box must already cover for the
            file to be left as it is.

    Returns:
        The same text when the figure covers at least ``minimum`` of the picture, or cannot be
        measured; else the same document with a ``viewBox`` that holds the figure and a little room.
    """
    share = figure_share(svg)
    if share is None or share >= minimum:
        return svg
    root = ET.fromstring(svg)  # noqa: S314 - a mark that already passed the policy
    box = figure_bounds(root)
    if box is None:
        return svg
    longer = max(box[2] - box[0], box[3] - box[1])
    room = longer * MARGIN
    left, top = box[0] - room, box[1] - room
    width, height = box[2] - box[0] + 2 * room, box[3] - box[1] + 2 * room
    if width <= 0 or height <= 0:
        return svg
    new = f"{_trim(left)} {_trim(top)} {_trim(width)} {_trim(height)}"
    return re.sub(r'viewBox="[^"]*"', f'viewBox="{new}"', svg, count=1)


def _trim(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"
