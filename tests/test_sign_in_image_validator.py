# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What may be the picture on the sign-in screen (``noust.validators.sign_in_image``).

The file is served to anyone who opens the console, with no session, so it is judged by what it
is and rewritten to carry nothing but pixels: the type comes from its bytes, never from a name or a
header the sender chose; the size and the dimensions come from its own headers and are held to the
limits; and what a camera or an editor left in it (a place, a name, a thumbnail, a comment) is
taken out before anything is stored. ``tests/test_adversarial_inputs.py`` attacks the same
functions with damaged and hostile files.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from noust.core.exceptions import ValidationError
from noust.validators import sign_in_image as rules
from tests.support import sign_in_images as make


def refusal(call, *args) -> ValidationError:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError) as caught:
        call(*args)
    return caught.value


def code_of(error: ValidationError) -> str:
    assert error.coded is not None, f"{error.message!r} has no code"
    return error.coded.code


def pixel_data(data: bytes, kind: bytes = b"IDAT") -> bytes:
    """The bytes of every chunk of this kind of a PNG, joined."""
    found, offset = b"", len(make.PNG_SIGNATURE)
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        if data[offset + 4 : offset + 8] == kind:
            found += data[offset + 8 : offset + 8 + length]
        offset += 12 + length
    return found


class TestWhatIsAcceptedAndWhatItBecomes:
    def test_a_png_is_known_by_its_bytes_and_measured_from_its_header(self) -> None:
        image = rules.check_image(make.png(300, 120))

        assert (image.content_type, image.width, image.height) == ("image/png", 300, 120)
        assert image.data.startswith(make.PNG_SIGNATURE)

    def test_a_jpeg_is_measured_from_its_frame_header(self) -> None:
        image = rules.check_image(make.jpeg(640, 480))

        assert (image.content_type, image.width, image.height) == ("image/jpeg", 640, 480)
        assert image.data.startswith(b"\xff\xd8") and image.data.endswith(b"\xff\xd9")

    @pytest.mark.parametrize("lossless", [False, True])
    def test_a_simple_webp_is_measured_from_its_bitstream(self, lossless: bool) -> None:
        image = rules.check_image(make.webp(321, 123, lossless=lossless))

        assert (image.content_type, image.width, image.height) == ("image/webp", 321, 123)

    def test_an_extended_webp_is_measured_and_its_canvas_must_agree_with_it(self) -> None:
        image = rules.check_image(make.webp_extended(50, 40))

        assert (image.width, image.height) == (50, 40)

    def test_the_hash_is_that_of_what_is_kept_so_a_clean_picture_has_one_address(self) -> None:
        dirty = make.png(extra=(make.png_chunk(b"tEXt", b"Author\x00Someone"),))
        clean = make.png()

        assert rules.check_image(dirty).sha256 == rules.check_image(clean).sha256
        kept = rules.check_image(dirty)
        assert kept.sha256 == hashlib.sha256(kept.data).hexdigest()

    @pytest.mark.parametrize(
        "original",
        [
            make.png(extra=(make.png_chunk(b"eXIf", b"GPS"),)),
            make.jpeg(before_frame=(make.jpeg_segment(0xE1, b"Exif\x00\x00GPS"),)),
            make.webp_extended(flags=0x08, after=(make.riff_chunk(b"EXIF", b"GPS"),)),
        ],
    )
    def test_stripping_is_idempotent(self, original: bytes) -> None:
        once = rules.check_image(original)

        assert rules.check_image(once.data).data == once.data


