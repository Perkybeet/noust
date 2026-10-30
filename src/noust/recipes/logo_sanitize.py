# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Turn a logo somebody else drew into one the console may serve, or refuse it.

A logo a project publishes is an editor's file (Inkscape, Illustrator, Figma): a stylesheet, a
``style`` on each shape, metadata, ids that collide with the page's, perhaps a script, a link or a
raster picture. :func:`noust.recipes.logos.validate_svg` accepts a closed list of elements and
attributes and nothing else, so this is the one way a file from another project reaches the package:
it is read, what it draws is written again as plain shapes, gradients and clip paths, and the result
is held to that same check before it is returned. What cannot be drawn inside the policy (a raster
picture, a mask, a pattern, a text that needs a font) makes the file unusable, and it is refused
with the reason rather than drawn as another logo.

What is removed, whatever the file says: ``<script>``, ``<foreignObject>``, event handlers, every
``href`` that does not point at an id of the same file, animation, filters, ``url(...)`` anywhere but
``url(#id)``, a stylesheet (its rules are applied to the shapes and the element dropped), entities,
a doctype and processing instructions. Ids are renamed to the recipe's own, so that two logos
written into one page never meet.

``scripts/recipe_logos.py`` is the tool that calls it; nothing at run time does, because the package
only holds files this has already passed.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from noust.recipes.logos import (
    ALLOWED_ATTRIBUTES,
    FORBIDDEN_IN_VALUE,
    PLAIN_VALUE,
    SVG_NAMESPACE,
    validate_svg,
)
from noust.recipes.model import RecipeError, recipe_error

__all__ = ["MAX_SOURCE_BYTES", "compact_path", "sanitize_svg"]

#: The largest file read: an illustrated logo is a few hundred KiB, a bomb is more.
MAX_SOURCE_BYTES = 1024 * 1024
MAX_SOURCE_DEPTH = 64
#: The most elements written and the most ``<use>`` expanded: a use of a use is how a file grows.
MAX_OUTPUT_ELEMENTS = 1500
MAX_USES = 40

XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NAMESPACE)

SHAPES = frozenset({"rect", "circle", "ellipse", "line", "polyline", "polygon", "path"})
#: Taken out without a word: nothing a logo draws is lost.
DROPPED = frozenset(
    {
        "script",
        "foreignObject",
        "metadata",
        "desc",
        "title",
        "style",
        "filter",
        "animate",
        "animateTransform",
        "animateMotion",
        "animateColor",
        "set",
        "view",
        "cursor",
        "namedview",
    }
)
#: What the file needs to look right and the policy cannot carry: the file is refused.
UNDRAWABLE = frozenset({"image", "mask", "pattern", "text", "tspan", "textPath", "marker"})
FONT_ATTRIBUTES = frozenset(
    {"font-family", "font-size", "font-weight", "text-anchor", "dominant-baseline"}
)
#: Written by the sanitizer itself, never copied from the file.
OWN_ATTRIBUTES = frozenset(
    {"id", "role", "aria-label", "aria-hidden", "focusable", "xmlns", "viewBox"}
)
#: What a CSS declaration may set: the presentation attributes the policy lists.
PRESENTATION = frozenset(
    {
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
        "stop-color",
        "stop-opacity",
        "clip-path",
    }
)
#: Numbers worth rounding, and the ones where a transform's precision matters.
NUMERIC = frozenset(
    {
        "x",
        "y",
        "width",
        "height",
        "cx",
        "cy",
        "r",
        "rx",
        "ry",
        "x1",
        "y1",
        "x2",
        "y2",
        "fx",
        "fy",
        "offset",
        "stroke-width",
        "stroke-miterlimit",
        "stroke-dasharray",
        "stroke-dashoffset",
        "opacity",
        "fill-opacity",
        "stroke-opacity",
        "stop-opacity",
        "points",
    }
)
TRANSFORMS = frozenset({"transform", "gradientTransform"})
#: What a gradient is drawn with: a fraction of the shape, or a point of a matrix, where a decimal
#: place is a different share of the picture than in a path, so it keeps significant digits.
RELATIVE = frozenset({"x1", "y1", "x2", "y2", "cx", "cy", "r", "fx", "fy", "offset"})
GRADIENT_ATTRIBUTES = (
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "fx",
    "fy",
    "gradientUnits",
    "spreadMethod",
)
#: Decide whether an element is drawn at all, and what ``currentColor`` is inside it.
HIDING = frozenset({"display", "visibility", "color"})
GEOMETRY = ALLOWED_ATTRIBUTES - OWN_ATTRIBUTES - FONT_ATTRIBUTES - PRESENTATION

