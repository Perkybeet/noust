# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What may be the picture on the sign-in screen.

An operator can put a company's logo, or any picture, where Noust draws a noust from the side
(``noust.core.sign_in_image``). The file is served to whoever opens the console, with no session,
from the console's own address, so it is judged here, at the one place, and rewritten before
anything is stored:

- **Its type is what its bytes are.** PNG, JPEG or WebP, by the signature, never by a name or a
  ``Content-Type`` the sender chose. An SVG is code, and opened at its own address it runs, so it
  is refused by name (``sign_in_image.svg_refused``), whatever it was called.
- **Its size is read from its own headers and held to the limits**: 1 MiB, and 4096 pixels on a
  side. A header that claims more than that is refused without decoding anything, which is what
  keeps a file of a few hundred bytes that says it is 65535 pixels wide from being a picture.
- **It is rewritten to carry pixels and nothing else.** What a camera or an editor leaves in a
  file (a place, a name, a thumbnail, a comment, an embedded profile's text) is taken out:
  JPEG ``APP1`` to ``APP15`` and comments, and the thumbnail a JFIF header may hold; PNG's text,
  ``eXIf``, ``tIME`` and every ancillary chunk this does not know; WebP's ``EXIF`` and ``XMP``.
  The pixels are not decoded, scaled or recoloured: their bytes are the sender's, copied. What
  follows the end of the image is dropped, so a file that is also an archive, or an SVG, is not.
  An animation is refused (WebP) or becomes its first frame (PNG): nobody can pause it on a screen
  people read, which WCAG 2.2.2 asks for.

It uses the standard library only: a decoder would be a dependency, and the checks here are of the
container, which is what a hostile file attacks. Recipes have their own PNG check
(:mod:`noust.recipes.logo_png`), written for a closed list of 64 KiB logos that are either exactly
what it accepts or refused; this one takes what an operator uploads and strips it, so it keeps the
walk over the chunks to itself rather than bend the other's policy.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import struct
import zlib
from dataclasses import dataclass

from noust.core.exceptions import ValidationError
from noust.core.server_text import say

__all__ = [
    "FITS",
    "MAX_ALT_CHARS",
    "MAX_IMAGE_BYTES",
    "MAX_SIDE",
    "MAX_UPLOAD_CHARS",
    "CleanImage",
    "check_alt",
    "check_fit",
    "check_image",
    "decode_upload",
]

#: The largest file accepted, as it is sent, and the most pixels on a side.
MAX_IMAGE_BYTES = 1024 * 1024
MAX_SIDE = 4096

#: The longest description a picture is given.
MAX_ALT_CHARS = 120

#: The most characters of base64 a file of the limit takes, so a body is refused before it is decoded.
MAX_UPLOAD_CHARS = 4 * ((MAX_IMAGE_BYTES + 2) // 3)

#: How a picture is drawn in its place: ``cover`` fills the space and crops, ``contain`` fits it
#: whole.
FITS: tuple[str, ...] = ("cover", "contain")

#: The types, by the name the rest of this module uses for each.
_CONTENT_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: The chunks of a PNG that say how to read the pixels, and nothing else, kept when they come
#: before the first ``IDAT``: gamma, chromaticities, the sRGB intent, the colour profile, the
#: significant bits, the density, the background and the transparency.
_PNG_KEPT = frozenset({b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT", b"pHYs", b"bKGD", b"tRNS"})

#: The bit depths each colour type of a PNG has.
_PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}

#: The JPEG frames a browser draws: baseline, extended sequential and progressive, Huffman coded.
_JPEG_FRAMES = frozenset({0xC0, 0xC1, 0xC2})

#: Every marker that starts a frame, so that one this does not draw is told apart from damage.
_JPEG_ANY_FRAME = frozenset(set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}) | {0xDE}

#: Control characters, line and paragraph separators and the characters that reorder text.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")

#: What may come before the first tag of a text file: a byte-order mark and white space.
_TEXT_LEAD = b"\xef\xbb\xbf \t\r\n\x0b\x0c"


@dataclass(frozen=True)
class CleanImage:
    """
    A picture that was judged and rewritten, ready to be kept.

    Attributes:
        data: The bytes to store and to serve.
        content_type: ``image/png``, ``image/jpeg`` or ``image/webp``: what it is, not what the
            sender said.
        width: Pixels across.
        height: Pixels down.
        sha256: The digest of ``data``, which is how the picture is addressed.
    """

    data: bytes
    content_type: str
    width: int
    height: int
    sha256: str

    @property
    def size(self) -> int:
        """Returns: The bytes kept."""
        return len(self.data)


def _refuse(code: str, *, field: str = "image", **params: str | int) -> ValidationError:
    """
    Args:
        code: The sentence.
        field: The request field it is about.
        **params: The values the sentence takes.

    Returns:
        The error to raise, carrying the sentence for the console to translate.
    """
    return ValidationError.from_text(say(code, **params), field=field)


def _damaged(kind: str) -> ValidationError:
    return _refuse("sign_in_image.damaged", kind=kind)


def _measure(kind: str, width: int, height: int) -> None:
    """
    Hold a size to the limits.

    Args:
        kind: PNG, JPEG or WebP, for the message.
        width: What the file's header says.
        height: What the file's header says.

    Raises:
        ValidationError: A side is zero (a picture of nothing), or past :data:`MAX_SIDE`.
    """
    if width < 1 or height < 1:
        raise _damaged(kind)
    if width > MAX_SIDE or height > MAX_SIDE:
        raise _refuse("sign_in_image.too_many_pixels", width=width, height=height, limit=MAX_SIDE)


def _clean_png(data: bytes) -> tuple[bytes, int, int]:
    """
    Read a PNG's chunks, check them and write it again without what is not pixels.

    Args:
        data: The file, signature included.

    Returns:
        The file as it is kept, and its width and height.

    Raises:
        ValidationError: A chunk that runs past the file or whose checksum fails, a header that is
            not first or says what the format does not have, a critical chunk this does not know,
            pixels that are missing or in pieces, or no end.
    """
    chunks: list[tuple[bytes, bytes]] = []
    offset = len(_PNG_SIGNATURE)
    while True:
        if offset + 12 > len(data):
            raise _damaged("PNG")
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        end = offset + 12 + length
        if length > 0x7FFFFFFF or end > len(data):
            raise _damaged("PNG")
        kind = data[offset + 4 : offset + 8]
        (checksum,) = struct.unpack(">I", data[end - 4 : end])
        if not kind.isalpha() or zlib.crc32(data[offset + 4 : end - 4]) != checksum:
            raise _damaged("PNG")
        chunks.append((kind, data[offset + 8 : end - 4]))
        offset = end
        if kind == b"IEND":
            break
    if chunks[-1][1] or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise _damaged("PNG")
    width, height, depth, colour, method, filtering, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1]
    )
    if (
        depth not in _PNG_DEPTHS.get(colour, ())
        or (method, filtering) != (0, 0)
        or interlace not in (0, 1)
    ):
        raise _damaged("PNG")
    _measure("PNG", width, height)

    kept = [chunks[0]]
    seen: set[bytes] = set()
    pixels = 0
    ended = False
    for kind, body in chunks[1:-1]:
        if kind == b"IDAT":
            if ended:
                raise _damaged("PNG")  # the pixels come in one run
            pixels += 1
            kept.append((kind, body))
            continue
        ended = pixels > 0
        if kind == b"IHDR":
            raise _damaged("PNG")
        if kind == b"PLTE":
            if pixels or b"PLTE" in seen or colour in (0, 4) or not body or len(body) % 3:
                raise _damaged("PNG")
            seen.add(kind)
            kept.append((kind, body))
        elif kind in _PNG_KEPT:
            if not pixels and kind not in seen and not (kind == b"tRNS" and colour in (4, 6)):
                seen.add(kind)
                kept.append((kind, body))
        elif kind[0] & 0x20 == 0:
            # An uppercase first letter is a chunk a decoder must understand to draw the picture:
            # one this does not know is not one it can vouch for. Every lowercase one (text, EXIF,
            # time, animation control, whatever else) is dropped.
            raise _damaged("PNG")
    if pixels == 0 or (colour == 3 and b"PLTE" not in seen):
        raise _damaged("PNG")
    kept.append(chunks[-1])
    text = _PNG_SIGNATURE + b"".join(
        struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        for kind, body in kept
    )
    return text, width, height