class TestTheTypeIsWhatTheBytesAre:
    @pytest.mark.parametrize(
        "name, data",
        [
            ("empty", b""),
            ("text", b"hello"),
            ("a png signature and nothing else", make.PNG_SIGNATURE),
            ("a jpeg signature and nothing else", b"\xff\xd8\xff"),
            ("a riff file that is not a webp", b"RIFF\x04\x00\x00\x00WAVE"),
            ("a gif", b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"),
            ("html", b"<html><script>alert(1)</script></html>"),
            ("a zip", b"PK\x03\x04" + b"\x00" * 40),
        ],
    )
    def test_what_is_not_a_picture_of_the_three_types_is_refused(
        self, name: str, data: bytes
    ) -> None:
        assert code_of(refusal(rules.check_image, data)) in {
            "sign_in_image.not_an_image",
            "sign_in_image.damaged",
        }, name

    @pytest.mark.parametrize(
        "text",
        [
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>',
            b"\xef\xbb\xbf  \n<svg/>",
            b"<!-- c --><SVG onload=alert(1)>",
        ],
    )
    def test_an_svg_is_refused_by_name_whatever_it_was_called(self, text: bytes) -> None:
        # The upload's own name or type is never an input; an SVG called logo.png is still an SVG.
        assert code_of(refusal(rules.check_image, text)) == "sign_in_image.svg_refused"

    def test_a_png_that_hides_an_svg_after_its_end_keeps_only_the_png(self) -> None:
        image = rules.check_image(make.png(trailer=b"<svg onload=alert(1)>"))

        assert b"<svg" not in image.data and image.data.endswith(make.png_chunk(b"IEND"))


class TestTheLimits:
    def test_a_file_of_exactly_one_mebibyte_is_accepted_and_one_byte_more_is_not(self) -> None:
        limit = rules.MAX_IMAGE_BYTES
        base = make.png()
        padding = limit - len(base) - 12
        exact = make.png(extra=(make.png_chunk(b"IDAT", b"\x00" * padding),))
        assert len(exact) == limit
        assert rules.check_image(exact).width == 8

        over = exact + b"\x00"
        error = refusal(rules.check_image, over)
        assert code_of(error) == "sign_in_image.too_large"

    def test_the_size_is_judged_before_anything_is_read_of_the_file(self) -> None:
        # Nothing of a file that is too large is parsed: the first byte could be anything.
        assert code_of(refusal(rules.check_image, b"\x00" * (rules.MAX_IMAGE_BYTES + 1))) == (
            "sign_in_image.too_large"
        )

    @pytest.mark.parametrize(
        "builder",
        [
            lambda w, h: make.png(w, h),
            lambda w, h: make.jpeg(w, h),
            lambda w, h: make.webp(w, h),
            lambda w, h: make.webp(w, h, lossless=True),
        ],
    )
    def test_four_thousand_and_ninety_six_pixels_a_side_is_the_most(self, builder) -> None:  # type: ignore[no-untyped-def]
        assert rules.check_image(builder(4096, 4096)).width == 4096
        for width, height in ((4097, 10), (10, 4097)):
            error = refusal(rules.check_image, builder(width, height))
            assert code_of(error) == "sign_in_image.too_many_pixels"
            assert error.coded is not None
            assert error.coded.params["limit"] == 4096

    def test_a_frame_header_that_lies_about_its_size_is_not_believed(self) -> None:
        # 65535 by 65535: a decoder would allocate gigabytes for a file of a few hundred bytes.
        error = refusal(rules.check_image, make.jpeg(65535, 65535))

        assert code_of(error) == "sign_in_image.too_many_pixels"

    def test_a_size_of_zero_is_not_a_picture(self) -> None:
        assert code_of(refusal(rules.check_image, make.jpeg(0, 10))) == "sign_in_image.damaged"
        assert code_of(refusal(rules.check_image, make.png(0, 10))) == "sign_in_image.damaged"


class TestAPng:
    def test_what_says_who_made_it_or_where_is_taken_out_and_the_pixels_are_not_touched(
        self,
    ) -> None:
        original = make.png(
            extra=(
                make.png_chunk(b"tEXt", b"Author\x00Someone"),
                make.png_chunk(b"zTXt", b"Comment\x00\x00xx"),
                make.png_chunk(b"iTXt", b"Title\x00\x00\x00\x00\x00<svg onload=x>"),
                make.png_chunk(b"eXIf", b"MM\x00*GPS"),
                make.png_chunk(b"tIME", b"\x07\xea\x0b\x05\x0c\x00\x00"),
                make.png_chunk(b"zzZz", b"an ancillary chunk nobody knows"),
            ),
        )

        kept = rules.check_image(original).data

        assert make.png_kinds(kept) == [b"IHDR", b"IDAT", b"IEND"]
        assert pixel_data(kept) == pixel_data(original)
        for leaked in (b"Someone", b"GPS", b"<svg", b"nobody"):
            assert leaked not in kept

    def test_what_says_how_to_read_the_pixels_stays(self) -> None:
        original = make.png(
            colour_type=3,
            after_header=(
                make.png_chunk(b"gAMA", b"\x00\x01\x86\xa0"),
                make.png_chunk(b"sRGB", b"\x00"),
                make.png_chunk(b"pHYs", b"\x00\x00\x0b\x13\x00\x00\x0b\x13\x01"),
            ),
            extra=(make.png_chunk(b"tRNS", b"\x80"),),
        )

        assert make.png_kinds(rules.check_image(original).data) == [
            b"IHDR",
            b"gAMA",
            b"sRGB",
            b"pHYs",
            b"PLTE",
            b"tRNS",
            b"IDAT",
            b"IEND",
        ]

    def test_an_animation_becomes_its_first_frame(self) -> None:
        original = make.png(
            after_header=(make.png_chunk(b"acTL", b"\x00\x00\x00\x02\x00\x00\x00\x00"),),
            extra=(),
            trailer=b"",
        )
        animated = original.replace(
            make.png_chunk(b"IEND"),
            make.png_chunk(b"fcTL", b"\x00" * 26)
            + make.png_chunk(b"fdAT", b"\x00" * 8)
            + make.png_chunk(b"IEND"),
        )

        assert make.png_kinds(rules.check_image(animated).data) == [b"IHDR", b"IDAT", b"IEND"]

    @pytest.mark.parametrize(
        "name, data, code",
        [
            (
                "an unknown critical chunk",
                make.png(extra=(make.png_chunk(b"ZZZZ", b"x"),)),
                "damaged",
            ),
            (
                "a checksum that does not hold",
                make.png(extra=(make.png_chunk(b"gAMA", b"\x00\x01\x86\xa0", crc=1),)),
                "damaged",
            ),
            ("no end", make.png(end=False), "damaged"),
            (
                "a header of the wrong length",
                make.PNG_SIGNATURE
                + make.png_chunk(b"IHDR", b"\x00" * 12)
                + make.png_chunk(b"IEND"),
                "damaged",
            ),
            (
                "a colour type the format does not have",
                make.png(colour_type=2).replace(b"\x08\x02\x00\x00\x00", b"\x08\x05\x00\x00\x00"),
                "damaged",
            ),
            (
                "no pixels",
                make.PNG_SIGNATURE + make.png()[8:33] + make.png_chunk(b"IEND"),
                "damaged",
            ),
            ("a chunk that runs past the file", make.png()[:-20], "damaged"),
        ],
    )
    def test_a_damaged_or_odd_png_is_refused(self, name: str, data: bytes, code: str) -> None:
        # The header's own checksum is wrong in the cases that edit it: refused as damaged as well.
        assert code_of(refusal(rules.check_image, data)) == f"sign_in_image.{code}", name

    @pytest.mark.parametrize("colour, depth", [(5, 8), (2, 4), (3, 16), (0, 3), (6, 1), (7, 8)])
    def test_a_header_that_names_a_colour_type_or_depth_the_format_lacks_is_refused(
        self, colour: int, depth: int
    ) -> None:
        import struct

        header = make.png_chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, depth, colour, 0, 0, 0))
        data = (
            make.PNG_SIGNATURE
            + header
            + make.png_chunk(b"IDAT", b"\x00" * 8)
            + make.png_chunk(b"IEND")
        )

        assert code_of(refusal(rules.check_image, data)) == "sign_in_image.damaged"

    def test_pixels_in_two_runs_are_refused(self) -> None:
        data = make.png(
            extra=(make.png_chunk(b"IDAT", b"\x00" * 4), make.png_chunk(b"gAMA", b"\x00" * 4))
        )

        assert code_of(refusal(rules.check_image, data)) == "sign_in_image.damaged"

    def test_a_header_that_is_not_first_is_refused(self) -> None:
        data = make.PNG_SIGNATURE + make.png_chunk(b"gAMA", b"\x00\x01\x86\xa0") + make.png()[8:]

        assert code_of(refusal(rules.check_image, data)) == "sign_in_image.damaged"


