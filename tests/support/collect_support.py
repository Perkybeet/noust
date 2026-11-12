# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A server for the collector to take copies from, and the ways it can go wrong (item 195).

:class:`FakeApi` is a server that answers the three things the collector asks (its list, a range of
a file, and being told which copies the central holds) from a real set of copies on a disk
(:mod:`tests.support.backup_collection_support`), the list through the real catalogue and the real
judgement of what a server says. A test makes it misbehave the way a server can: a tunnel that
drops in the middle of a file, a list that lies about a digest or a size, a file that is shorter
than it said, a name that climbs. Nothing here is the engine's: it only stands where the network
would.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from noust.core.exceptions import NodeUnreachableError
from noust.fleet.collect.api import NodeUnsupported
from noust.fleet.collect.names import RemoteCatalogue, RemoteFile, RemoteItem, parse_catalogue
from tests.support.backup_collection_support import Disk


class FakeApi:
    """
    One server's copies, as its API serves them.

    Attributes:
        disk: The server's copies.
        reads: Every range asked, as ``(kind, id, file, offset, length)``.
        lists: How many times the list was asked.
        held: What the central last said it holds.
        mutate: Changes the list's body before it is judged (a lying server).
        on_read: Replaces the answer to a range (a server that fails, or lies).
        unsupported: The server is one before 4.3: it has no such routes.
    """

    legacy = False

    def __init__(self, disk: Disk) -> None:
        self.disk = disk
        self.reads: list[tuple[str, str, str, int, int]] = []
        self.lists = 0
        self.held: list[tuple[str, str, str | None]] | None = None
        self.mutate: Callable[[dict[str, Any]], None] | None = None
        self.on_read: Callable[[RemoteItem, RemoteFile, int, int, bytes], bytes] | None = None
        self.unsupported = False
        self.refuse_held = False
        self.sealing_required = False

    def body(self) -> dict[str, Any]:
        catalogue = self.disk.catalogue().build()
        items = [item.to_dict() for item in catalogue.items]
        if self.sealing_required:
            for entry in items:
                entry["blocked"] = None if entry["sealed"] else "sealing_required"
        body: dict[str, Any] = {
            "fingerprint": catalogue.fingerprint,
            "unchanged": False,
            "items": items,
            "problems": [dict(p) for p in catalogue.problems],
        }
        if self.mutate is not None:
            self.mutate(body)
        return body

    def list(self, *, known: str | None = None) -> RemoteCatalogue:
        self.lists += 1
        if self.unsupported:
            raise NodeUnsupported("no backup-collection routes")
        body = self.body()
        if known and known == body["fingerprint"]:
            return parse_catalogue({"fingerprint": known, "unchanged": True})
        return parse_catalogue(json.loads(json.dumps(body)))

    def read(self, item: RemoteItem, file: RemoteFile, offset: int, length: int) -> bytes:
        self.reads.append((item.kind, item.id, file.name, offset, length))
        found = self.disk.catalogue().item(item.kind, item.id)
        listed = found.file(file.name) if found is not None else None
        data = b""
        if listed is not None:
            with open(listed.path, "rb") as handle:
                handle.seek(offset)
                data = handle.read(length)
        if self.on_read is not None:
            return self.on_read(item, file, offset, length, data)
        return data

    def say_held(self, held: Sequence[tuple[str, str, str | None]]) -> None:
        if self.refuse_held:
            raise NodeUnreachableError("the server did not answer")
        self.held = list(held)


def drops_once(at_offset: int) -> Callable[[RemoteItem, RemoteFile, int, int, bytes], bytes]:
    """
    Make a server whose connection drops the first time a file is read at an offset.

    Args:
        at_offset: Where the connection drops.

    Returns:
        An ``on_read`` for :class:`FakeApi`.
    """
    state = {"dropped": False}

    def answer(item: RemoteItem, file: RemoteFile, offset: int, length: int, data: bytes) -> bytes:
        if offset >= at_offset and not state["dropped"] and file.role == "archive":
            state["dropped"] = True
            raise NodeUnreachableError("The tunnel dropped")
        return data

    return answer


def file_text(path: Path) -> bytes:
    """
    Args:
        path: A file.

    Returns:
        What is in it.
    """
    return path.read_bytes()
