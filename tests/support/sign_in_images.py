# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pictures for the tests of the sign-in image, made by hand from the formats' own structure.

Nothing here draws: what the validator reads is the container (chunks, segments, the header that
says how large a picture is), so a file that has the right structure and a little data is as good a
specimen as a photograph, and the bytes of each part are in plain sight for a test to damage, add to
or take away. The ones a browser must also draw are made by the browser in the end-to-end test.
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_chunk(kind: bytes, body: bytes = b"", *, crc: int | None = None) -> bytes:
    """
    One PNG chunk.

    Args:
        kind: Its four letters.
        body: Its data.
        crc: A checksum to write instead of the right one, to make a damaged chunk.

    Returns:
        Length, name, data and checksum.
    """
    checksum = zlib.crc32(kind + body) if crc is None else crc
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", checksum)


def png(
    width: int = 8,
    height: int = 6,
    *,
    extra: tuple[bytes, ...] = (),
    after_header: tuple[bytes, ...] = (),
    colour_type: int = 2,
    depth: int = 8,
    trailer: bytes = b"",
    end: bool = True,
) -> bytes:
    """
    A PNG of ``width`` by ``height`` filled with one colour.

    Args:
        width: Pixels across.
        height: Pixels down.
        extra: Chunks to put between the header and the pixels (each already a whole chunk).
        after_header: Chunks to put right after ``IHDR``, before ``extra``.
        colour_type: The colour type of the header (2 is RGB).
        depth: The bit depth.
        trailer: Bytes to put after ``IEND``.
        end: False leaves ``IEND`` out, a file that stops short.

    Returns:
        The file.
    """
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour_type]
    row_bytes = (width * channels * depth + 7) // 8
    pixels = zlib.compress((b"\x00" + b"\x40" * row_bytes) * min(height, 64))
    header = png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, colour_type, 0, 0, 0))
    palette = png_chunk(b"PLTE", b"\x10\x20\x30" * 2) if colour_type == 3 else b""
    return (
        PNG_SIGNATURE
        + header
        + b"".join(after_header)
        + palette
        + b"".join(extra)
        + png_chunk(b"IDAT", pixels)
        + (png_chunk(b"IEND") if end else b"")
        + trailer
    )


def png_kinds(data: bytes) -> list[bytes]:
    """
    The names of the chunks of a PNG, in order.

    Args:
        data: A well-formed PNG.

    Returns:
        Each chunk's four letters.
    """
    kinds, offset = [], len(PNG_SIGNATURE)
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kinds.append(data[offset + 4 : offset + 8])
        offset += 12 + length
    return kinds


def jpeg_segment(marker: int, body: bytes = b"") -> bytes:
    """
    One JPEG segment that has a length.

    Args:
        marker: The byte after ``0xFF``.
        body: Its data.

    Returns:
        The marker, the length (which counts itself) and the data.
    """
    return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body


def jpeg(
    width: int = 8,
    height: int = 6,
    *,
    before_frame: tuple[bytes, ...] = (),
    after_frame: tuple[bytes, ...] = (),
    sof: int = 0xC0,
    components: int = 3,
    scans: int = 1,
    end: bool = True,
    trailer: bytes = b"",
) -> bytes:
    """
    A JPEG with the structure of one and entropy-coded data that nothing decodes.

    Args:
        width: What the frame header says.
        height: What the frame header says.
        before_frame: Segments to put between ``SOI`` and the frame header.
        after_frame: Segments to put between the frame header and the first scan.
        sof: The frame marker: ``0xC0`` baseline, ``0xC2`` progressive.
        components: Colour components the frame declares.
        scans: How many scans follow (a progressive file has several).
        end: False leaves ``EOI`` out, a file that stops short.
        trailer: Bytes to put after ``EOI``.

    Returns:
        The file.
    """
    frame = struct.pack(">BHHB", 8, height, width, components) + b"".join(
        bytes([index + 1, 0x11, 0]) for index in range(components)
    )
    scan_header = bytes([components]) + b"".join(
        bytes([index + 1, 0x00]) for index in range(components)
    )
    scan = jpeg_segment(0xDA, scan_header + b"\x00\x3f\x00") + b"\x12\x34\xff\x00\x56\xff\xd0\x78"
    return (
        b"\xff\xd8"
        + b"".join(before_frame)
        + jpeg_segment(0xDB, b"\x00" + bytes(range(64)))
        + jpeg_segment(sof, frame)
        + b"".join(after_frame)
        + scan * scans
        + (b"\xff\xd9" if end else b"")
        + trailer
    )


