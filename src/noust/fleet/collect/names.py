# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a server says of its copies, judged before anything of it is used (item 195).

A server is not trusted with the central's disk. Its list names copies, files, sizes and digests,
and a compromised server (or one that is only wrong) can say anything: a name that climbs out of
the folder, a size that fills the disk, a digest that is not one, a thousand copies of nothing. Every
value that reaches a path or the store passes through here first, by an allowlist, and what does not
pass is not taken and is said, with why, so the operator sees a server that lies and not a pass that
quietly did less.

Nothing here reads the disk or the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.managers.backup_catalogue import KINDS, ORIGINS

__all__ = [
    "MAX_FILES",
    "MAX_ITEMS",
    "MAX_PLAUSIBLE_BYTES",
    "Rejected",
    "RemoteCatalogue",
    "RemoteFile",
    "RemoteItem",
    "parse_catalogue",
    "safe_segment",
]

#: Most copies a server may list. A server with more has a retention problem, not a list problem.
MAX_ITEMS = 100_000

#: Most files a copy may have: the copy, and what says what it is.
MAX_FILES = 4

#: The largest file a copy may claim to be. Past it a server is lying or has a disk nobody has:
#: it is not taken, and it does not stop the others being taken.
MAX_PLAUSIBLE_BYTES = 4 * 1024**4

#: A name that goes into a path of the central's: no separator, no control character, no leading
#: dot (so never ``.`` or ``..`` and never a hidden file), and the characters a copy's name
#: actually has.
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._@+=,~$-]{0,254}")

#: An identifier of a copy on its server: printable, one line.
_ITEM_ID = re.compile(r"[^\x00-\x1f\x7f]{1,300}")

#: What a person reads of what a copy is of.
_OWNER = re.compile(r"[^\x00-\x1f\x7f]{1,200}")

_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class RemoteFile:
    """
    One file of a copy, as a server listed it and the central accepted it.

    Attributes:
        name: A plain file name.
        size: Bytes.
        sha256: The digest the server recorded for it, or None.
        role: ``archive`` or ``sidecar``.
    """

    name: str
    size: int
    sha256: str | None
    role: str


@dataclass(frozen=True)
class RemoteItem:
    """
    One copy, as a server listed it and the central accepted it.

    Attributes:
        kind: One of :data:`~noust.managers.backup_catalogue.KINDS`.
        id: Its identifier on the server.
        owner: What it is a copy of.
        folder: One plain name, for a folder of the central's.
        created_at: When it was taken, UTC, ISO 8601.
        origin: ``scheduled``, ``manual`` or ``safety``.
        sealed: Whether its file is encrypted where it lies.
        files: Its files; the copy is the first.
        meta: Small facts a restore needs.
        blocked: Why the server will not let it go, or None.
    """

    kind: str
    id: str
    owner: str
    folder: str
    created_at: str
    origin: str
    sealed: bool
    files: tuple[RemoteFile, ...]
    meta: dict[str, str] = field(default_factory=dict)
    blocked: str | None = None

    @property
    def size(self) -> int:
        """Returns: What all its files weigh."""
        return sum(item.size for item in self.files)

    @property
    def main(self) -> RemoteFile:
        """Returns: The copy itself: its first file."""
        return self.files[0]


@dataclass(frozen=True)
class Rejected:
    """
    A copy the server listed that the central did not accept, and why.

    Attributes:
        kind: What it said the kind was.
        id: What it said the identifier was, cut and printable.
        reason: Why, in a sentence.
    """

    kind: str
    id: str
    reason: str


@dataclass(frozen=True)
class RemoteCatalogue:
    """
    What a server said it has.

    Attributes:
        fingerprint: Its fingerprint of the list.
        unchanged: The server answered that the list is the one the central already had.
        items: The copies accepted.
        rejected: The copies not accepted, each with its reason.
        problems: The kinds the server could not list: ``{"kind", "message"}``.
        piece_size: The piece the server suggests.
        max_piece: The most it serves in one.
    """

    fingerprint: str
    unchanged: bool = False
    items: tuple[RemoteItem, ...] = ()
    rejected: tuple[Rejected, ...] = ()
    problems: tuple[dict[str, str], ...] = ()
    piece_size: int = 4 * 1024**2
    max_piece: int = 8 * 1024**2


def safe_segment(value: object) -> str | None:
    """
    Judge a name that becomes part of a path on the central's disk.

    Args:
        value: What a server said.

    Returns:
        The name when it is plain; None otherwise.
    """
    if not isinstance(value, str) or not _SEGMENT.fullmatch(value):
        return None
    return value


