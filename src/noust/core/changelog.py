# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What changed in each release, readable inside the product (checklist N-24).

The console used to send an operator to GitHub to find out what an update would do. Two sources are
now read in place:

- **The releases this installation has.** ``changelog.json`` ships in the package, generated from
  the Debian changelog by ``scripts/release.py`` (the one place a release's summary is written, and
  the source of the GitHub release's text too). :func:`packaged` reads it: "what's new in what I run".
- **A release it does not have yet.** The GitHub release of a newer version carries the same lines;
  :func:`parse_release_body` reads them, so the console can show "what this update changes" before
  the operator presses anything.

An entry's lines are plain sentences. One that needs the operator to do something starts with
``Action required:``; the console sets those apart, and the upgrade guide of the release says how.
Nothing here is HTML: the text is shown as text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from typing import Any

#: A line that needs something done by the operator starts with this.
ACTION_MARKER = "Action required:"

#: How many entries the packaged file keeps: a few years of releases, a few tens of kilobytes.
KEEP = 40

#: ``noust (3.3.1-1) unstable; urgency=medium``: the package name and the upstream version.
_ENTRY = re.compile(r"^(\S+) \((?:\d+:)?(\d+(?:\.\d+)+)(?:[~+-][^)]*)?\) [^;]+;")

#: `` -- Name <email>  Mon, 05 Oct 2026 13:08:48 +0000``.
_SIGNATURE = re.compile(r"^ -- .+?>  (?P<date>.+)$")


@dataclass(frozen=True)
class Item:
    """
    One line of a release's notes.

    Attributes:
        text: What changed, as a sentence.
        action_required: Whether the operator has to do something when updating.
    """

    text: str
    action_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Returns: The item as plain data."""
        return {"text": self.text, "action_required": self.action_required}


@dataclass(frozen=True)
class Entry:
    """
    The notes of one release.

    Attributes:
        version: ``3.3.1``.
        date: ``2026-10-05``, or an empty string when the changelog gave none.
        items: What changed, in the order the release wrote it.
    """

    version: str
    date: str = ""
    items: tuple[Item, ...] = field(default_factory=tuple)

    @property
    def action_required(self) -> bool:
        """True when any line of the release needs the operator to act."""
        return any(item.action_required for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        """Returns: The entry as plain data."""
        return {
            "version": self.version,
            "date": self.date,
            "action_required": self.action_required,
            "items": [item.to_dict() for item in self.items],
        }


def version_key(version: str) -> tuple[int, ...]:
    """
    Order a release string.

    Args:
        version: ``3.3.1``.

    Returns:
        Its numeric parts; an unreadable one sorts first.
    """
    parts = version.split(".")
    return tuple(int(p) for p in parts) if all(p.isdigit() for p in parts) else (0,)


def _item(text: str) -> Item:
    """
    Make an item from a bullet's text, reading the action marker.

    Args:
        text: The bullet, possibly continued over several lines.

    Returns:
        The item; the marker is removed from the text and kept as a flag.
    """
    text = " ".join(text.split())
    if text.lower().startswith(ACTION_MARKER.lower()):
        return Item(text[len(ACTION_MARKER) :].strip(), True)
    return Item(text, False)


def _bullets(lines: list[str]) -> tuple[Item, ...]:
    """
    Group lines into bullets: a line that starts with ``*`` or ``-`` begins one, an indented line continues it.

    Args:
        lines: The entry's body lines.

    Returns:
        The items.
    """
    items: list[Item] = []
    current: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped[0] in "*-" and stripped[1:2] == " ":
            if current:
                items.append(_item(" ".join(current)))
            current = [stripped[2:]]
        elif current:
            current.append(stripped)
        else:
            current = [stripped]
    if current:
        items.append(_item(" ".join(current)))
    return tuple(items)


def parse_debian(text: str) -> list[Entry]:
    """
    Read a Debian changelog.

    Args:
        text: The changelog, newest entry first.

    Returns:
        One entry per release, in the file's order; the same version under two package names
        is kept once, from the first.
    """
    entries: list[Entry] = []
    seen: set[str] = set()
    version: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        head = _ENTRY.match(line)
        if head is not None:
            version, body = head.group(2), []
            continue
        if version is None:
            continue
        signature = _SIGNATURE.fullmatch(line)
        if signature is not None:
            if version not in seen:
                seen.add(version)
                entries.append(Entry(version, _iso_date(signature.group("date")), _bullets(body)))
            version = None
            continue
        body.append(line)
    return entries


def _iso_date(stamp: str) -> str:
    """
    Turn a changelog's RFC 2822 date into a day.

    Args:
        stamp: ``Mon, 05 Oct 2026 13:08:48 +0000``.

    Returns:
        ``2026-10-05``, or an empty string when it cannot be read.
    """
    try:
        return datetime.strptime(stamp.strip(), "%a, %d %b %Y %H:%M:%S %z").date().isoformat()
    except ValueError:
        return ""


def parse_release_body(body: str, version: str) -> Entry:
    """
    Read the notes of a GitHub release.

    The release workflow writes ``## Changes in vX.Y.Z`` and then the Debian changelog's lines
    for that version, followed by the installation instructions under other headings.

    Args:
        body: The release's description.
        version: The version asked for.

    Returns:
        The entry; with no items when the description has no such section.
    """
    lines: list[str] = []
    inside = False
    for line in body.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip().lower().startswith("changes in")
            continue
        if inside:
            lines.append(line)
    return Entry(version, "", _bullets(lines))


def render(entries: list[Entry]) -> str:
    """
    Write the packaged file: the newest entries, as JSON, stable byte for byte.

    Args:
        entries: Entries, newest first.

    Returns:
        The file's text.
    """
    document = {"entries": [entry.to_dict() for entry in entries[:KEEP]]}
    return json.dumps(document, indent=1, ensure_ascii=False) + "\n"


def render_from_debian(text: str) -> str:
    """
    Generate the packaged file from the Debian changelog.

    Args:
        text: ``obs/debian.changelog``.

    Returns:
        The file's text.
    """
    return render(parse_debian(text))


def packaged() -> list[Entry]:
    """
    Read the notes that ship with this installation.

    Returns:
        The entries, newest first; empty when the file is missing (a source checkout that never
        ran the release script) or unreadable, which is never a reason to fail a request.
    """
    try:
        text = resources.files("noust").joinpath("changelog.json").read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError, ModuleNotFoundError):
        return []
    entries: list[Entry] = []
    for raw in data.get("entries", []):
        items = tuple(
            Item(str(i.get("text", "")), bool(i.get("action_required")))
            for i in raw.get("items", [])
            if i.get("text")
        )
        entries.append(Entry(str(raw.get("version", "")), str(raw.get("date", "")), items))
    return entries


def since(entries: list[Entry], version: str) -> list[Entry]:
    """
    Pick the entries newer than a version.

    Args:
        entries: Entries, any order.
        version: The version the operator runs.

    Returns:
        The entries strictly newer than it, newest first.
    """
    floor = version_key(version)
    return sorted(
        (e for e in entries if version_key(e.version) > floor),
        key=lambda e: version_key(e.version),
        reverse=True,
    )
