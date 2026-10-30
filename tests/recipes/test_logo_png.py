# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A logo that exists only as a picture is a PNG of 256 pixels or more, stripped of everything but pixels."""

from __future__ import annotations

import struct
import zlib
from typing import Any

import pytest

from noust.recipes import RecipeError
from noust.recipes.logo_png import MAX_PNG_BYTES, MAX_SIDE, MIN_SIDE, sanitize_png, validate_png

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind: bytes, body: bytes, *, crc: int | None = None) -> bytes:
    checksum = zlib.crc32(kind + body) if crc is None else crc
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", checksum)


def header(
    width: int = 256, height: int = 256, depth: int = 8, colour: int = 6, interlace: int = 0
) -> bytes:
    return chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, interlace))


def pixels(width: int = 256, height: int = 256, channels: int = 4) -> bytes:
    """A flat picture: one filter byte per row, then the row, compressed."""
    row = b"\x00" + bytes([40, 90, 160, 255][:channels]) * width
    return chunk(b"IDAT", zlib.compress(row * height, 9))


def png(*parts: bytes, width: int = 256, height: int = 256) -> bytes:
    return (
        SIGNATURE
        + header(width, height)
        + b"".join(parts)
        + pixels(width, height)
        + chunk(b"IEND", b"")
    )


class TestWhatIsAccepted:
    def test_a_plain_png_of_two_hundred_and_fifty_six_pixels_is_accepted_as_it_is(self) -> None:
        data = png()

        assert validate_png("demo", data) == data
        assert MIN_SIDE == 256 and MAX_SIDE == 1024

    def test_a_palette_png_needs_its_palette_and_may_have_its_transparency(self) -> None:
        palette = chunk(b"PLTE", bytes([10, 20, 30, 200, 100, 50]))
        transparency = chunk(b"tRNS", b"\x00\xff")
        body = b"\x00" + b"\x01" * 256
        data = (
            SIGNATURE
            + header(colour=3, depth=8)
            + palette
            + transparency
            + chunk(b"IDAT", zlib.compress(body * 256))
            + chunk(b"IEND", b"")
        )

        assert validate_png("demo", data) == data

    def test_the_colour_space_the_picture_declares_may_stay(self) -> None:
        data = png(chunk(b"sRGB", b"\x00"))

        assert validate_png("demo", data) == data

    def test_a_picture_is_not_larger_than_sixty_four_kib(self) -> None:
        assert MAX_PNG_BYTES == 64 * 1024


class TestWhatIsRefused:
    @pytest.mark.parametrize(
        ("data", "message"),
        [
            (b"", "not a PNG"),
            (b"GIF89a" + b"\x00" * 40, "not a PNG"),
            (SIGNATURE, "IHDR"),
            (SIGNATURE + header()[:10], "truncated"),
            (SIGNATURE + pixels() + header() + chunk(b"IEND", b""), "IHDR"),
            (SIGNATURE + header() + header() + pixels() + chunk(b"IEND", b""), "IHDR"),
            (SIGNATURE + header() + chunk(b"IEND", b""), "IDAT"),
            (SIGNATURE + header() + pixels(), "IEND"),
            (png() + b"trailing", "after IEND"),
            (SIGNATURE + header(width=255) + pixels(255, 256) + chunk(b"IEND", b""), "256"),
            (SIGNATURE + header(height=100) + pixels(256, 100) + chunk(b"IEND", b""), "256"),
            (
                SIGNATURE
                + header(width=1025, height=1025)
                + pixels(1025, 1025)
                + chunk(b"IEND", b""),
                "1024",
            ),
            (SIGNATURE + header(width=0) + pixels(0, 256) + chunk(b"IEND", b""), "256"),
            (SIGNATURE + header(depth=3) + pixels() + chunk(b"IEND", b""), "depth"),
            (SIGNATURE + header(colour=1) + pixels() + chunk(b"IEND", b""), "colour type"),
            (SIGNATURE + header(colour=6, depth=4) + pixels() + chunk(b"IEND", b""), "depth"),
            (SIGNATURE + header(interlace=2) + pixels() + chunk(b"IEND", b""), "interlace"),
            (SIGNATURE + header(colour=3) + pixels() + chunk(b"IEND", b""), "PLTE"),
            (
                SIGNATURE
                + header(colour=3)
                + chunk(b"PLTE", b"\x00\x01")
                + pixels()
                + chunk(b"IEND", b""),
                "PLTE",
            ),
        ],
    )
    def test_a_file_that_is_not_a_plain_picture_of_the_right_size_is_refused(
        self, data: bytes, message: str
    ) -> None:
        with pytest.raises(RecipeError, match=message):
            validate_png("demo", data)

    def test_a_checksum_that_does_not_hold_is_refused(self) -> None:
        bad = (
            SIGNATURE
            + header()
            + chunk(b"IDAT", zlib.compress(b"\x00" * 1025 * 256), crc=1)
            + chunk(b"IEND", b"")
        )

        with pytest.raises(RecipeError, match="checksum"):
            validate_png("demo", bad)

    @pytest.mark.parametrize(
        "kind",
        [
            b"tEXt",
            b"zTXt",
            b"iTXt",
            b"eXIf",
            b"iCCP",
            b"pHYs",
            b"tIME",
            b"acTL",
            b"fcTL",
            b"fdAT",
            b"prVt",
            b"XXXX",
            b"IHDR",
        ],
    )
    def test_a_chunk_that_carries_anything_but_pixels_is_refused(self, kind: bytes) -> None:
        with pytest.raises(RecipeError, match="chunk"):
            validate_png("demo", png(chunk(kind, b"Comment\x00<script>alert(1)</script>")))

    def test_a_length_that_runs_past_the_file_or_past_what_a_chunk_may_be_is_refused(self) -> None:
        huge = SIGNATURE + header() + struct.pack(">I", 2**31) + b"IDAT" + b"\x00" * 8
        past = SIGNATURE + header() + struct.pack(">I", 5000) + b"IDAT" + b"\x00" * 8

        for data in (huge, past):
            with pytest.raises(RecipeError, match=r"truncated|chunk"):
                validate_png("demo", data)

    def test_a_picture_larger_than_sixty_four_kib_is_refused(self) -> None:
        noise = b"".join(chunk(b"IDAT", bytes(range(256)) * 64) for _ in range(5))
        data = SIGNATURE + header() + noise + chunk(b"IEND", b"")

        with pytest.raises(RecipeError, match="64 KiB"):
            validate_png("demo", data)