def _count(value: object, *, ceiling: int) -> int | None:
    """
    Args:
        value: A size a server said.
        ceiling: The most it may be.

    Returns:
        The size, or None when it is not a whole number from 0 to the ceiling.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= ceiling:
        return None
    return value


def _moment(value: object) -> str | None:
    """
    Args:
        value: When a server said a copy was taken.

    Returns:
        The same moment in UTC, ISO 8601, or None when it is not one.
    """
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _file(raw: object) -> tuple[RemoteFile | None, str]:
    """
    Judge one file of a copy.

    Args:
        raw: What the server listed.

    Returns:
        The file, or None and the reason.
    """
    if not isinstance(raw, dict):
        return None, "a file is not an object"
    name = safe_segment(raw.get("name"))
    if name is None:
        return None, "a file has a name that is not a plain file name"
    role = raw.get("role")
    if role not in ("archive", "sidecar"):
        return None, f"{name}: its role is neither archive nor sidecar"
    size = _count(raw.get("size"), ceiling=MAX_PLAUSIBLE_BYTES)
    if size is None:
        return None, f"{name}: its size is not one a file can have"
    digest = raw.get("sha256")
    if digest is not None and not (isinstance(digest, str) and _SHA256.fullmatch(digest)):
        return None, f"{name}: its digest is not a SHA-256"
    return RemoteFile(name, size, digest, str(role)), ""


def _meta(raw: object) -> dict[str, str]:
    """
    Args:
        raw: What the server said a restore needs.

    Returns:
        The small, printable pairs of it; the rest is dropped.
    """
    if not isinstance(raw, dict):
        return {}
    found: dict[str, str] = {}
    for key, value in list(raw.items())[:16]:
        if isinstance(key, str) and isinstance(value, str) and len(key) <= 32 and len(value) <= 200:
            if _OWNER.fullmatch(value) or value == "":
                found[key] = value
    return found


def _item(raw: object, *, seen: set[tuple[str, str]]) -> tuple[RemoteItem | None, Rejected | None]:
    """
    Judge one copy of a server's list.

    Args:
        raw: What the server listed.
        seen: The copies already accepted, which this one must not repeat.

    Returns:
        The copy, or None and why it was not taken.
    """
    if not isinstance(raw, dict):
        return None, Rejected("?", "?", "a copy is not an object")
    kind, item_id = raw.get("kind"), raw.get("id")
    shown = (str(item_id)[:60] if isinstance(item_id, str) else "?").replace("\n", " ")
    if kind not in KINDS:
        return None, Rejected(str(kind)[:20], shown, "its kind is not one this central knows")
    if not isinstance(item_id, str) or not _ITEM_ID.fullmatch(item_id):
        return None, Rejected(str(kind), shown, "its identifier is not printable text")
    if (str(kind), item_id) in seen:
        return None, Rejected(str(kind), shown, "the server listed it twice")
    folder = safe_segment(raw.get("folder"))
    if folder is None:
        return None, Rejected(str(kind), shown, "its folder is not a plain name")
    owner = raw.get("owner")
    if not isinstance(owner, str) or not _OWNER.fullmatch(owner):
        return None, Rejected(str(kind), shown, "what it is a copy of is not printable text")
    created = _moment(raw.get("created_at"))
    if created is None:
        return None, Rejected(str(kind), shown, "its date is not a date")
    files_raw = raw.get("files")
    if not isinstance(files_raw, list) or not 1 <= len(files_raw) <= MAX_FILES:
        return None, Rejected(str(kind), shown, "it has no files, or too many")
    files: list[RemoteFile] = []
    for entry in files_raw:
        parsed, why = _file(entry)
        if parsed is None:
            return None, Rejected(str(kind), shown, why)
        files.append(parsed)
    if files[0].role != "archive" or len({f.name for f in files}) != len(files):
        return None, Rejected(
            str(kind), shown, "its files are not an archive and what describes it"
        )
    origin = raw.get("origin")
    blocked = raw.get("blocked")
    item = RemoteItem(
        kind=str(kind),
        id=item_id,
        owner=owner,
        folder=folder,
        created_at=created,
        origin=origin if origin in ORIGINS else "manual",
        sealed=raw.get("sealed") is True,
        files=tuple(files),
        meta=_meta(raw.get("meta")),
        blocked=blocked if isinstance(blocked, str) and len(blocked) <= 40 else None,
    )
    seen.add((item.kind, item.id))
    return item, None


def parse_catalogue(body: Any) -> RemoteCatalogue:
    """
    Judge what a server answered to the list of its copies.

    Args:
        body: The decoded JSON.

    Returns:
        What was accepted, and what was not and why. A body that is not a list of copies at all is
        a catalogue of nothing, with the reason said.
    """
    if not isinstance(body, dict):
        return RemoteCatalogue("", rejected=(Rejected("?", "?", "the answer is not an object"),))
    fingerprint = body.get("fingerprint")
    fingerprint = fingerprint if isinstance(fingerprint, str) and len(fingerprint) <= 64 else ""
    piece = _count(body.get("piece_size"), ceiling=64 * 1024**2) or 4 * 1024**2
    largest = _count(body.get("max_piece"), ceiling=64 * 1024**2) or 8 * 1024**2
    if body.get("unchanged") is True:
        return RemoteCatalogue(fingerprint, unchanged=True, piece_size=piece, max_piece=largest)
    raw_items = body.get("items")
    raw_items = raw_items if isinstance(raw_items, list) else []
    items: list[RemoteItem] = []
    rejected: list[Rejected] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_items[:MAX_ITEMS]:
        item, refusal = _item(raw, seen=seen)
        if item is not None:
            items.append(item)
        elif refusal is not None:
            rejected.append(refusal)
    if len(raw_items) > MAX_ITEMS:
        rejected.append(Rejected("?", "?", f"the server listed more than {MAX_ITEMS} copies"))
    problems = tuple(
        {"kind": str(p.get("kind", ""))[:20], "message": str(p.get("message", ""))[:300]}
        for p in (body.get("problems") or [])
        if isinstance(p, dict)
    )
    return RemoteCatalogue(
        fingerprint,
        items=tuple(items),
        rejected=tuple(rejected),
        problems=problems,
        piece_size=min(piece, largest),
        max_piece=largest,
    )
