# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Removing settings from ``config.yaml`` as text.

A reload-and-dump would drop every comment the operator wrote (and the order
and formatting they chose), which is why ``Config.upgrade`` appends to the file
instead of rewriting it. Cleaning out settings that no version reads any more
is the same kind of edit and gets the same treatment: the lines of the setting
go, everything else stays byte for byte, and the standalone comments stay even
where they were written about a setting that is now gone.

The scanner understands block-style mappings, which is what Noust and every
editor writes. It does not try to understand the rest of YAML: the caller
parses the result and compares it with what removing the same keys from the
parsed file gives, and refuses to write anything when the two differ. A file
this cannot edit safely (a flow-style mapping holding a setting to remove) is
therefore reported, never damaged.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

#: ``key:`` or ``"key":`` at the start of a line, indentation excluded. Keys
#: with a colon in them are not settings Noust has.
_KEY_LINE = re.compile(
    r"""^(?P<indent>[ ]*)(?P<quote>["']?)(?P<key>[A-Za-z0-9_.\-]+)(?P=quote)[ ]*:(?:[ ]|$)"""
)


class CannotEditText(Exception):
    """The file's layout is one this module does not edit; edit it by hand."""


@dataclass
class _Entry:
    """
    One mapping key found in the text.

    Attributes:
        path: Dotted path from the top of the file.
        indent: Column its key starts at.
        line: Index of the line holding the key.
        parent: The entry it is nested under, None at the top level.
        children: Entries nested directly under it.
        extent: Indexes of every non-comment line that belongs to it: its own
            line and the lines of its value.
        own: The lines of its value that are not the key line of a child (a
            list item, a continuation of a scalar).
    """

    path: str
    indent: int
    line: int
    parent: _Entry | None = None
    children: list[_Entry] = field(default_factory=list)
    extent: list[int] = field(default_factory=list)
    own: list[int] = field(default_factory=list)


def _is_skippable(line: str) -> bool:
    """
    Tell blank lines and comment lines, which never belong to a setting.

    Args:
        line: One line of the file.

    Returns:
        True for a blank or comment-only line.
    """
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _indent_of(line: str) -> int:
    """
    Measure a line's indentation.

    Args:
        line: One line of the file.

    Returns:
        How many spaces precede its content.
    """
    return len(line) - len(line.lstrip(" "))


def _scan(lines: list[str]) -> list[_Entry]:
    """
    Find the mapping keys of a file and the lines each one owns.

    Args:
        lines: The file, split into lines without their line endings.

    Returns:
        Every key found, in file order.

    Raises:
        CannotEditText: When a tab indents a line, which YAML forbids and
            which this scanner would misread.
    """
    entries: list[_Entry] = []
    stack: list[_Entry] = []
    for number, line in enumerate(lines):
        if _is_skippable(line):
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise CannotEditText("a line is indented with a tab")
        indent = _indent_of(line)
        # An indentless sequence ("key:" then "- item" at the key's own
        # column) belongs to the key above it.
        is_item = line.lstrip().startswith("- ") or line.strip() == "-"
        while stack and (stack[-1].indent > indent or (stack[-1].indent == indent and not is_item)):
            stack.pop()
        for open_entry in stack:
            open_entry.extent.append(number)
        if is_item or (stack and indent > stack[-1].indent and not _KEY_LINE.match(line)):
            if stack:
                stack[-1].own.append(number)
            continue

        match = _KEY_LINE.match(line)
        if match is None:
            if stack:
                stack[-1].own.append(number)
            continue

        parent = stack[-1] if stack and stack[-1].indent < indent else None
        if parent is not None and parent.own:
            # A key inside a list item or a block scalar: not a setting.
            parent.own.append(number)
            continue
        key = match["key"]
        path = f"{parent.path}.{key}" if parent is not None else key
        entry = _Entry(path=path, indent=indent, line=number, parent=parent)
        entry.extent.append(number)
        if parent is not None:
            parent.children.append(entry)
            # The child's line is not a line of the parent's own value.
        entries.append(entry)
        stack.append(entry)
    return entries


def _flow_value_spans_lines(line: str) -> bool:
    """
    Tell whether a key's value opens a flow collection it does not close.

    Args:
        line: The key's line.

    Returns:
        True when the value starts with ``{`` or ``[`` and the brackets do
        not balance on this line, so the value continues below.
    """
    value = line.split(":", 1)[1].strip()
    if value.startswith("#"):
        return False
    if not value or value[0] not in "{[":
        return False
    depth = 0
    for char in value:
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
    return depth != 0


def remove_keys(text: str, dotted_keys: Iterable[str]) -> str:
    """
    Remove settings from YAML text, leaving everything else as it was.

    A section is removed with everything under it. A parent left with no
    settings at all by the removal is removed too: ``monitor:`` with nothing
    below it would load as ``None`` and replace the section's defaults.
    Standalone comments and blank lines are never removed; a comment written
    at the end of a removed line goes with it.

    Args:
        text: The file's content.
        dotted_keys: Dotted paths to remove, such as ``monitor.openai``. A
            path the file does not have is ignored.

    Returns:
        The new content, with the file's own line endings kept.

    Raises:
        CannotEditText: When a setting to remove is written in a way this
            does not edit (a flow collection spread over several lines).
    """
    targets = set(dotted_keys)
    if not targets:
        return text
    lines = text.splitlines(keepends=True)
    bare = [line.rstrip("\r\n") for line in lines]
    entries = _scan(bare)

    doomed: set[int] = set()
    for entry in entries:
        if entry.path not in targets:
            continue
        if _flow_value_spans_lines(bare[entry.line]):
            raise CannotEditText(f"{entry.path} is written as a multi-line flow collection")
        doomed.update(entry.extent)

    # A parent whose every child went, and which holds nothing of its own,
    # goes too - all the way up, but never past the top of the file.
    changed = True
    while changed:
        changed = False
        for entry in entries:
            if entry.line in doomed or not entry.children or entry.own:
                continue
            if all(child.line in doomed for child in entry.children):
                doomed.update(entry.extent)
                changed = True

    return "".join(line for number, line in enumerate(lines) if number not in doomed)