class TestTheRewriteThatStripsAPicture:
    def test_text_metadata_a_profile_and_what_a_viewer_never_needs_are_dropped(self) -> None:
        dirty = png(
            chunk(b"tEXt", b"Comment\x00<script>alert(1)</script>"),
            chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00<x:xmpmeta/>"),
            chunk(b"eXIf", b"MM\x00*"),
            chunk(b"iCCP", b"p\x00\x00" + zlib.compress(b"profile")),
            chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
            chunk(b"tIME", struct.pack(">HBBBBB", 2026, 10, 29, 1, 2, 3)),
            chunk(b"sRGB", b"\x00"),
            chunk(b"vpAg", b"\x00" * 9),
        )
        clean = sanitize_png("demo", dirty)

        assert validate_png("demo", clean) == clean
        for gone in (
            b"tEXt",
            b"iTXt",
            b"eXIf",
            b"iCCP",
            b"pHYs",
            b"tIME",
            b"vpAg",
            b"script",
            b"xmpmeta",
        ):
            assert gone not in clean
        assert b"sRGB" in clean and b"IDAT" in clean and len(clean) < len(dirty)

    def test_the_pixels_are_not_touched(self) -> None:
        dirty = png(chunk(b"tEXt", b"a\x00b"))
        clean = sanitize_png("demo", dirty)

        def idat(data: bytes) -> bytes:
            out, offset = b"", 8
            while offset < len(data):
                (length,) = struct.unpack(">I", data[offset : offset + 4])
                if data[offset + 4 : offset + 8] == b"IDAT":
                    out += data[offset + 8 : offset + 8 + length]
                offset += 12 + length
            return out

        assert idat(clean) == idat(dirty)

    def test_a_checksum_that_is_wrong_in_a_chunk_that_is_dropped_does_not_matter_but_a_wrong_one_in_pixels_does(
        self,
    ) -> None:
        broken_text = png(chunk(b"tEXt", b"a\x00b", crc=7))
        assert validate_png("demo", sanitize_png("demo", broken_text))

        broken_pixels = (
            SIGNATURE
            + header()
            + chunk(b"IDAT", zlib.compress(b"\x00" * 1025 * 256), crc=1)
            + chunk(b"IEND", b"")
        )
        with pytest.raises(RecipeError, match="checksum"):
            sanitize_png("demo", broken_pixels)

    def test_an_animated_picture_is_refused_not_stripped_to_its_first_frame(self) -> None:
        animated = png(chunk(b"acTL", struct.pack(">II", 2, 0)))

        with pytest.raises(RecipeError, match="animated"):
            sanitize_png("demo", animated)

    def test_a_file_that_is_not_a_png_is_refused_whole(self) -> None:
        for data in (b"", b"<svg/>", SIGNATURE, SIGNATURE + b"\x00" * 100):
            with pytest.raises(RecipeError):
                sanitize_png("demo", data)


def test_a_logo_of_a_recipe_may_be_a_png(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Through the schema: ``logo: logo.png`` is read, judged and credited like an SVG."""
    from noust.recipes import assets, parse_recipe
    from tests.recipes.factory import compose_recipe

    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "logo.png").write_bytes(png())
    (folder / "LOGO-NOTICE").write_text(
        "Source: https://github.com/example/demo/blob/abc/logo.png\nLicence: MIT\nRetrieved: 2026-10-29\n"
    )
    real = assets.asset_resource
    monkeypatch.setattr(
        assets, "asset_resource", lambda n, a: folder.joinpath(a) if n == "demo" else real(n, a)
    )

    recipe = parse_recipe("demo", compose_recipe(logo="logo.png"))
    summary: dict[str, Any] = recipe.summary()

    assert summary["logo_source"] == "asset" and summary["logo_license"] == "MIT"

    (folder / "logo.png").write_bytes(png(chunk(b"tEXt", b"a\x00b")))
    with pytest.raises(RecipeError, match="chunk"):
        parse_recipe("demo", compose_recipe(logo="logo.png"))

    (folder / "logo.gif").write_bytes(b"GIF89a")
    with pytest.raises(RecipeError, match=r"\.svg or a \.png"):
        parse_recipe("demo", compose_recipe(logo="logo.gif"))
