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
import time
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

#: A prelude names one document per collection. A real server holds
#: thousands at most; past this the file is something else posing as a dump,
#: and every namespace becomes part of a restore's arguments.
_MAX_NAMESPACES = 100_000

#: Bytes the documents of a prelude may add up to. Each is capped at 16 MiB,
#: but a prelude of a hundred thousand of those is gigabytes read for nothing.
_MAX_PRELUDE = 64 * 1024 * 1024

#: MongoDB refuses a database name of 64 bytes or more.
MAX_DATABASE_NAME = 64

#: MongoDB caps a collection name (and, before 4.4, a whole namespace) at 255 bytes.
MAX_COLLECTION_NAME = 255

#: How long reading a gzipped archive to its end may take. The read only
#: decompresses and discards, so time is the bound that matters: a small gzip
#: bomb expands for hours.
READ_WHOLE_DEADLINE = 3600

#: How many times its compressed size a gzipped archive may expand to. A
#: dump of real data compresses a few times, and gzip itself cannot exceed
#: about 1032:1; past this it is a bomb, not a database.
_MAX_EXPANSION = 200

#: Below this a gzipped archive is never refused for its expansion: a small
#: dump of very repetitive documents can compress far better than a big one.
_EXPANSION_FLOOR = 1024 * 1024 * 1024

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


def _namespace(fields: dict[str, str]) -> tuple[str, str]:
    """
    Take a collection's namespace from its prelude document, refusing impossible names.

    The names go into a restore's ``--nsInclude``/``--nsFrom`` and into what
    the operator is shown, so a name MongoDB itself would refuse is a forged
    archive, not one to aim a restore with.

    Args:
        fields: The document's string fields.

    Returns:
        ``(database, collection)``.

    Raises:
        ArchiveError: When a name is longer than MongoDB allows.
    """
    database = fields.get("db", "")
    collection = fields.get("collection", "")
    if len(database.encode("utf-8")) >= MAX_DATABASE_NAME:
        raise ArchiveError(
            f"The prelude names a database of more than {MAX_DATABASE_NAME - 1} bytes, "
            "which MongoDB does not allow: this is not an archive mongodump wrote."
        )
    if len(collection.encode("utf-8")) > MAX_COLLECTION_NAME:
        raise ArchiveError(
            f"The prelude names a collection of more than {MAX_COLLECTION_NAME} bytes, "
            "which MongoDB does not allow: this is not an archive mongodump wrote."
        )
    return database, collection


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
            read = len(_document(stream, _read_exact(stream, 4)))
            namespaces: list[tuple[str, str]] = []
            while True:
                head = _read_exact(stream, 4)
                if head == _TERMINATOR:
                    return ArchivePrelude(tuple(namespaces))
                if len(namespaces) >= _MAX_NAMESPACES:
                    raise ArchiveError(
                        f"The prelude names more than {_MAX_NAMESPACES} collections: "
                        "this is not an archive Noust restores."
                    )
                try:
                    document = _document(stream, head)
                    fields = _strings(document)
                except struct.error as exc:
                    raise ArchiveError(f"A prelude document is malformed: {exc}") from exc
                read += len(document)
                if read > _MAX_PRELUDE:
                    raise ArchiveError(
                        f"The prelude runs past {_MAX_PRELUDE // (1024 * 1024)} MiB: "
                        "this is not an archive."
                    )
                namespaces.append(_namespace(fields))
    except (EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise ArchiveError(f"The gzip stream around the archive is damaged: {exc}") from exc


def read_whole(path: Path, *, deadline: float = READ_WHOLE_DEADLINE) -> int:
    """
    Read a gzipped archive to its end, so a truncated file shows.

    A plain archive has no checksum to read, so only its size is taken; a
    gzipped one does, and gzip refuses a stream that ends early or whose
    trailer does not match. The decompression is bounded twice, because the
    file is untrusted input that a few kilobytes can make expand for hours:
    by ``deadline``, and by how far past its compressed size it may grow.

    Args:
        path: The archive.
        deadline: Seconds the read may take.

    Returns:
        The size of the plain archive in bytes.

    Raises:
        ArchiveError: When the gzip stream is cut short or damaged, expands
            past what a dump can, or takes longer than ``deadline``.
        OSError: When the file cannot be read.
    """
    if not is_gzip(path):
        return path.stat().st_size
    limit = max(_EXPANSION_FLOOR, path.stat().st_size * _MAX_EXPANSION)
    ends = time.monotonic() + deadline
    total = 0
    try:
        with _open(path) as stream:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise ArchiveError(
                        f"The gzipped archive expands past {limit} bytes, "
                        f"{_MAX_EXPANSION} times its own size: it is not a dump."
                    )
                if time.monotonic() > ends:
                    raise ArchiveError(
                        f"Reading the gzipped archive took longer than {int(deadline)} seconds; "
                        "it was not checked to its end."
                    )
    except (EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise ArchiveError(f"The gzip stream around the archive is damaged: {exc}") from exc
    return total