_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE | re.DOTALL)
_SELECTOR = re.compile(r"(?P<tag>[A-Za-z][A-Za-z0-9]*|\*)?(?P<rest>(?:[.#][A-Za-z_][\w-]*)*)")
_TITLE_CHARACTERS = re.compile(r"[^A-Za-z0-9 .+-]")


def sanitize_svg(name: str, source: str | bytes, *, title: str | None = None) -> str:
    """
    Write a logo again so that it passes the console's policy.

    Args:
        name: The recipe, for the messages and for the ids written.
        source: The file as the project published it.
        title: The mark's accessible name; the recipe's name when None.

    Returns:
        The SVG, already checked by :func:`noust.recipes.logos.validate_svg`.

    Raises:
        RecipeError: It is larger than any logo, not an SVG, declares entities, draws
            something the policy cannot carry (a raster picture, a mask, a pattern, a text),
            is too large or too busy once cleaned, or refers to an id it does not have.
    """
    return _Sanitizer(name, title or name).run(_decode(name, source))


def _decode(name: str, source: str | bytes) -> str:
    """
    Args:
        name: The recipe, for the message.
        source: The file.

    Returns:
        Its text.

    Raises:
        RecipeError: It is larger than :data:`MAX_SOURCE_BYTES` or not UTF-8.
    """
    size = len(source) if isinstance(source, bytes) else len(source.encode("utf-8", "replace"))
    if size > MAX_SOURCE_BYTES:
        raise recipe_error(name, f"logo of {name} is larger than {MAX_SOURCE_BYTES // 1024} KiB")
    if isinstance(source, bytes):
        try:
            return source.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise recipe_error(name, f"logo of {name} is not UTF-8 text") from exc
    return source


@dataclass
class _Rule:
    """One simple selector of a stylesheet and what it sets."""

    tag: str | None
    identifier: str | None
    classes: frozenset[str]
    declarations: dict[str, str]
    specificity: int
    order: int


