# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a person may keep as a panel of the console's Home.

A panel is the console's own composition (which widgets, where on a 12-column grid, at what size,
with what settings), but the server stores it per account and a person, or a script holding that
person's token, writes it. So it is judged at this one place before it is stored, like
``noust.yaml``: the document is version 1 and closed, every key is known, every number is an
integer inside the grid, and a setting is a scalar, never a nested document. Anything else is a
:class:`~noust.core.exceptions.ValidationError` naming the field (``layout.widgets[2].w``), never
kept and never silently ignored.

:func:`check_layout` does not edit what it is given: it reads each field into a new document built
from the known keys alone, which is what is stored, so the stored text has nothing in it that was
not judged. The limits are the plan's: 40 widgets, 32 KB for the document, 2 KB for one widget's
settings; and 20 panels for an account, which the store holds to
(:mod:`noust.core.account_dashboards`).

Widgets are named by a ``type`` the console defines; whether the person's role may see one is the
console's decision when it draws it, so the server accepts any well-formed type and an unknown one
is dropped when the panel is read back.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, TypeGuard

from noust.core.exceptions import ValidationError

__all__ = [
    "GRID_COLUMNS",
    "LAYOUT_VERSION",
    "MAX_DASHBOARDS",
    "MAX_DOCUMENT_BYTES",
    "MAX_SETTINGS_BYTES",
    "MAX_WIDGETS",
    "check_dashboard_id",
    "check_dashboard_name",
    "check_dashboard_scope",
    "check_layout",
    "layout_text",
]

#: The one version of the document there is.
LAYOUT_VERSION = 1

#: Columns of the grid a widget is placed on.
GRID_COLUMNS = 12

#: The most widgets a panel holds, panels an account keeps, and bytes a document and one widget's
#: settings take (the document as it is stored: compact JSON, in UTF-8).
MAX_WIDGETS = 40
MAX_DASHBOARDS = 20
MAX_DOCUMENT_BYTES = 32 * 1024
MAX_SETTINGS_BYTES = 2 * 1024

#: The tallest a widget is, in rows, and the lowest row it starts at: 40 widgets of the tallest
#: size one under another still fit, and nothing is placed further down than a person could scroll.
_MAX_HEIGHT = 24
_MAX_ROW = 999

#: Integers past this do not survive a round trip through a browser's numbers.
_MAX_SAFE_INTEGER = 2**53 - 1

#: The longest text a setting holds, and the longest name of a panel.
_MAX_TEXT = 200
_MAX_NAME = 60

#: What a widget's type, a widget's id and a panel's id are, judged with ``fullmatch``.
_TYPE = re.compile(r"[a-z][a-z0-9-]{0,40}")
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")

#: A setting's name is an identifier that does not start with an underscore, so no name a
#: JavaScript object treats specially (``__proto__``) can be one.
_SETTING_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")

#: Control characters, line and paragraph separators and the characters that reorder text.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")

#: ``POST /api/auth/dashboards/order`` is the route that orders them, so no panel is called that.
_RESERVED_IDS = frozenset({"order"})

_SCOPES = ("server", "fleet")

_LAYOUT_KEYS = ("version", "widgets")
_WIDGET_KEYS = ("id", "type", "x", "y", "w", "h", "settings")


def _refuse(field: str, message: str, details: str = "") -> ValidationError:
    return ValidationError(message, details=details, field=field)


def _is_int(value: object) -> TypeGuard[int]:
    """A JSON integer: ``bool`` is an ``int`` in Python and is not one here."""
    return type(value) is int


def check_dashboard_id(value: object) -> str:
    """
    Judge the id a person gave a panel.

    Args:
        value: What was given.

    Returns:
        The id: lower case letters, digits and hyphens, 1 to 40 characters, not starting with a
        hyphen.

    Raises:
        ValidationError: It is not one, or it is ``order``, which names the route that orders
            the panels.
    """
    if not isinstance(value, str) or not _ID.fullmatch(value) or value in _RESERVED_IDS:
        raise _refuse(
            "dashboard",
            "That is not an id Noust can keep a panel under",
            "Use 1 to 40 lower case letters, digits and hyphens, not starting with a hyphen "
            "(and not 'order', which is the name of a route).",
        )
    return value