def jfif(thumbnail: bytes = b"") -> bytes:
    """
    A JFIF header, with a thumbnail when asked.

    Args:
        thumbnail: Pixels of an embedded thumbnail, 3 bytes each; its size is said in the header.

    Returns:
        The ``APP0`` segment.
    """
    pixels = len(thumbnail) // 3
    return jpeg_segment(
        0xE0,
        b"JFIF\x00\x01\x01\x01\x00\x48\x00\x48" + bytes([pixels, 1 if pixels else 0]) + thumbnail,
    )


def riff(chunks: bytes, *, size: int | None = None) -> bytes:
    """
    A WebP container.

    Args:
        chunks: What goes inside, already as chunks.
        size: The size to write in the header instead of the right one.

    Returns:
        ``RIFF``, the size, ``WEBP`` and the chunks.
    """
    declared = 4 + len(chunks) if size is None else size
    return b"RIFF" + struct.pack("<I", declared) + b"WEBP" + chunks


def riff_chunk(kind: bytes, body: bytes) -> bytes:
    """
    One chunk of a RIFF file, padded to an even length.

    Args:
        kind: Its four letters.
        body: Its data.

    Returns:
        The name, the size and the data.
    """
    return kind + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")


def vp8_body(width: int, height: int) -> bytes:
    """
    The start of a lossy bitstream: the key frame's tag, its start code and its size.

    Args:
        width: Pixels across.
        height: Pixels down.

    Returns:
        The bytes, with filler where the picture's data would be.
    """
    return b"\x30\x01\x00\x9d\x01\x2a" + struct.pack("<HH", width, height) + b"\x00" * 8


def vp8l_body(width: int, height: int) -> bytes:
    """
    The start of a lossless bitstream: its signature and its size.

    Args:
        width: Pixels across.
        height: Pixels down.

    Returns:
        The bytes, with filler where the picture's data would be.
    """
    packed = (width - 1) | ((height - 1) << 14)
    return b"\x2f" + struct.pack("<I", packed) + b"\x00" * 6


def webp(width: int = 8, height: int = 6, *, lossless: bool = False) -> bytes:
    """
    A simple WebP: one image chunk and nothing else.

    Args:
        width: Pixels across.
        height: Pixels down.
        lossless: ``VP8L`` rather than ``VP8 ``.

    Returns:
        The file.
    """
    if lossless:
        return riff(riff_chunk(b"VP8L", vp8l_body(width, height)))
    return riff(riff_chunk(b"VP8 ", vp8_body(width, height)))


def webp_extended(
    width: int = 8,
    height: int = 6,
    *,
    flags: int = 0x00,
    canvas: tuple[int, int] | None = None,
    before: tuple[bytes, ...] = (),
    after: tuple[bytes, ...] = (),
) -> bytes:
    """
    An extended WebP: a ``VP8X`` header, optional chunks, one lossy image, optional chunks.

    Args:
        width: Pixels across the image.
        height: Pixels down the image.
        flags: The ``VP8X`` flags: 0x20 ICC profile, 0x10 alpha, 0x08 EXIF, 0x04 XMP, 0x02 animation.
        canvas: What ``VP8X`` says the size is, when it is to disagree with the image.
        before: Whole chunks between ``VP8X`` and the image.
        after: Whole chunks after the image (``EXIF ``, ``XMP ``).

    Returns:
        The file.
    """
    canvas_width, canvas_height = canvas or (width, height)
    header = (
        bytes([flags, 0, 0, 0])
        + (canvas_width - 1).to_bytes(3, "little")
        + (canvas_height - 1).to_bytes(3, "little")
    )
    return riff(
        riff_chunk(b"VP8X", header)
        + b"".join(before)
        + riff_chunk(b"VP8 ", vp8_body(width, height))
        + b"".join(after)
    )