class _Sanitizer:
    """One file, read once: its ids, its stylesheet, and the tree that is written."""

    def __init__(self, name: str, title: str) -> None:
        self.name = name
        self.where = f"logo of {name}"
        cleaned = _TITLE_CHARACTERS.sub("", title).strip()[:40]
        self.title = cleaned or _TITLE_CHARACTERS.sub("", name)[:40] or "logo"
        prefix = name[:30] if name[:1].isalpha() else f"l{name[:29]}"
        self.prefix = re.sub(r"[^A-Za-z0-9-]", "-", prefix) or "l"
        self.rules: list[_Rule] = []
        self.by_id: dict[str, ET.Element] = {}
        self.new_ids: dict[str, str] = {}
        self.defs: list[ET.Element] = []
        self.digits = 3
        self.count = 0
        self.uses = 0
        #: What ``currentColor`` is where the walk is: the ``color`` of the nearest ancestor that sets one.
        self.color = ""

    # -- reading ------------------------------------------------------------------------------

    def refuse(self, message: str) -> RecipeError:
        """
        Args:
            message: What is wrong with the file.

        Returns:
            The error to raise, in the validator's words.
        """
        return recipe_error(self.name, f"{self.where} {message}")

    def run(self, text: str) -> str:
        """
        Args:
            text: The decoded file.

        Returns:
            The cleaned SVG, checked.

        Raises:
            RecipeError: See :func:`sanitize_svg`.
        """
        if "\x00" in text:
            raise self.refuse("is not well-formed XML (it holds a NUL byte)")
        if re.search(r"<!ENTITY", text, re.IGNORECASE):
            raise self.refuse("declares entities, which are never expanded")
        text = self._without_doctype(text)
        try:
            # Entities and an internal doctype subset are refused above: expat has nothing to expand,
            # and ElementTree loads no external resource.
            root = ET.fromstring(text)  # noqa: S314
        except ET.ParseError as exc:
            raise self.refuse(f"is not well-formed XML: {exc}") from exc
        if root.tag != f"{{{SVG_NAMESPACE}}}svg":
            raise self.refuse("must be an <svg> in the SVG namespace")
        for element in root.iter():
            identifier = element.get("id")
            if identifier and identifier not in self.by_id:
                self.by_id[identifier] = element
        self._collect_rules(root)
        out = ET.Element(self._q("svg"))
        out.set("role", "img")
        out.set("viewBox", self._view_box(root))
        self._attributes(root, "svg", out.attrib, root=True)
        ET.SubElement(out, self._q("title")).text = self.title
        for child in root:
            self._emit(child, out, 1, ())
        self._prune(out)
        if not any(self._draws(child) for child in out if child.tag not in {self._q("title")}):
            raise self.refuse("draws nothing once it is cleaned")
        if self.defs:
            holder = ET.Element(self._q("defs"))
            holder.extend(self.defs)
            out.insert(1, holder)
        # ElementTree writes an empty element as `<path />`; the space is a byte a logo does not need.
        return validate_svg(self.name, ET.tostring(out, encoding="unicode").replace(" />", "/>"))

    @staticmethod
    def _q(local: str) -> str:
        return f"{{{SVG_NAMESPACE}}}{local}"

    def _without_doctype(self, text: str) -> str:
        """
        Args:
            text: The file.

        Returns:
            It without a plain doctype (Illustrator writes one with a public identifier).

        Raises:
            RecipeError: The doctype has an internal subset.
        """
        match = re.search(r"<!DOCTYPE\b", text, re.IGNORECASE)
        if match is None:
            return text
        simple = re.compile(r"<!DOCTYPE\b[^>\[]*>", re.IGNORECASE).match(text, match.start())
        if simple is None:
            raise self.refuse("has a doctype with an internal subset, where entities are declared")
        return text[: simple.start()] + text[simple.end() :]

    def _view_box(self, root: ET.Element) -> str:
        """
        Args:
            root: The file's ``<svg>``.

        Returns:
            The viewBox to write, from the file's own or from its width and height; it also
            decides how many decimals the numbers keep.

        Raises:
            RecipeError: The file has no size to scale.
        """
        raw = root.get("viewBox")
        values: list[float] | None = None
        if raw is not None:
            try:
                values = [float(part) for part in re.split(r"[\s,]+", raw.strip())]
            except ValueError:
                values = None
            if values is not None and len(values) != 4:
                values = None
        else:
            width = _length(root.get("width"))
            height = _length(root.get("height"))
            if width is not None and height is not None:
                values = [0.0, 0.0, width, height]
        if values is None or values[2] <= 0 or values[3] <= 0 or max(values) > 1e6:
            raise self.refuse("has no size to scale (a viewBox, or a width and a height)")
        span = max(values[2], values[3])
        self.digits = 3 if span <= 64 else 2 if span <= 1024 else 1
        return " ".join(_format(value, 3) for value in values)

    # -- the stylesheet -----------------------------------------------------------------------

    def _collect_rules(self, root: ET.Element) -> None:
        """
        Read the file's stylesheets into rules of simple selectors.

        Args:
            root: The file's ``<svg>``.
        """
        text = "".join(
            element.text or ""
            for element in root.iter(self._q("style"))
            if element.get("type") in (None, "", "text/css")
        )
        text = _without_at_rules(re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL))
        order = 0
        for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
            declarations = _declarations(block.group(2))
            for selector in block.group(1).split(","):
                parsed = _SELECTOR.fullmatch(selector.strip())
                if parsed is None or not (parsed.group("tag") or parsed.group("rest")):
                    continue
                rest = parsed.group("rest") or ""
                identifiers = re.findall(r"#([\w-]+)", rest)
                classes = frozenset(re.findall(r"\.([\w-]+)", rest))
                tag = parsed.group("tag")
                self.rules.append(
                    _Rule(
                        None if tag in (None, "*") else tag,
                        identifiers[0] if identifiers else None,
                        classes,
                        declarations,
                        100 * len(identifiers)
                        + 10 * len(classes)
                        + (1 if tag not in (None, "*") else 0),
                        order,
                    )
                )
                order += 1
        self.rules.sort(key=lambda rule: (rule.specificity, rule.order))

    # -- attributes ---------------------------------------------------------------------------

    def _attributes(
        self, element: ET.Element, local: str, target: dict[str, str], *, root: bool = False
    ) -> bool:
        """
        Write what an element looks like: presentation and geometry, the cascade applied.

        Args:
            element: The source element.
            local: Its name.
            target: Where the attributes go.
            root: Whether it is the file's ``<svg>``, whose size is written elsewhere.

        Returns:
            False when the element is not drawn (``display:none``), True otherwise.
        """
        found: dict[str, str] = {}
        classes = set((element.get("class") or "").split())
        if element.get("mask") is not None:
            raise self.refuse("uses a mask, which the console's policy cannot carry")
        for key, value in element.attrib.items():
            if key in PRESENTATION or key in GEOMETRY or key in TRANSFORMS or key in HIDING:
                found[key] = value
        for rule in self.rules:
            if (
                (rule.tag is None or rule.tag == local)
                and (rule.identifier is None or rule.identifier == element.get("id"))
                and rule.classes <= classes
            ):
                found.update(rule.declarations)
        found.update(_declarations(element.get("style") or ""))
        if found.get("display") == "none" or found.get("visibility") in {"hidden", "collapse"}:
            return False
        own = found.get("color", "").strip()
        if own and PLAIN_VALUE.fullmatch(own) and own.lower() not in {"currentcolor", "inherit"}:
            self.color = own
        for key, value in found.items():
            if root and key not in PRESENTATION:
                continue
            if key not in PRESENTATION and key not in GEOMETRY and key not in TRANSFORMS:
                continue
            written = self._value(key, value)
            if written is not None:
                target[key] = written
        return True

    def _value(self, key: str, value: str, *, gradient: bool = False) -> str | None:
        """
        Args:
            key: The attribute.
            value: What the file says, from an attribute, a rule or a ``style``.
            gradient: Whether it is an attribute of a gradient, whose numbers are relative.

        Returns:
            What to write, or None to leave the attribute out.

        Raises:
            RecipeError: A reference to an id the file does not have.
        """
        value = re.sub(r"\s*!important\s*$", "", value.strip(), flags=re.IGNORECASE)
        if not value:
            return None
        if value.lower() == "currentcolor" and self.color:
            value = self.color
        if "url(" in value.lower():
            if key not in {"fill", "stroke", "clip-path"}:
                return None
            matched = _URL.search(value)
            if matched is None or not matched.group(2).strip().startswith("#"):
                return None
            return f"url(#{self._reference(key, matched.group(2).strip()[1:])})"
        if key == "d":
            try:
                return compact_path(value, self.digits)
            except ValueError as exc:
                raise self.refuse(f"has path data that cannot be read: {exc}") from exc
        if key in TRANSFORMS or key == "offset" or (gradient and key in RELATIVE):
            value = _round_numbers(value, significant=6)
        elif key in NUMERIC:
            value = _round_numbers(value, self.digits)
        if FORBIDDEN_IN_VALUE.search(value) or not PLAIN_VALUE.fullmatch(value):
            return None
        return value

    # -- references ---------------------------------------------------------------------------

    def _reference(self, key: str, old: str) -> str:
        """
        Make sure what an id names is written, once, under an id of the recipe's own.

        Args:
            key: ``fill`` and ``stroke`` name a gradient, ``clip-path`` a clip path.
            old: The id in the file.

        Returns:
            The id written.

        Raises:
            RecipeError: The file has no such element, or it is not what the paint needs.
        """
        if old in self.new_ids:
            return self.new_ids[old]
        target = self.by_id.get(old)
        if target is None:
            raise self.refuse(f"paints with #{old}, which names nothing the file has")
        local = _local(target)
        gradient = local in {"linearGradient", "radialGradient"}
        if (key == "clip-path") == gradient or local not in {
            "linearGradient",
            "radialGradient",
            "clipPath",
        }:
            raise self.refuse(
                f"uses #{old} ({local}) where a {'clip path' if key == 'clip-path' else 'gradient'} is needed"
            )
        new = f"{self.prefix}-{len(self.new_ids) + 1}"
        self.new_ids[old] = new
        built = self._gradient(target, new) if gradient else self._clip(target, new)
        self.defs.append(built)
        return new

    def _gradient(self, source: ET.Element, new: str) -> ET.Element:
        """
        Args:
            source: A gradient of the file.
            new: The id to write it under.

        Returns:
            The gradient with the stops and attributes it borrows through ``href`` written in.

        Raises:
            RecipeError: It has no stops.
        """
        chain: list[ET.Element] = []
        seen: set[int] = set()
        current: ET.Element | None = source
        while current is not None and id(current) not in seen and len(chain) < 8:
            seen.add(id(current))
            chain.append(current)
            reference = current.get("href") or current.get(f"{{{XLINK_NAMESPACE}}}href") or ""
            current = self.by_id.get(reference[1:]) if reference.startswith("#") else None
            if current is not None and _local(current) not in {"linearGradient", "radialGradient"}:
                current = None
        out = ET.Element(self._q(_local(source) or "linearGradient"))
        out.set("id", new)
        for part in reversed(chain):
            for key in (*GRADIENT_ATTRIBUTES, "gradientTransform"):
                value = part.get(key)
                written = self._value(key, value, gradient=True) if value is not None else None
                if written is not None:
                    out.set(key, written)
        stops = next(
            (part for part in chain if any(_local(child) == "stop" for child in part)), None
        )
        if stops is None:
            raise self.refuse(f"has a gradient (#{source.get('id')}) without stops")
        for child in stops:
            if _local(child) != "stop":
                continue
            stop = ET.SubElement(out, self._q("stop"))
            self._attributes(child, "stop", stop.attrib)
            for key in [
                key for key in stop.attrib if key not in {"offset", "stop-color", "stop-opacity"}
            ]:
                del stop.attrib[key]
            if "offset" not in stop.attrib:
                stop.set("offset", "0")
        return out

    def _clip(self, source: ET.Element, new: str) -> ET.Element:
        """
        Args:
            source: A clip path of the file.
            new: The id to write it under.

        Returns:
            The clip path, written as the shapes it holds.

        Raises:
            RecipeError: It holds something that is not a shape.
        """
        out = ET.Element(self._q("clipPath"))
        out.set("id", new)
        units = source.get("clipPathUnits")
        if units in {"userSpaceOnUse", "objectBoundingBox"}:
            out.set("clipPathUnits", units)
        if source.get("transform"):
            written = self._value("transform", source.get("transform") or "")
            if written is not None:
                out.set("transform", written)
        for child in source:
            local = _local(child)
            if local is None or local in DROPPED:
                continue
            if local not in SHAPES:
                raise self.refuse(f"has a clip path that holds <{local}>, which is not a shape")
            shape = ET.SubElement(out, self._q(local))
            if not self._attributes(child, local, shape.attrib):
                out.remove(shape)
                continue
            for key in [
                key
                for key in shape.attrib
                if key in {"fill", "stroke", "clip-path"} and "url(" in shape.attrib[key]
            ]:
                del shape.attrib[key]
            self._count()
        return out

    # -- the tree -----------------------------------------------------------------------------

    def _count(self) -> None:
        self.count += 1
        if self.count > MAX_OUTPUT_ELEMENTS:
            raise self.refuse("is too large once its uses are written out")

    def _emit(
        self, source: ET.Element, parent: ET.Element, depth: int, trail: tuple[str, ...]
    ) -> None:
        """
        Write one element and what is under it.

        Args:
            source: The element of the file.
            parent: Where it goes.
            depth: How deep it is in the file.
            trail: The ids of the uses being expanded, to refuse one that uses itself.

        Raises:
            RecipeError: The element cannot be drawn inside the policy, or the file is too deep.
        """
        if depth > MAX_SOURCE_DEPTH:
            raise self.refuse(f"is nested more than {MAX_SOURCE_DEPTH} deep")
        local = _local(source)
        if local is None or local in DROPPED:
            return
        if local in UNDRAWABLE:
            raise self.refuse(f"draws with <{local}>, which the console's policy cannot carry")
        if local in {"defs", "symbol", "linearGradient", "radialGradient", "clipPath"}:
            return  # defined here, drawn where something refers to it
        if local == "a":
            local = "g"
        if local == "use":
            self._use(source, parent, depth, trail)
            return
        if local == "switch":
            # Illustrator writes its own data in a foreign object beside what it draws: the
            # drawing is the first child that is not that.
            for child in source:
                if _local(child) not in {None, *DROPPED}:
                    self._emit(child, parent, depth + 1, trail)
                    break
            return
        if local == "svg":
            self._nested(source, parent, depth, trail)
            return
        if local != "g" and local not in SHAPES:
            raise self.refuse(f"has <{local}>, which is not something a logo is drawn with")
        written = ET.Element(self._q(local))
        outer = self.color
        try:
            if not self._attributes(source, local, written.attrib):
                return
            self._count()
            if local == "g":
                for child in source:
                    self._emit(child, written, depth + 1, trail)
            elif not self._complete(local, written):
                return
            parent.append(written)
        finally:
            self.color = outer

    def _use(
        self, source: ET.Element, parent: ET.Element, depth: int, trail: tuple[str, ...]
    ) -> None:
        """
        Write a ``<use>`` of a shape of the same file as the shape; drop any other.

        Args:
            source: The ``<use>``.
            parent: Where it goes.
            depth: How deep it is in the file.
            trail: The ids of the uses being expanded.

        Raises:
            RecipeError: It uses itself, or the file expands to too much.
        """
        reference = source.get("href") or source.get(f"{{{XLINK_NAMESPACE}}}href") or ""
        if not reference.startswith("#"):
            return
        target = self.by_id.get(reference[1:])
        if target is None:
            return
        if reference[1:] in trail:
            raise self.refuse(f"has a <use> that uses itself (#{reference[1:]})")
        self.uses += 1
        if self.uses > MAX_USES:
            raise self.refuse("is too large once its uses are written out")
        wrapper = ET.Element(self._q("g"))
        if not self._attributes(source, "use", wrapper.attrib):
            return
        offset = " ".join(
            _format(_length(source.get(axis)) or 0.0, self.digits) for axis in ("x", "y")
        )
        for key in ("x", "y", "width", "height"):
            wrapper.attrib.pop(key, None)
        if offset != "0 0":
            moved = f"translate({offset})"
            wrapper.set(
                "transform",
                f"{moved} {wrapper.get('transform')}" if wrapper.get("transform") else moved,
            )
        inner = [target] if _local(target) != "symbol" else list(target)
        for item in inner:
            self._emit(item, wrapper, depth + 1, (*trail, reference[1:]))
        if len(wrapper):
            self._count()
            parent.append(wrapper)

    def _nested(
        self, source: ET.Element, parent: ET.Element, depth: int, trail: tuple[str, ...]
    ) -> None:
        """
        Write an ``<svg>`` inside the file as the group that places its viewBox in its box.

        Args:
            source: The nested ``<svg>``.
            parent: Where it goes.
            depth: How deep it is in the file.
            trail: The ids of the uses being expanded.

        Raises:
            RecipeError: Its viewBox cannot be read.
        """
        wrapper = ET.Element(self._q("g"))
        if not self._attributes(source, "svg", wrapper.attrib):
            return
        for key in ("x", "y", "width", "height"):
            wrapper.attrib.pop(key, None)
        x = _length(source.get("x")) or 0.0
        y = _length(source.get("y")) or 0.0
        box = source.get("viewBox")
        transform = ""
        if box is None:
            if x or y:
                transform = f"translate({_format(x, self.digits)} {_format(y, self.digits)})"
        else:
            try:
                min_x, min_y, view_width, view_height = (
                    float(part) for part in re.split(r"[\s,]+", box.strip())
                )
            except ValueError as exc:
                raise self.refuse("has a nested <svg> whose viewBox cannot be read") from exc
            width = _length(source.get("width")) or view_width
            height = _length(source.get("height")) or view_height
            if view_width <= 0 or view_height <= 0:
                raise self.refuse("has a nested <svg> with a viewBox that has no size")
            transform = _viewport_transform(
                x,
                y,
                width,
                height,
                (min_x, min_y, view_width, view_height),
                source.get("preserveAspectRatio") or "",
                self.digits + 2,
            )
        if transform:
            wrapper.set(
                "transform",
                f"{transform} {wrapper.get('transform')}"
                if wrapper.get("transform")
                else transform,
            )
        for child in source:
            self._emit(child, wrapper, depth + 1, trail)
        if len(wrapper):
            self._count()
            parent.append(wrapper)

    def _complete(self, local: str, written: ET.Element) -> bool:
        """
        Args:
            local: The shape's name.
            written: The shape as written.

        Returns:
            Whether it has what draws it; a shape without it draws nothing and is left out.

        Raises:
            RecipeError: A path whose data cannot be read.
        """
        if local == "path":
            return "d" in written.attrib
        if local in {"polyline", "polygon"}:
            return "points" in written.attrib
        if local == "circle":
            return "r" in written.attrib
        if local == "ellipse":
            return "rx" in written.attrib and "ry" in written.attrib
        return True

    def _prune(self, element: ET.Element) -> None:
        """
        Take out the groups nothing is left in, and unwrap those that change nothing.

        Args:
            element: A written element.
        """
        for child in list(element):
            self._prune(child)
            if child.tag == self._q("g") and not len(child):
                element.remove(child)
        for index, child in reversed(list(enumerate(element))):
            if child.tag == self._q("g") and not child.attrib:
                element[index : index + 1] = list(child)

    def _draws(self, element: ET.Element) -> bool:
        return element.tag != self._q("g") or any(self._draws(child) for child in element)