def check_dashboard_name(value: object) -> str:
    """
    Judge the name a person gave a panel.

    Args:
        value: What was given.

    Returns:
        The name without the spaces around it.

    Raises:
        ValidationError: It is not text, is empty, is longer than 60 characters or holds a control
            character.
    """
    if not isinstance(value, str):
        raise _refuse("name", "The name of a panel must be text")
    name = value.strip()
    if not name or len(name) > _MAX_NAME:
        raise _refuse("name", f"The name of a panel is 1 to {_MAX_NAME} characters")
    if _CONTROL.search(name):
        raise _refuse("name", "The name of a panel cannot hold a line break or a control character")
    return name


def check_dashboard_scope(value: object) -> str:
    """
    Judge what a panel follows.

    Args:
        value: What was given.

    Returns:
        ``server`` (the server chosen in the selector) or ``fleet`` (every server).

    Raises:
        ValidationError: It is neither.
    """
    if not isinstance(value, str) or value not in _SCOPES:
        raise _refuse(
            "scope", "A panel follows the server or the fleet", "Use 'server' or 'fleet'."
        )
    return value


def _setting_text(value: str, field: str) -> str:
    if _CONTROL.search(value):
        raise _refuse(field, "A text setting cannot hold a line break or a control character")
    if len(value) > _MAX_TEXT:
        raise _refuse(field, f"A text setting is at most {_MAX_TEXT} characters")
    return value


