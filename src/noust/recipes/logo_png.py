# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A logo that exists only as a picture: a PNG of 256 pixels or more, with nothing in it but pixels.

Most projects publish their mark as an SVG, and the closed list of :mod:`noust.recipes.logos` is how
that is served. A few publish a picture only (the application's icon, a raster logo), and a mark
that is shown at 64 pixels on a screen twice as dense needs 256 on a side to be sharp. The picture is
served as ``image/png`` with ``nosniff``, to an ``<img>`` that never runs anything; the check is that it
is a PNG and only that:

- the signature, an ``IHDR`` first, pixels (``IDAT``, in one run) and ``IEND`` last with nothing
  after it, every checksum holding;
- the chunks that say how to read pixels (``PLTE``, ``tRNS``, ``sRGB``) and no other: text, EXIF, an
  embedded colour profile, a time, an animation and every chunk this does not know are metadata, and a
  place for what is not a logo to ride in;
- 256 to 1024 pixels on each side, a colour type and a bit depth the format defines, at most 64 KiB.

:func:`sanitize_png` is how a file from a project becomes one that passes: it keeps those chunks, in
that order, and writes the rest away. It does not decode, scale or recolour: the pixels are the
project's. ``scripts/recipe_logos.py add`` calls it; a PNG that is not 256 pixels is a file to
scale first, or a project that has the monogram.
"""

from __future__ import annotations

import struct
import zlib

from noust.recipes.model import recipe_error

__all__ = ["MAX_PNG_BYTES", "MAX_SIDE", "MIN_SIDE", "PNG_SIGNATURE", "sanitize_png", "validate_png"]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: The largest logo shipped as a picture, and the sides it may have.
MAX_PNG_BYTES = 64 * 1024
MIN_SIDE = 256
MAX_SIDE = 1024
#: What a project's file may weigh before it is stripped.
MAX_SOURCE_BYTES = 4 * 1024 * 1024

#: The chunks that say how to read pixels, besides the header, the pixels and the end.
_PIXEL_CHUNKS = (b"sRGB", b"PLTE", b"tRNS")
_BIT_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_MAX_CHUNK = 2**31 - 1


def _chunks(name: str, data: bytes, *, checked: bool = True) -> list[tuple[bytes, bytes]]:
    """
    Cut a PNG into its chunks.

    Args:
        name: The recipe, for the message.
        data: The file, signature included.
        checked: Whether every checksum must hold; a stripper checks only the chunks it keeps.

    Returns:
        Each chunk's name and body, until ``IEND`` is read (what follows it is not read).

    Raises:
        RecipeError: A chunk that runs past the file or is longer than a PNG allows, or a checksum
            that does not hold.
    """
    where = f"logo of {name}"
    found: list[tuple[bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        if offset + 12 > len(data):
            raise recipe_error(name, f"{where} is truncated")
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        if length > _MAX_CHUNK:
            raise recipe_error(name, f"{where} has a chunk longer than a PNG allows")
        end = offset + 12 + length
        if end > len(data):
            raise recipe_error(name, f"{where} is truncated")
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        (checksum,) = struct.unpack(">I", data[offset + 8 + length : end])
        if (
            checked or kind in (b"IHDR", b"IDAT", b"IEND", *_PIXEL_CHUNKS)
        ) and checksum != zlib.crc32(kind + body):
            raise recipe_error(
                name, f"{where}: the checksum of a {kind.decode('latin-1')} chunk does not hold"
            )
        found.append((kind, body))
        offset = end
        if kind == b"IEND":
            break
    return found


def validate_png(name: str, data: bytes) -> bytes:
    """
    Check that a logo is a plain PNG of the right size, with nothing in it but pixels.

    Args:
        name: The recipe, for the message.
        data: The file.

    Returns:
        The data, unchanged.

    Raises:
        RecipeError: It is too large, not a PNG, has a chunk that is not about pixels, has the
            chunks out of order or a checksum that does not hold, is outside 256 to 1024 pixels on a
            side, or declares a colour type, a bit depth or an interlace the format does not have.
    """
    where = f"logo of {name}"
    if len(data) > MAX_PNG_BYTES:
        raise recipe_error(name, f"{where} is larger than {MAX_PNG_BYTES // 1024} KiB")
    if not data.startswith(PNG_SIGNATURE):
        raise recipe_error(name, f"{where} is not a PNG")
    chunks = _chunks(name, data)
    if not chunks or chunks[0][0] != b"IHDR":
        raise recipe_error(name, f"{where} must start with an IHDR chunk")
    if chunks[-1][0] != b"IEND":
        raise recipe_error(name, f"{where} has no IEND chunk")
    consumed = len(PNG_SIGNATURE) + sum(12 + len(body) for _, body in chunks)
    if consumed != len(data):
        raise recipe_error(name, f"{where} has data after IEND")
    colour = _header(name, where, chunks[0][1])
    seen: list[bytes] = []
    pixels_started = False
    for kind, body in chunks[1:-1]:
        if kind == b"IHDR":
            raise recipe_error(name, f"{where} has more than one IHDR chunk")
        if kind == b"IDAT":
            pixels_started = True
            continue
        if kind not in _PIXEL_CHUNKS:
            raise recipe_error(
                name,
                f"{where} has a {kind.decode('latin-1')!r} chunk, which carries something other "
                "than pixels (a PNG here is its header, its palette, its pixels and nothing else)",
            )
        if pixels_started:
            raise recipe_error(
                name, f"{where} has a {kind.decode('latin-1')} chunk after its pixels"
            )
        if kind in seen:
            raise recipe_error(name, f"{where} has a {kind.decode('latin-1')} chunk twice")
        seen.append(kind)
        if kind == b"PLTE" and (len(body) % 3 or not 3 <= len(body) <= 768):
            raise recipe_error(name, f"{where} has a PLTE chunk that is not a palette")
    if not pixels_started:
        raise recipe_error(name, f"{where} has no IDAT chunk: there are no pixels")
    if colour == 3 and b"PLTE" not in seen:
        raise recipe_error(name, f"{where} is a palette picture without its PLTE chunk")
    return data


def _header(name: str, where: str, body: bytes) -> int:
    """
    Args:
        name: The recipe, for the message.
        where: What is being checked, for the message.
        body: The body of the ``IHDR`` chunk.

    Returns:
        The colour type.

    Raises:
        RecipeError: A header the format does not have, or a size outside what a logo may be.
    """
    if len(body) != 13:
        raise recipe_error(name, f"{where} has an IHDR chunk that is not 13 bytes long")
    width, height, depth, colour, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", body
    )
    if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
        raise recipe_error(
            name,
            f"{where} is {width} by {height} pixels: between {MIN_SIDE} and {MAX_SIDE} on each side "
            "(a smaller picture is blurred on a dense screen)",
        )
    if colour not in _BIT_DEPTHS:
        raise recipe_error(name, f"{where} has colour type {colour}, which PNG does not have")
    if depth not in _BIT_DEPTHS[colour]:
        raise recipe_error(
            name, f"{where} has a bit depth of {depth}, which colour type {colour} does not have"
        )
    if compression or filtering:
        raise recipe_error(
            name,
            f"{where} has an IHDR chunk with a compression or a filter method PNG does not define",
        )
    if interlace not in (0, 1):
        raise recipe_error(
            name, f"{where} has an interlace method ({interlace}) PNG does not define"
        )
    return int(colour)


def sanitize_png(name: str, source: bytes) -> bytes:
    """
    Write a PNG again with only what a logo is: its header, how to read its pixels, its pixels.

    Args:
        name: The recipe, for the message.
        source: The file as the project published it.

    Returns:
        The PNG, already checked by :func:`validate_png`.

    Raises:
        RecipeError: It is not a PNG, is animated, is truncated, has a checksum that does not hold
            in a chunk that is kept, or fails the check after it is stripped (a size outside 256 to
            1024 pixels, more than 64 KiB).
    """
    where = f"logo of {name}"
    if len(source) > MAX_SOURCE_BYTES:
        raise recipe_error(name, f"{where} is larger than {MAX_SOURCE_BYTES // 1024} KiB")
    if not source.startswith(PNG_SIGNATURE):
        raise recipe_error(name, f"{where} is not a PNG")
    chunks = _chunks(name, source, checked=False)
    if any(kind in (b"acTL", b"fcTL", b"fdAT") for kind, _ in chunks):
        raise recipe_error(name, f"{where} is an animated PNG; a logo is a still picture")
    if not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise recipe_error(name, f"{where} must run from an IHDR chunk to an IEND chunk")
    kept = [chunks[0]]
    for wanted in _PIXEL_CHUNKS:
        kept.extend(chunk for chunk in chunks[1:-1] if chunk[0] == wanted)
    kept.extend(chunk for chunk in chunks[1:-1] if chunk[0] == b"IDAT")
    kept.append(chunks[-1])
    out = bytearray(PNG_SIGNATURE)
    for kind, body in kept:
        out += (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )
    return validate_png(name, bytes(out))