# -- helpers ----------------------------------------------------------------------------------


def _local(element: ET.Element) -> str | None:
    """
    Args:
        element: An element of the file.

    Returns:
        Its name when it is in the SVG namespace, else None: an editor's elements are left out.
    """
    tag = element.tag
    if not isinstance(tag, str) or not tag.startswith(f"{{{SVG_NAMESPACE}}}"):
        return None
    return tag.rsplit("}", 1)[1]


def _length(value: str | None) -> float | None:
    """
    Args:
        value: A ``width``, ``height``, ``x`` or ``y``.

    Returns:
        The number, when it is in pixels or has no unit; None otherwise.
    """
    if value is None:
        return None
    matched = re.fullmatch(r"\s*([+-]?(?:\d+\.?\d*|\.\d+))(?:px)?\s*", value)
    return float(matched.group(1)) if matched else None


def _format(value: float, digits: int) -> str:
    """
    Args:
        value: A number.
        digits: How many decimals to keep.

    Returns:
        It in the shortest form: no trailing zeros, no leading zero, no negative zero.
    """
    rounded = round(value, digits)
    if rounded == 0:
        return "0"
    text = f"{rounded:.{digits}f}".rstrip("0").rstrip(".")
    return re.sub(r"^(-?)0\.", r"\1.", text)