def _setting(value: object, field: str) -> str | int | float | bool:
    """
    Args:
        value: What a widget keeps under a setting's name.
        field: Its path, for the error.

    Returns:
        The value, when it is a scalar.

    Raises:
        ValidationError: It is a list, an object, null or a number JSON cannot carry.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _setting_text(value, field)
    if _is_int(value):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise _refuse(field, "A number setting is smaller than 2^53")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _refuse(field, "A number setting is finite")
        return value
    raise _refuse(
        field, "A setting is a text, a number or true or false", "Leave a key out to say nothing."
    )


def _settings(value: object, field: str) -> dict[str, str | int | float | bool]:
    """
    Args:
        value: A widget's settings.
        field: Its path, for the error.

    Returns:
        A copy: scalars under identifiers, at most 2 KB as compact JSON.

    Raises:
        ValidationError: Anything else.
    """
    if not isinstance(value, dict):
        raise _refuse(field, "The settings of a widget are an object")
    checked: dict[str, str | int | float | bool] = {}
    size = 2
    for name, item in value.items():
        if not isinstance(name, str) or not _SETTING_NAME.fullmatch(name):
            raise _refuse(
                field,
                "A setting is named by a letter followed by letters, digits and underscores",
                "At most 40 characters; a name starting with an underscore is not allowed.",
            )
        checked[name] = _setting(item, f"{field}.{name}")
        # Counted as it goes: an object of a million keys is refused at the 342nd, not measured.
        size += (
            len(json.dumps(name)) + 1 + len(_json(item).encode()) + (1 if len(checked) > 1 else 0)
        )
        if size > MAX_SETTINGS_BYTES:
            raise _refuse(
                field, f"The settings of a widget are at most {MAX_SETTINGS_BYTES // 1024} KB"
            )
    return checked


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _within(value: object, low: int, high: int, field: str, what: str) -> int:
    if not _is_int(value) or not low <= value <= high:
        raise _refuse(field, f"{what} is a whole number from {low} to {high}")
    return value


def _widget(value: object, place: str, seen: set[str]) -> dict[str, Any]:
    """
    Args:
        value: One entry of ``widgets``.
        place: Its path, ``layout.widgets[3]``.
        seen: The ids of the widgets before it.

    Returns:
        The widget, rebuilt from the keys it may have.

    Raises:
        ValidationError: It is not a well-formed widget on the grid.
    """
    if not isinstance(value, dict):
        raise _refuse(place, "A widget is an object")
    for key in sorted(map(str, value)):
        if key not in _WIDGET_KEYS:
            raise _refuse(
                f"{place}.{key}", f"A widget has no {key!r}", f"It has {', '.join(_WIDGET_KEYS)}."
            )
    for key in _WIDGET_KEYS:
        if key not in value:
            raise _refuse(f"{place}.{key}", f"A widget needs its {key!r}")
    widget_id = value["id"]
    if not isinstance(widget_id, str) or not _ID.fullmatch(widget_id):
        raise _refuse(
            f"{place}.id", "The id of a widget is 1 to 40 lower case letters, digits and hyphens"
        )
    if widget_id in seen:
        raise _refuse(f"{place}.id", f"Two widgets are called {widget_id!r}")
    kind = value["type"]
    if not isinstance(kind, str) or not _TYPE.fullmatch(kind):
        raise _refuse(
            f"{place}.type",
            "The type of a widget is a lower case letter followed by up to 40 letters, digits and hyphens",
        )
    x = _within(value["x"], 0, GRID_COLUMNS - 1, f"{place}.x", "The column of a widget")
    y = _within(value["y"], 0, _MAX_ROW, f"{place}.y", "The row of a widget")
    w = _within(value["w"], 1, GRID_COLUMNS, f"{place}.w", "The width of a widget")
    h = _within(value["h"], 1, _MAX_HEIGHT, f"{place}.h", "The height of a widget")
    if x + w > GRID_COLUMNS:
        raise _refuse(
            f"{place}.w",
            f"A widget that starts at column {x} and is {w} wide ends past the {GRID_COLUMNS} columns",
        )
    seen.add(widget_id)
    return {
        "id": widget_id,
        "type": kind,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "settings": _settings(value["settings"], f"{place}.settings"),
    }


def layout_text(layout: dict[str, Any]) -> str:
    """
    The text a checked document is stored as.

    Args:
        layout: What :func:`check_layout` returned.

    Returns:
        Compact JSON with sorted keys, so the same document is the same text.
    """
    return json.dumps(layout, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def check_layout(value: object) -> dict[str, Any]:
    """
    Judge the document of a panel.

    Args:
        value: The parsed JSON a client sent: ``{"version": 1, "widgets": [...]}``.

    Returns:
        A new document made of the known keys alone, with every number an integer on the grid.

    Raises:
        ValidationError: Naming the field (``layout.widgets[2].w``) when the document is not
            version 1, has a key it should not, has more than 40 widgets, places a widget off the
            12 columns, repeats an id, keeps a setting that is not a scalar, or is past 32 KB.
    """
    if not isinstance(value, dict):
        raise _refuse("layout", "A panel's layout is an object")
    for key in sorted(map(str, value)):
        if key not in _LAYOUT_KEYS:
            raise _refuse(
                f"layout.{key}", f"A layout has no {key!r}", "It has version and widgets."
            )
    if "version" not in value:
        raise _refuse("layout.version", "A layout needs its version")
    if not _is_int(value["version"]) or value["version"] != LAYOUT_VERSION:
        raise _refuse(
            "layout.version", f"Only version {LAYOUT_VERSION} of the layout is understood"
        )
    if "widgets" not in value:
        raise _refuse("layout.widgets", "A layout needs its widgets")
    widgets = value["widgets"]
    if not isinstance(widgets, list):
        raise _refuse("layout.widgets", "The widgets of a layout are a list")
    if len(widgets) > MAX_WIDGETS:
        raise _refuse(
            "layout.widgets",
            f"A panel holds at most {MAX_WIDGETS} widgets",
            "Remove some, or put them in another panel.",
        )
    seen: set[str] = set()
    checked = {
        "version": LAYOUT_VERSION,
        "widgets": [_widget(item, f"layout.widgets[{i}]", seen) for i, item in enumerate(widgets)],
    }
    if len(layout_text(checked).encode()) > MAX_DOCUMENT_BYTES:
        raise _refuse(
            "layout",
            f"A panel's layout is at most {MAX_DOCUMENT_BYTES // 1024} KB",
            "Remove widgets or shorten their settings.",
        )
    return checked
