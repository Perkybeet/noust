# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a ``mongodump --archive`` holds, read from its prelude.

A container's MongoDB is dumped as one archive on stdout, because Noust cannot
reach the directory tree mongodump would otherwise write inside the container.
The archive keeps the namespaces it was dumped from, and mongorestore loads
them where they came from unless told otherwise: restoring ``shop``'s archive
"as a new database" without ``--nsFrom``/``--nsTo`` would write over ``shop``.
So the restore has to know which database an archive is of, and the only
honest answer is the archive itself.

The archive starts with a prelude that mongo-tools writes before any data
(``archive/prelude.go``): the magic number ``0x8199e26d`` (little-endian), a
BSON header document, one BSON document per collection (``db``,
``collection``, ``metadata``, ``size``...), and a terminator of four ``0xFF``
bytes. This module reads exactly that, plain or gzipped, without running
anything: reading a file is not executing a process.
"""

from __future__ import annotations

import gzip
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

#: The first four bytes of every mongodump archive.
ARCHIVE_MAGIC = struct.pack("<I", 0x8199E26D)

#: What ends the prelude (and every block of the body): int32 -1.
_TERMINATOR = b"\xff\xff\xff\xff"

#: The first two bytes of a gzip stream.
_GZIP_MAGIC = b"\x1f\x8b"

#: BSON caps a document at 16 MiB; anything larger is not a prelude.
_MAX_DOCUMENT = 16 * 1024 * 1024

#: A prelude names one document per collection; a million is not a dump.
_MAX_NAMESPACES = 1_000_000

#: Fixed widths of the BSON element types a prelude can carry.
_FIXED_WIDTH = {0x01: 8, 0x07: 12, 0x08: 1, 0x09: 8, 0x0A: 0, 0x10: 4, 0x11: 8, 0x12: 8, 0x13: 16}


class ArchiveError(ValueError):
    """The file is not a mongodump archive, or its prelude is cut short."""


@dataclass(frozen=True)
class ArchivePrelude:
    """
    What an archive's prelude says it holds.

    Attributes:
        namespaces: ``(database, collection)`` per collection, in order.
    """

    namespaces: tuple[tuple[str, str], ...]

    @property
    def databases(self) -> tuple[str, ...]:
        """
        Returns:
            Each database the archive holds collections of, once, in order.
        """
        return tuple(dict.fromkeys(db for db, _ in self.namespaces if db))


def is_gzip(path: Path) -> bool:
    """
    Say whether a file is gzipped, by its first bytes rather than its name.

    Args:
        path: The file.

    Returns:
        True when it starts with the gzip magic.

    Raises:
        OSError: When the file cannot be read.
    """
    with path.open("rb") as handle:
        return handle.read(2) == _GZIP_MAGIC


def _open(path: Path) -> BinaryIO:
    """
    Open an archive for reading, decompressing it when it is gzipped.

    Args:
        path: The archive.

    Returns:
        A binary stream of the plain archive.

    Raises:
        OSError: When the file cannot be read.
    """
    if is_gzip(path):
        return gzip.open(path, "rb")  # type: ignore[return-value]
    return path.open("rb")


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    """
    Read exactly ``size`` bytes.

    Args:
        stream: The archive.
        size: How many bytes.

    Returns:
        The bytes.

    Raises:
        ArchiveError: When the file ends first.
    """
    data = stream.read(size)
    if len(data) != size:
        raise ArchiveError("The archive ends in the middle of its prelude: it is cut short.")
    return data


def _cstring(document: bytes, offset: int) -> tuple[str, int]:
    """
    Read a BSON element name.

    Args:
        document: The document.
        offset: Where the name starts.

    Returns:
        The name and the offset after its NUL.

    Raises:
        ArchiveError: When the name is not terminated.
    """
    end = document.find(b"\x00", offset)
    if end < 0:
        raise ArchiveError("A prelude document has an unterminated field name.")
    return document[offset:end].decode("utf-8", errors="replace"), end + 1


def _strings(document: bytes) -> dict[str, str]:
    """
    Read the top-level string fields of a BSON document.

    Args:
        document: The whole document, its length prefix included.

    Returns:
        Each string field by name; every other field is skipped.

    Raises:
        ArchiveError: When the document is malformed.
    """
    found: dict[str, str] = {}
    offset = 4
    end = len(document) - 1
    while offset < end:
        kind = document[offset]
        name, offset = _cstring(document, offset + 1)
        if kind in (0x02, 0x0D, 0x0E):
            (length,) = struct.unpack_from("<i", document, offset)
            if length < 1 or offset + 4 + length > end + 1:
                raise ArchiveError("A prelude document has a string longer than itself.")
            raw = document[offset + 4 : offset + 4 + length - 1]
            if kind == 0x02:
                found[name] = raw.decode("utf-8", errors="replace")
            offset += 4 + length
        elif kind in (0x03, 0x04):
            (length,) = struct.unpack_from("<i", document, offset)
            if length < 5:
                raise ArchiveError("A prelude document has a malformed embedded document.")
            offset += length
        elif kind == 0x05:
            (length,) = struct.unpack_from("<i", document, offset)
            if length < 0:
                raise ArchiveError("A prelude document has a malformed binary field.")
            offset += 4 + 1 + length
        elif kind in _FIXED_WIDTH:
            offset += _FIXED_WIDTH[kind]
        else:
            # Nothing mongodump writes in a prelude has another type: stop
            # rather than guess the width of something unknown.
            raise ArchiveError(f"A prelude document has a field of unknown type 0x{kind:02x}.")
        if offset > end:
            raise ArchiveError("A prelude document runs past its own length.")
    return found


def _document(stream: BinaryIO, first: bytes) -> bytes:
    """
    Read one BSON document whose first four bytes are already read.

    Args:
        stream: The archive.
        first: The document's length prefix.

    Returns:
        The whole document.

    Raises:
        ArchiveError: When its length is impossible or the file ends first.
    """
    (length,) = struct.unpack("<i", first)
    if length < 5 or length > _MAX_DOCUMENT:
        raise ArchiveError(f"A prelude document claims {length} bytes: this is not an archive.")
    document = first + _read_exact(stream, length - 4)
    if document[-1] != 0:
        raise ArchiveError("A prelude document does not end where its length says.")
    return document


def read_prelude(path: Path) -> ArchivePrelude:
    """
    Read what a mongodump archive says it holds, before any of its data.

    Args:
        path: The archive, plain or gzipped.

    Returns:
        Its namespaces.

    Raises:
        ArchiveError: When the file is not a mongodump archive or its prelude
            is cut short or malformed.
        OSError: When the file cannot be read.
    """
    try:
        with _open(path) as stream:
            if stream.read(4) != ARCHIVE_MAGIC:
                raise ArchiveError(
                    "The file does not start with the mongodump archive magic: "
                    "it is not an archive written by 'mongodump --archive'."
                )
            _document(stream, _read_exact(stream, 4))
            namespaces: list[tuple[str, str]] = []
            while True:
                head = _read_exact(stream, 4)
                if head == _TERMINATOR:
                    return ArchivePrelude(tuple(namespaces))
                if len(namespaces) >= _MAX_NAMESPACES:
                    raise ArchiveError("The prelude never ends: this is not an archive.")
                try:
                    fields = _strings(_document(stream, head))
                except struct.error as exc:
                    raise ArchiveError(f"A prelude document is malformed: {exc}") from exc
                namespaces.append((fields.get("db", ""), fields.get("collection", "")))
    except (EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise ArchiveError(f"The gzip stream around the archive is damaged: {exc}") from exc


def read_whole(path: Path) -> int:
    """
    Read a gzipped archive to its end, so a truncated file shows.

    A plain archive has no checksum to read; a gzipped one does, and gzip
    refuses a stream that ends early or whose trailer does not match.

    Args:
        path: The archive.

    Returns:
        The size of the plain archive in bytes.

    Raises:
        ArchiveError: When the gzip stream is cut short or damaged.
        OSError: When the file cannot be read.
    """
    total = 0
    try:
        with _open(path) as stream:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
    except (EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise ArchiveError(f"The gzip stream around the archive is damaged: {exc}") from exc
    return total