def _round_numbers(value: str, digits: int | None = None, *, significant: int | None = None) -> str:
    """
    Args:
        value: A text with numbers in it (``points``, a ``transform``, a dash array).
        digits: How many decimals to keep.
        significant: Instead, how many significant digits: for a number that is a scale, whose
            first decimals can be zeros. What is smaller than a millionth is nothing.

    Returns:
        The text with each number rounded.
    """

    def one(match: re.Match[str]) -> str:
        number = float(match.group(0))
        if significant is None:
            return _format(number, digits or 0)
        if abs(number) < 1e-6:
            return "0"
        magnitude = max(0, significant - 1 - math.floor(math.log10(abs(number))))
        return _format(number, magnitude)

    return _NUMBER.sub(one, value)


def _viewport_transform(
    x: float,
    y: float,
    width: float,
    height: float,
    view: tuple[float, float, float, float],
    aspect: str,
    digits: int,
) -> str:
    """
    Args:
        x: Where the nested viewport starts.
        y: Where the nested viewport starts.
        width: The viewport's size.
        height: The viewport's size.
        view: Its viewBox: minimum x and y, then width and height.
        aspect: Its ``preserveAspectRatio``.
        digits: How many decimals to keep.

    Returns:
        The transform that puts the viewBox in the viewport the way the attribute says. Clipping
        to the viewport is not written: a logo's content sits inside it.
    """
    min_x, min_y, view_width, view_height = view
    words = aspect.split()
    align = words[0] if words else "xMidYMid"
    slice_it = "slice" in words
    scale_x, scale_y = width / view_width, height / view_height
    if align == "none":
        parts = [
            f"translate({_format(x - min_x * scale_x, digits)} {_format(y - min_y * scale_y, digits)})"
        ]
        scale = f"scale({_format(scale_x, digits)} {_format(scale_y, digits)})"
    else:
        uniform = max(scale_x, scale_y) if slice_it else min(scale_x, scale_y)
        fraction_x = {"xMin": 0.0, "xMid": 0.5, "xMax": 1.0}.get(align[:4], 0.5)
        fraction_y = {"YMin": 0.0, "YMid": 0.5, "YMax": 1.0}.get(align[4:8], 0.5)
        offset_x = x + fraction_x * (width - view_width * uniform) - min_x * uniform
        offset_y = y + fraction_y * (height - view_height * uniform) - min_y * uniform
        parts = [f"translate({_format(offset_x, digits)} {_format(offset_y, digits)})"]
        scale = f"scale({_format(uniform, digits)})"
    parts.append(scale)
    # A move by nothing and a scale of one are what the nested viewport already is.
    return " ".join(part for part in parts if part not in {"translate(0 0)", "scale(1)"})


