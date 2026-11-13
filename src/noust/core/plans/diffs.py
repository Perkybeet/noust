# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A file's change, as a plan may show it.

A plan is read with the permission to read the application, which is less than the permission to
read its secrets, and by a model that is never given one. So a change is shown only as far as it
cannot give one away:

- a file of variables (a ``.env``, an environment file) shows the **names** that appear, change or
  go, never a value, and a variable whose value changes is a removed name and an added one;
- any other file that only its owner can read (the mode a credential is written with) shows
  nothing of its content;
- the rest is a diff, and every line of it goes through the caller's scrubber (the values the
  application is known to hold, and every shape of a credential) and through one rule more: the
  value of an assignment whose name says it is a secret is hidden whatever the file is.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from noust.core.config import REDACTED
from noust.core.plans.shape import PlanDiffLine, Reason, Shown
from noust.core.secret_detection import name_looks_secret

#: The lines of a change that a plan shows; the rest is said to be cut.
MAX_DIFF_LINES = 200

#: A file longer than this is not diffed at all: reading a dump to compare it is not a plan.
MAX_TEXT_BYTES = 256 * 1024

#: Lines of context around a change, as ``diff -U2``.
CONTEXT_LINES = 2

#: A line of a file of variables: ``NAME=value``, with ``export`` allowed.
_VARIABLE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)")

#: A ``name: value`` or ``name=value`` line of a configuration or a compose file, with a list dash
#: and quotes allowed around the name.
_ASSIGNMENT = re.compile(
    r"(?P<head>\s*(?:-\s+)?(?:export\s+)?[\"']?(?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)[\"']?\s*[:=]\s*)"
    r"(?P<value>\S.*)"
)


#: The first character of a line of a unified diff, as the kind of line it is.
_KINDS: dict[str, Literal["add", "del"]] = {"+": "add", "-": "del"}


@dataclass(frozen=True)
class FileView:
    """
    What a plan shows of one file's change.

    Attributes:
        shown: ``diff``, ``names`` or ``none``.
        lines: The lines, when there are any.
        truncated: There were more lines than are shown.
        reason: Why nothing is shown, when nothing is.
    """

    shown: Shown
    lines: tuple[PlanDiffLine, ...] = ()
    truncated: bool = False
    reason: Reason | None = None


def is_variables_file(path: Path) -> bool:
    """
    Args:
        path: A file's path.

    Returns:
        Whether its name is that of a file of environment variables (``.env``, ``.env.production``,
        ``shop.env``).
    """
    name = path.name
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def _is_private(mode: int | None) -> bool:
    """
    Args:
        mode: The permissions a file is written with, when they are known.

    Returns:
        Whether only its owner can read it: how Noust writes a credential.
    """
    return mode is not None and mode & 0o077 == 0


def _text_of(content: str | bytes | None) -> str | None:
    """
    Args:
        content: A file's content: text, bytes or nothing.

    Returns:
        Its text, or None when it is nothing; raises ``ValueError`` for what is not text.

    Raises:
        ValueError: The bytes are not UTF-8 or hold a NUL: it is not a text file.
    """
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if b"\x00" in content:
        raise ValueError("binary")
    return content.decode("utf-8")


def _variables(text: str | None) -> dict[str, str] | None:
    """
    Args:
        text: A file's text.

    Returns:
        Name to value when every line that is not a comment or blank is a variable, in the order
        they appear; None when the text is not a file of variables.
    """
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _VARIABLE.fullmatch(stripped)
        if match is None:
            return None
        found[match.group(1)] = match.group(2).strip()
    return found


def _names_change(before: str | None, after: str | None) -> tuple[PlanDiffLine, ...]:
    """
    Args:
        before: The variables a file holds now, as text.
        after: The variables it would hold.

    Returns:
        The names that are added, removed or whose value changes. A value is compared and never
        shown: a variable that changes is its name removed and added.
    """
    old = _variables(before) or {}
    new = _variables(after) or {}
    lines: list[PlanDiffLine] = []
    for name, value in new.items():
        if name not in old:
            lines.append(PlanDiffLine("add", name))
        elif old[name] != value:
            lines.append(PlanDiffLine("del", name))
            lines.append(PlanDiffLine("add", name))
    lines.extend(PlanDiffLine("del", name) for name in old if name not in new)
    return tuple(lines)


def mask_line(line: str, scrub: Callable[[str], str]) -> str:
    """
    Hide what a line of a file would give away.

    Args:
        line: One line of a text file.
        scrub: The caller's scrubber: the values the application is known to hold and every shape
            of a credential.

    Returns:
        The line with the value of a secret-named assignment hidden (unless it only refers to
        another variable) and then scrubbed.
    """
    match = _ASSIGNMENT.fullmatch(line)
    if match is not None and name_looks_secret(match.group("name")):
        value = match.group("value").strip()
        if not value.startswith(("${", "$(", "<")):
            line = f"{match.group('head')}{REDACTED}"
    return scrub(line)


def _diff(before: str | None, after: str | None, scrub: Callable[[str], str]) -> FileView:
    """
    Args:
        before: A file's text now, None for a file that does not exist.
        after: Its text after, None for a file that is deleted.
        scrub: The caller's scrubber.

    Returns:
        The diff, with two lines of context and at most :data:`MAX_DIFF_LINES` lines.
    """
    old = (before or "").splitlines()
    new = (after or "").splitlines()
    produced: list[PlanDiffLine] = []
    first = True
    for line in difflib.unified_diff(old, new, n=CONTEXT_LINES, lineterm=""):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            # Hunks are told apart by a context line that says so, so that two changes far apart
            # are not read as neighbours.
            if not first:
                produced.append(PlanDiffLine("same", "..."))
            first = False
            continue
        produced.append(PlanDiffLine(_KINDS.get(line[:1], "same"), mask_line(line[1:], scrub)))
    truncated = len(produced) > MAX_DIFF_LINES
    return FileView("diff", tuple(produced[:MAX_DIFF_LINES]), truncated)


def view_of_change(
    path: Path,
    *,
    before: str | bytes | None,
    after: str | bytes | None,
    scrub: Callable[[str], str],
    mode: int | None = None,
) -> FileView:
    """
    Decide what a plan shows of a file it would write or delete.

    Args:
        path: The file.
        before: What it holds now: None when it does not exist.
        after: What it would hold: None when it would be deleted.
        scrub: The caller's scrubber, applied to every line that is shown.
        mode: The permissions it would be written with, when the change is a write.

    Returns:
        What may be shown.
    """
    sizes = [len(item) for item in (before, after) if item is not None]
    if sizes and max(sizes) > MAX_TEXT_BYTES:
        return FileView("none", reason="large")
    try:
        old, new = _text_of(before), _text_of(after)
    except (ValueError, UnicodeDecodeError):
        return FileView("none", reason="binary")

    if is_variables_file(path) or _is_private(mode):
        # Only its names, and only when every line is a variable: a private file of any other
        # shape (a token, a key, a dump) is not read at all.
        if _variables(old) is not None and _variables(new) is not None:
            return FileView("names", _names_change(old, new))
        return FileView("none", reason="secret")
    if old == new:
        return FileView("diff")
    return _diff(old, new, scrub)