class TestAJpeg:
    def test_what_a_camera_leaves_is_taken_out_and_the_scan_is_not_touched(self) -> None:
        scan = make.jpeg()
        original = make.jpeg(
            before_frame=(
                make.jfif(),
                make.jpeg_segment(0xE1, b"Exif\x00\x00" + b"GPS 28.1,-15.4"),
                make.jpeg_segment(0xE2, b"ICC_PROFILE\x00\x01\x01 private profile name"),
                make.jpeg_segment(0xED, b"Photoshop 3.0\x00 a name"),
                make.jpeg_segment(0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x00"),
                make.jpeg_segment(0xFE, b"made with a tool"),
            ),
            after_frame=(make.jpeg_segment(0xFE, b"another comment"),),
        )

        kept = rules.check_image(original).data

        for leaked in (b"GPS", b"private", b"Photoshop", b"made with", b"another", b"Adobe"):
            assert leaked not in kept
        assert scan[scan.index(b"\xff\xda") :] == kept[kept.index(b"\xff\xda") :]

    def test_the_jfif_header_stays_without_the_thumbnail_it_may_carry(self) -> None:
        original = make.jpeg(before_frame=(make.jfif(thumbnail=b"SECRETTHUMB!"),))

        kept = rules.check_image(original).data

        assert b"JFIF" in kept and b"SECRETTHUMB" not in kept
        assert kept[2:4] == b"\xff\xe0"

    def test_a_jpeg_without_a_jfif_header_is_a_jpeg(self) -> None:
        assert rules.check_image(make.jpeg()).content_type == "image/jpeg"

    def test_a_progressive_jpeg_keeps_every_scan_and_loses_a_comment_between_them(self) -> None:
        first = make.jpeg(sof=0xC2, scans=1)
        original = (
            first[:-2]
            + make.jpeg_segment(0xFE, b"between the scans")
            + first[first.index(b"\xff\xda") : -2]
            + b"\xff\xd9"
        )

        kept = rules.check_image(original).data

        assert kept.count(b"\xff\xda") == 2 and b"between" not in kept

    def test_bytes_that_follow_the_end_of_the_image_are_dropped(self) -> None:
        kept = rules.check_image(make.jpeg(trailer=b"<svg onload=alert(1)>PK\x03\x04")).data

        assert kept.endswith(b"\xff\xd9") and b"<svg" not in kept and b"PK" not in kept

    @pytest.mark.parametrize(
        "name, data",
        [
            ("no frame header", make.jpeg().replace(b"\xff\xc0", b"\xff\xfe")),
            ("lossless", make.jpeg(sof=0xC3)),
            ("arithmetic coded", make.jpeg(sof=0xC9)),
            (
                "two frames",
                make.jpeg(
                    after_frame=(make.jpeg_segment(0xC0, b"\x08\x00\x08\x00\x08\x01\x01\x11\x00"),)
                ),
            ),
            ("no end", make.jpeg(end=False)),
            ("a segment longer than the file", make.jpeg()[:30]),
            ("a segment whose length is too short to be one", b"\xff\xd8\xff\xfe\x00\x01\xff\xd9"),
            (
                "a frame that ends before its components",
                b"\xff\xd8"
                + make.jpeg_segment(0xC0, b"\x08\x00\x06\x00\x08\x03\x01\x11\x00")
                + make.jpeg()[make.jpeg().index(b"\xff\xda") :],
            ),
            (
                "no scan",
                b"\xff\xd8"
                + make.jpeg_segment(0xC0, b"\x08\x00\x08\x00\x08\x01\x01\x11\x00")
                + b"\xff\xd9",
            ),
        ],
    )
    def test_a_jpeg_that_is_not_one_a_browser_draws_is_refused(
        self, name: str, data: bytes
    ) -> None:
        assert code_of(refusal(rules.check_image, data)) in {
            "sign_in_image.damaged",
            "sign_in_image.unsupported",
        }, name


class TestAWebp:
    def test_exif_and_xmp_are_taken_out_and_their_flags_with_them(self) -> None:
        original = make.webp_extended(
            flags=0x10 | 0x08 | 0x04,
            before=(make.riff_chunk(b"ALPH", b"\x00\x01\x02"),),
            after=(
                make.riff_chunk(b"EXIF", b"GPS 28.1,-15.4"),
                make.riff_chunk(b"XMP ", b"<x:xmpmeta/>"),
            ),
        )

        kept = rules.check_image(original).data

        assert b"GPS" not in kept and b"xmpmeta" not in kept and b"EXIF" not in kept
        assert kept[:4] == b"RIFF" and kept[8:12] == b"WEBP" and kept[12:16] == b"VP8X"
        assert kept[20] == 0x10  # alpha stays, the metadata flags do not
        assert int.from_bytes(kept[4:8], "little") == len(kept) - 8
        assert b"ALPH" in kept

    def test_the_colour_profile_stays(self) -> None:
        original = make.webp_extended(flags=0x20, before=(make.riff_chunk(b"ICCP", b"profile"),))

        kept = rules.check_image(original).data

        assert b"ICCP" in kept and kept[20] == 0x20

    def test_a_plain_webp_with_trailing_bytes_loses_them(self) -> None:
        kept = rules.check_image(make.webp() + b"<svg onload=alert(1)>").data

        assert b"<svg" not in kept and int.from_bytes(kept[4:8], "little") == len(kept) - 8

    @pytest.mark.parametrize(
        "name, data, code",
        [
            ("an animation by its flag", make.webp_extended(flags=0x02), "animated"),
            (
                "an animation by its frames",
                make.webp_extended(before=(make.riff_chunk(b"ANIM", b"\x00" * 6),)),
                "animated",
            ),
            (
                "a canvas that is not the picture",
                make.webp_extended(10, 10, canvas=(4000, 4000)),
                "damaged",
            ),
            ("no picture", make.riff(make.riff_chunk(b"EXIF", b"x")), "damaged"),
            (
                "a header and no picture",
                make.riff(make.riff_chunk(b"VP8X", b"\x00" * 10)),
                "damaged",
            ),
            (
                "a size that runs past the file",
                make.riff(make.riff_chunk(b"VP8 ", make.vp8_body(8, 8)), size=9999),
                "damaged",
            ),
            (
                "a lossy stream without its start code",
                make.riff(make.riff_chunk(b"VP8 ", b"\x30\x01\x00" + b"\x00" * 12)),
                "damaged",
            ),
            (
                "a lossless stream without its signature",
                make.riff(make.riff_chunk(b"VP8L", b"\x00" * 12)),
                "damaged",
            ),
            (
                "a chunk that runs past the end",
                make.riff(b"VP8 \xff\xff\x00\x00" + b"\x00" * 8),
                "damaged",
            ),
        ],
    )
    def test_a_webp_that_is_animated_or_broken_is_refused(
        self, name: str, data: bytes, code: str
    ) -> None:
        assert code_of(refusal(rules.check_image, data)) == f"sign_in_image.{code}", name


class TestHowItIsSent:
    def test_base64_comes_back_as_the_bytes_it_was(self) -> None:
        raw = make.png()

        assert rules.decode_upload(base64.b64encode(raw).decode()) == raw

    @pytest.mark.parametrize("sent", [None, 5, b"abc", ["a"], {"a": 1}, True])
    def test_what_is_not_text_is_refused(self, sent: object) -> None:
        assert code_of(refusal(rules.decode_upload, sent)) == "sign_in_image.not_base64"

    @pytest.mark.parametrize(
        "sent", ["***", "abc", "ab=c", "a b c d", "data:image/png;base64,AAAA", "AAAA\n"]
    )
    def test_text_that_is_not_base64_is_refused(self, sent: str) -> None:
        assert code_of(refusal(rules.decode_upload, sent)) == "sign_in_image.not_base64"

    def test_the_size_is_judged_on_the_text_before_it_is_decoded(self) -> None:
        # More characters than the limit could ever be written in: refused without decoding them.
        huge = "A" * (rules.MAX_UPLOAD_CHARS + 4)

        assert code_of(refusal(rules.decode_upload, huge)) == "sign_in_image.too_large"

    def test_the_longest_text_a_file_of_the_limit_makes_is_accepted(self) -> None:
        text = base64.b64encode(b"\x00" * rules.MAX_IMAGE_BYTES).decode()

        assert len(text) <= rules.MAX_UPLOAD_CHARS
        assert len(rules.decode_upload(text)) == rules.MAX_IMAGE_BYTES


class TestHowItIsFitted:
    @pytest.mark.parametrize("fit", ["cover", "contain"])
    def test_the_two_fits(self, fit: str) -> None:
        assert rules.check_fit(fit) == fit

    @pytest.mark.parametrize(
        "fit", ["", "fill", "COVER", "cover ", "contain\n", None, 1, "scale-down"]
    )
    def test_nothing_else_is_a_fit(self, fit: object) -> None:
        assert code_of(refusal(rules.check_fit, fit)) == "sign_in_image.bad_fit"


class TestTheDescription:
    def test_a_description_is_text_of_up_to_one_hundred_and_twenty_characters(self) -> None:
        assert rules.check_alt("Acme Corp, since 1987") == "Acme Corp, since 1987"
        assert rules.check_alt("é" * 120) == "é" * 120

    def test_empty_means_the_picture_is_decoration(self) -> None:
        assert rules.check_alt("") == ""
        assert rules.check_alt("   ") == ""
        assert rules.check_alt(None) == ""

    def test_the_edges_are_trimmed(self) -> None:
        assert rules.check_alt("  Acme  ") == "Acme"

    def test_one_more_than_the_limit_is_refused(self) -> None:
        error = refusal(rules.check_alt, "a" * 121)

        assert code_of(error) == "sign_in_image.alt_too_long"
        assert error.coded is not None and error.coded.params["limit"] == 120

    @pytest.mark.parametrize(
        "text",
        [
            "a\nb",
            "a\rb",
            "a\x00b",
            "a\x1bb",
            "a\u2028b",
            "a\u202eb",
            "a\u2066b",
            "a\ufeffb",
            "a\tb",
        ],
    )
    def test_line_breaks_control_characters_and_the_ones_that_reorder_text_are_refused(
        self, text: str
    ) -> None:
        assert code_of(refusal(rules.check_alt, text)) == "sign_in_image.alt_control"

    @pytest.mark.parametrize("text", [5, ["a"], b"a", True])
    def test_a_description_that_is_not_text_is_refused(self, text: object) -> None:
        assert code_of(refusal(rules.check_alt, text)) == "sign_in_image.alt_control"