_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def compact_path(data: str, digits: int) -> str:
    """
    Read path data and write it again with rounded numbers and no redundancy.

    Args:
        data: The ``d`` of a ``<path>``.
        digits: How many decimals to keep.

    Returns:
        The same path: a command is written once for a run of its own, a space only where two
        numbers could not be told apart, and the arc flags are kept apart from what follows.

    Raises:
        ValueError: The data is not path data.
    """
    groups: list[tuple[str, list[str]]] = []
    position, size = 0, len(data)
    command = ""
    while True:
        while position < size and data[position] in " ,\t\r\n":
            position += 1
        if position >= size:
            break
        character = data[position]
        if character.isalpha():
            if character.upper() not in _ARITY:
                raise ValueError(f"unknown path command {character!r}")
            command = character
            position += 1
            if command in "Zz":
                groups.append((command, []))
                command = ""
                continue
            continue
        if not command:
            raise ValueError("path data with numbers before a command")
        arguments: list[str] = []
        for index in range(_ARITY[command.upper()]):
            while position < size and data[position] in " ,\t\r\n":
                position += 1
            if command.upper() == "A" and index in (3, 4):
                if position >= size or data[position] not in "01":
                    raise ValueError("an arc flag that is not 0 or 1")
                arguments.append(data[position])
                position += 1
                continue
            matched = _NUMBER.match(data, position)
            if matched is None:
                raise ValueError(f"expected a number at {position}")
            number = float(matched.group(0))
            if number != number or abs(number) == float("inf"):
                raise ValueError("a number that is not finite")
            arguments.append(_format(number, digits))
            position = matched.end()
        groups.append((command, arguments))
        if command == "M":
            command = "L"
        elif command == "m":
            command = "l"
    if not groups or groups[0][0] not in "Mm":
        raise ValueError("path data must start with a moveto")
    return _write_path(groups)