def _clean_jpeg(data: bytes) -> tuple[bytes, int, int]:
    """
    Walk a JPEG's segments, check them and write it again without what is not pixels.

    Args:
        data: The file, starting with the start-of-image marker.

    Returns:
        The file as it is kept, and its width and height.

    Raises:
        ValidationError: A segment that runs past the file or is too short to be one, a frame that
            is missing, doubled or is not one a browser draws, a scan that never ends, or no end of
            image.
    """
    out = [b"\xff\xd8"]
    frame: tuple[int, int] | None = None
    scans = 0
    pos = 2
    while True:
        if pos >= len(data) or data[pos] != 0xFF:
            raise _damaged("JPEG")
        while pos < len(data) and data[pos] == 0xFF:  # fill bytes may come before a marker
            pos += 1
        if pos >= len(data):
            raise _damaged("JPEG")
        marker = data[pos]
        pos += 1
        if marker == 0xD9:
            break
        if marker in (0x00, 0x01, 0xD8) or 0xD0 <= marker <= 0xD7 or pos + 2 > len(data):
            raise _damaged("JPEG")
        (length,) = struct.unpack(">H", data[pos : pos + 2])
        end = pos + length
        if length < 2 or end > len(data):
            raise _damaged("JPEG")
        body = data[pos + 2 : end]
        whole = bytes([0xFF, marker]) + data[pos:end]
        pos = end

        if marker in _JPEG_ANY_FRAME:
            if marker not in _JPEG_FRAMES:
                raise _refuse("sign_in_image.unsupported", kind="JPEG")
            if frame is not None or len(body) < 6:
                raise _damaged("JPEG")
            precision, height, width, components = struct.unpack(">BHHB", body[:6])
            if len(body) != 6 + 3 * components or not 1 <= components <= 4:
                raise _damaged("JPEG")
            if precision != 8:
                raise _refuse("sign_in_image.unsupported", kind="JPEG")
            _measure("JPEG", width, height)
            frame = (width, height)
            out.append(whole)
        elif marker == 0xDA:
            if frame is None:
                raise _damaged("JPEG")
            out.append(whole)
            start = pos
            while True:
                found = data.find(b"\xff", pos)
                if found == -1 or found + 1 >= len(data):
                    raise _damaged("JPEG")  # a scan that never reaches the next marker
                following = data[found + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7:
                    pos = found + 2  # a stuffed byte, a restart: still the scan's data
                elif following == 0xFF:
                    pos = found + 1
                else:
                    break
            out.append(data[start:found])
            pos = found
            scans += 1
        elif marker == 0xE0:
            # The JFIF header says the density; the thumbnail it may carry is a second picture of
            # whatever the first was before it was cropped, so it is left out.
            if body[:5] == b"JFIF\x00" and len(body) >= 14:
                out.append(b"\xff\xe0" + struct.pack(">H", 16) + body[:12] + b"\x00\x00")
        elif 0xE1 <= marker <= 0xEF or marker == 0xFE:
            continue  # EXIF, XMP, profiles, editors' notes, comments
        else:
            out.append(whole)  # tables and the restart interval
    if frame is None or scans == 0:
        raise _damaged("JPEG")
    out.append(b"\xff\xd9")
    return b"".join(out), frame[0], frame[1]


def _webp_size(kind: bytes, body: bytes) -> tuple[int, int]:
    """
    Read the size a WebP bitstream says it is.

    Args:
        kind: ``VP8 `` (lossy) or ``VP8L`` (lossless).
        body: The chunk's data.

    Returns:
        Width and height.

    Raises:
        ValidationError: A stream without the signature a bitstream starts with.
    """
    if kind == b"VP8 ":
        if len(body) < 10 or body[0] & 1 or body[3:6] != b"\x9d\x01\x2a":
            raise _damaged("WebP")
        width, height = struct.unpack("<HH", body[6:10])
        return width & 0x3FFF, height & 0x3FFF
    if len(body) < 5 or body[0] != 0x2F:
        raise _damaged("WebP")
    (bits,) = struct.unpack("<I", body[1:5])
    if bits >> 29:
        raise _damaged("WebP")
    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1


def _riff_chunk(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")


def _clean_webp(data: bytes) -> tuple[bytes, int, int]:
    """
    Walk a WebP's chunks, check them and write it again without what is not pixels.

    Args:
        data: The file, ``RIFF`` first.

    Returns:
        The file as it is kept, and its width and height.

    Raises:
        ValidationError: A chunk that runs past the file, no picture or more than one, a canvas
            that is not the picture's size, or an animation.
    """
    (declared,) = struct.unpack("<I", data[4:8]) if len(data) >= 8 else (0,)
    end = declared + 8
    if declared < 4 or end > len(data):
        raise _damaged("WebP")
    chunks: list[tuple[bytes, bytes]] = []
    pos = 12
    while pos < end:
        if pos + 8 > end:
            raise _damaged("WebP")
        kind = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        stop = pos + 8 + size
        if stop + (size & 1) > end:
            raise _damaged("WebP")
        chunks.append((kind, data[pos + 8 : stop]))
        pos = stop + (size & 1)
    kinds = [kind for kind, _ in chunks]
    if b"ANIM" in kinds or b"ANMF" in kinds:
        raise _refuse("sign_in_image.animated")
    images = [chunk for chunk in chunks if chunk[0] in (b"VP8 ", b"VP8L")]
    if len(images) != 1:
        raise _damaged("WebP")
    image_kind, image_body = images[0]
    width, height = _webp_size(image_kind, image_body)
    _measure("WebP", width, height)

    if chunks[0][0] != b"VP8X":
        if chunks[0] != images[0]:
            raise _damaged("WebP")
        parts = [_riff_chunk(image_kind, image_body)]
    else:
        header = chunks[0][1]
        if len(header) != 10:
            raise _damaged("WebP")
        if header[0] & 0x02:
            raise _refuse("sign_in_image.animated")
        canvas = (
            int.from_bytes(header[4:7], "little") + 1,
            int.from_bytes(header[7:10], "little") + 1,
        )
        if canvas != (width, height):
            raise _damaged("WebP")
        profile = next((body for kind, body in chunks if kind == b"ICCP"), None)
        alpha = next((body for kind, body in chunks if kind == b"ALPH"), None)
        flags = (header[0] & 0x10) | (0x20 if profile is not None else 0)
        parts = [_riff_chunk(b"VP8X", bytes([flags, 0, 0, 0]) + header[4:10])]
        if profile is not None:
            parts.append(_riff_chunk(b"ICCP", profile))
        if alpha is not None and image_kind == b"VP8 ":
            parts.append(_riff_chunk(b"ALPH", alpha))
        parts.append(_riff_chunk(image_kind, image_body))
    inside = b"WEBP" + b"".join(parts)
    return b"RIFF" + struct.pack("<I", len(inside)) + inside, width, height


def check_image(data: object) -> CleanImage:
    """
    Judge a picture by what it is, and rewrite it to carry pixels and nothing else.

    Args:
        data: The file's bytes.

    Returns:
        The picture as it is to be kept, with its type, size and digest.

    Raises:
        ValidationError: It is larger than the limit; it is an SVG; it is not a PNG, a JPEG or a
            WebP; it is damaged or incomplete; it is an animation; a side is past the limit.
            Each is a sentence of the catalog (``sign_in_image.*``), with the field ``image``.
    """
    if not isinstance(data, bytes | bytearray):
        raise _refuse("sign_in_image.not_an_image")
    raw = bytes(data)
    # Before anything is read of it: a file too large is not looked into.
    if len(raw) > MAX_IMAGE_BYTES:
        raise _refuse("sign_in_image.too_large", limit="1 MB")
    if raw.startswith(_PNG_SIGNATURE):
        kind, (clean, width, height) = "png", _clean_png(raw)
    elif raw.startswith(b"\xff\xd8\xff"):
        kind, (clean, width, height) = "jpeg", _clean_jpeg(raw)
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        kind, (clean, width, height) = "webp", _clean_webp(raw)
    elif raw[:4096].lstrip(_TEXT_LEAD).startswith(b"<") and b"<svg" in raw[:4096].lower():
        raise _refuse("sign_in_image.svg_refused")
    else:
        raise _refuse("sign_in_image.not_an_image")
    return CleanImage(
        data=clean,
        content_type=_CONTENT_TYPES[kind],
        width=width,
        height=height,
        sha256=hashlib.sha256(clean).hexdigest(),
    )


def decode_upload(text: object) -> bytes:
    """
    Read the base64 a picture is sent as.

    Args:
        text: What the request's field holds.

    Returns:
        The bytes. They are not judged: :func:`check_image` does that.

    Raises:
        ValidationError: It is not text, it is not base64 (a data address, a space or a line
            break is not), or it is longer than any file of the limit could be.
    """
    if not isinstance(text, str):
        raise _refuse("sign_in_image.not_base64")
    # By length first: a body is read with a cap, and this is what is left of it to refuse.
    if len(text) > MAX_UPLOAD_CHARS:
        raise _refuse("sign_in_image.too_large", limit="1 MB")
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise _refuse("sign_in_image.not_base64") from exc


def check_fit(value: object) -> str:
    """
    Judge how a picture is drawn in its place.

    Args:
        value: What was given.

    Returns:
        ``cover`` or ``contain``.

    Raises:
        ValidationError: It is any other thing.
    """
    if not isinstance(value, str) or value not in FITS:
        raise _refuse("sign_in_image.bad_fit", field="fit")
    return value


def check_alt(value: object) -> str:
    """
    Judge the description of a picture, which a screen reader says in its place.

    Args:
        value: What was given; None or empty says the picture is decoration.

    Returns:
        The text without its edges, up to :data:`MAX_ALT_CHARS` characters; empty for none.

    Raises:
        ValidationError: It is not text, it holds a line break, a control character or one that
            reorders text, or it is too long.
    """
    if value is None:
        return ""
    if not isinstance(value, str) or _CONTROL.search(value):
        raise _refuse("sign_in_image.alt_control", field="alt")
    text = value.strip()
    if len(text) > MAX_ALT_CHARS:
        raise _refuse("sign_in_image.alt_too_long", field="alt", limit=MAX_ALT_CHARS)
    return text