def _write_path(groups: list[tuple[str, list[str]]]) -> str:
    """
    Args:
        groups: Each command with its arguments, as numbers already formatted.

    Returns:
        The path data: a command letter only where the command changes (a lineto after a moveto
        is implied), a space only where two numbers could not be told apart.
    """
    tokens: list[tuple[str, str]] = []
    previous = ""
    for command, arguments in groups:
        if command in "Zz" or not (
            command == previous or (previous, command) in {("M", "L"), ("m", "l")}
        ):
            tokens.append(("command", command))
        for index, number in enumerate(arguments):
            flag = command.upper() == "A" and index in (3, 4)
            tokens.append(("flag" if flag else "number", number))
        previous = command
    out: list[str] = []
    for position, (kind, text) in enumerate(tokens):
        if position and kind != "command" and tokens[position - 1][0] != "command":
            before_kind, before = tokens[position - 1]
            if text.startswith("-"):
                pass
            elif text.startswith(".") and before_kind == "number" and "." in before:
                pass
            else:
                out.append(" ")
        out.append(text)
    return "".join(out)


def _without_at_rules(css: str) -> str:
    """
    Args:
        css: A stylesheet without comments.

    Returns:
        It without ``@import`` and the blocks of ``@media``, ``@font-face`` and the like: only
        the plain rules of the document apply to a logo, which is shown without a context.
    """
    out: list[str] = []
    position = 0
    while position < len(css):
        if css[position] != "@":
            out.append(css[position])
            position += 1
            continue
        end_statement = css.find(";", position)
        end_block = css.find("{", position)
        if end_block == -1 or (end_statement != -1 and end_statement < end_block):
            position = len(css) if end_statement == -1 else end_statement + 1
            continue
        depth = 0
        while position < len(css):
            if css[position] == "{":
                depth += 1
            elif css[position] == "}":
                depth -= 1
                if depth == 0:
                    position += 1
                    break
            position += 1
    return "".join(out)


def _declarations(text: str) -> dict[str, str]:
    """
    Args:
        text: The inside of a rule or a ``style`` attribute.

    Returns:
        What it sets among the presentation properties, plus ``display`` and ``visibility``
        (which decide whether the element is drawn at all) and ``color``.
    """
    found: dict[str, str] = {}
    for part in text.split(";"):
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip().lower()
        if key in PRESENTATION or key in HIDING:
            found[key] = value.strip()
    return found
