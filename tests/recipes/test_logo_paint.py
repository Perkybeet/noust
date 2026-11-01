# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A logo is shown on the console's light stone and on its dark stone, and reads on both."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from noust.recipes import RecipeError, logo_svg
from noust.recipes.logo_paint import (
    DARK_SURFACES,
    INK,
    LIGHT_SURFACES,
    MIN_CONTRAST,
    PAPER,
    adapt_to_scheme,
    contrast,
)
from noust.recipes.logos import validate_svg

HEAD = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" role="img"><title>X</title>'


def svg(body: str, head: str = HEAD) -> str:
    return head + body + "</svg>"


def fills(text: str) -> list[str]:
    """Every colour a shape of the result is filled with (its own or the one it inherits), in order."""
    root = ET.fromstring(text)  # noqa: S314 - the output of the code under test
    found: list[str] = []

    def walk(element: ET.Element, inherited: str) -> None:
        own = element.get("fill", inherited)
        if element.tag.endswith(("path", "circle", "rect")):
            found.append(own)
        for child in element:
            walk(child, own)

    walk(root, "")
    return found


class TestContrast:
    def test_black_on_white_is_twenty_one_and_a_colour_on_itself_is_one(self) -> None:
        assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
        assert contrast("#336699", "#336699") == pytest.approx(1.0)

    def test_the_two_stones_are_what_the_console_draws(self) -> None:
        """tokens.css: the page and the raised surface of each theme, the darkest and the lightest."""
        assert LIGHT_SURFACES == ("#d9ddd5", "#fbfcfa")
        assert DARK_SURFACES == ("#1b201e", "#323a36")
        for ink in (INK,):
            assert min(contrast(ink, surface) for surface in LIGHT_SURFACES) >= 7
        for paper in (PAPER,):
            assert min(contrast(paper, surface) for surface in DARK_SURFACES) >= 7


class TestOneColour:
    BLACK = svg('<path d="M0 0h24v24H0z"/>')

    def test_a_black_mark_stays_black_on_light_stone_and_is_paper_on_dark(self) -> None:
        assert adapt_to_scheme("demo", self.BLACK, "light") == self.BLACK
        assert fills(adapt_to_scheme("demo", self.BLACK, "dark")) == [PAPER]

    def test_a_mark_without_a_colour_takes_the_brands_when_it_reads(self) -> None:
        """The collection's marks are drawn without a fill, and the brand's colour is its own."""
        light = adapt_to_scheme("demo", self.BLACK, "light", brand="609926")
        dark = adapt_to_scheme("demo", self.BLACK, "dark", brand="609926")

        assert fills(light) == ["#609926"] and fills(dark) == ["#609926"]

    def test_a_brand_colour_that_does_not_read_gives_way_to_the_stone_s_own_ink(self) -> None:
        # Near black is the brand of many a mark, and nothing on dark stone.
        assert fills(adapt_to_scheme("demo", self.BLACK, "dark", brand="090B0B")) == [PAPER]
        assert fills(adapt_to_scheme("demo", self.BLACK, "light", brand="090B0B")) == ["#090b0b"]
        # A pale yellow is the opposite on light stone.
        assert fills(adapt_to_scheme("demo", self.BLACK, "light", brand="FFEC6E")) == [INK]
        assert fills(adapt_to_scheme("demo", self.BLACK, "dark", brand="FFEC6E")) == ["#ffec6e"]

    def test_a_dark_blue_in_the_file_is_judged_like_a_brand_colour(self) -> None:
        blue = svg('<path d="M0 0h9v9z" fill="#0117BE"/><circle r="3" fill="#0117be"/>')

        assert fills(adapt_to_scheme("demo", blue, "light")) == ["#0117BE", "#0117be"]
        assert fills(adapt_to_scheme("demo", blue, "dark")) == [PAPER, PAPER]

    def test_a_stroke_only_mark_is_painted_through_its_stroke(self) -> None:
        outline = svg('<path d="M0 0h9v9z" fill="none" stroke="#000"/>')
        out = adapt_to_scheme("demo", outline, "dark")

        assert f'stroke="{PAPER}"' in out and 'fill="none"' in out

    def test_a_fill_inherited_from_a_group_is_changed_where_it_is_set(self) -> None:
        grouped = svg('<g fill="#111"><path d="M0 0h9v9z"/><circle r="2"/></g>')
        out = adapt_to_scheme("demo", grouped, "dark")

        assert out.count(PAPER) == 1 and 'fill="#111"' not in out

    def test_the_result_still_passes_the_guard(self) -> None:
        validate_svg("demo", adapt_to_scheme("demo", self.BLACK, "dark", brand="123456"))


class TestSeveralColours:
    def test_a_coloured_mark_is_left_as_its_designer_drew_it(self) -> None:
        green_and_blue = svg(
            '<path d="M0 0h9v9z" fill="#16a34a"/><path d="M9 9h9v9z" fill="#2563eb"/>'
        )

        for scheme in ("light", "dark"):
            assert adapt_to_scheme("demo", green_and_blue, scheme) == green_and_blue

    def test_a_black_and_white_mark_is_inverted_on_the_scheme_that_would_lose_it(self) -> None:
        panda = svg(
            '<path d="M0 0h20v20z" fill="#000"/><circle r="3" fill="#fff"/><rect width="2" height="2" fill="#808080"/>'
        )

        assert adapt_to_scheme("demo", panda, "light") == panda
        dark = adapt_to_scheme("demo", panda, "dark")
        values = fills(dark)
        assert values[0] == PAPER and values[1] == INK
        assert values[2] not in {PAPER, INK}

    def test_a_white_and_grey_mark_is_inverted_on_light_stone(self) -> None:
        mark = svg('<path d="M0 0h20v20z" fill="#fff"/><circle r="3" fill="#ddd"/>')

        assert adapt_to_scheme("demo", mark, "dark") == mark
        light = fills(adapt_to_scheme("demo", mark, "light"))
        assert light[0] == INK

    def test_dark_ink_beside_a_colour_becomes_paper_on_dark_stone_when_nothing_is_light(
        self,
    ) -> None:
        mark = svg('<path d="M0 0h9v9z" fill="#31314a"/><path d="M9 9h9v9z" fill="#4caf50"/>')

        assert fills(adapt_to_scheme("demo", mark, "light")) == ["#31314a", "#4caf50"]
        assert fills(adapt_to_scheme("demo", mark, "dark")) == [PAPER, "#4caf50"]

    def test_the_same_ink_on_a_tile_with_something_light_is_left_alone(self) -> None:
        """A white glyph and a dark outline sit on the coloured tile, not on the stone."""
        tile = svg(
            '<rect width="24" height="24" fill="#4caf50"/><path d="M4 4h9v9z" fill="#fff"/><path d="M9 9h9v9z" fill="#111"/>'
        )

        assert adapt_to_scheme("demo", tile, "dark") == tile
        assert adapt_to_scheme("demo", tile, "light") == tile

    def test_a_mark_on_a_tile_of_its_own_is_left_alone_whatever_its_colours(self) -> None:
        """Noust's monogram: letters on a dark tile are not a figure on the page."""
        monogram = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img"><title>X</title>'
            '<rect width="64" height="64" rx="14" fill="#24292f"/>'
            '<text x="32" y="41" fill="#ffffff">GR</text></svg>'
        )
        circle = svg(
            '<circle cx="12" cy="12" r="12" fill="#111"/><path d="M6 6h12v12z" fill="#fff"/>'
        )

        for scheme in ("light", "dark"):
            assert adapt_to_scheme("demo", monogram, scheme) == monogram
            assert adapt_to_scheme("demo", circle, scheme) == circle

    def test_a_rectangle_that_paints_nothing_is_not_a_tile(self) -> None:
        """Heimdall's file starts with an unfilled frame as big as the picture: the black figure is the mark."""
        frame = svg(
            '<rect fill="none" x="-1" y="-1" width="26" height="26"/><path d="M3 3h18v18z"/>'
        )
        see_through = svg(
            '<circle fill="transparent" cx="12" cy="12" r="12"/><path d="M3 3h18v18z"/>'
        )

        for text in (frame, see_through):
            drawn = fills(adapt_to_scheme("demo", text, "dark"))
            assert PAPER in drawn
            assert min(contrast(PAPER, surface) for surface in DARK_SURFACES) >= 7

    def test_the_heimdall_mark_reads_on_the_dark_stone(self) -> None:
        """The packaged file: black at 1.1 to 1 before, the stone's paper now."""
        dark = logo_svg("heimdall", "dark")

        painted = [paint for paint in fills(dark) if paint not in ("", "none")]
        assert painted
        for paint in painted:
            assert min(contrast(paint, surface) for surface in DARK_SURFACES) >= MIN_CONTRAST

    def test_a_small_square_among_other_shapes_is_a_shape_and_not_a_tile(self) -> None:
        panda = svg(
            '<rect width="5" height="5" fill="#000"/><circle cx="12" cy="12" r="3" fill="#fff"/>'
        )

        assert fills(adapt_to_scheme("demo", panda, "dark"))[0] == PAPER

    def test_a_gradient_is_never_repainted(self) -> None:
        gradient = svg(
            '<defs><linearGradient id="g"><stop offset="0" stop-color="#000"/><stop offset="1" stop-color="#111"/></linearGradient></defs>'
            '<path d="M0 0h24v24z" fill="url(#g)"/>'
        )

        assert adapt_to_scheme("demo", gradient, "dark", brand="123456") == gradient

    def test_a_colour_that_is_not_understood_leaves_the_file_as_it_is(self) -> None:
        odd = svg('<path d="M0 0h9v9z" fill="var(--c)"/><path d="M9 9h9v9z"/>')

        assert adapt_to_scheme("demo", odd, "dark") == odd


class TestColoursInTheirSpellings:
    @pytest.mark.parametrize(
        "spelling",
        [
            "#000",
            "#000000",
            "black",
            "BLACK",
            "rgb(0,0,0)",
            "rgb(0, 0, 0)",
            "rgb(0%,0%,0%)",
            "#000000ff",
        ],
    )
    def test_black_is_black_however_it_is_written(self, spelling: str) -> None:
        out = adapt_to_scheme("demo", svg(f'<path d="M0 0h9v9z" fill="{spelling}"/>'), "dark")

        assert fills(out) == [PAPER]

    def test_currentcolor_is_the_default_black_of_an_image(self) -> None:
        out = adapt_to_scheme("demo", svg('<path d="M0 0h9v9z" fill="currentColor"/>'), "dark")

        assert fills(out) == [PAPER]

    def test_a_scheme_that_is_neither_light_nor_dark_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="scheme"):
            adapt_to_scheme("demo", svg('<path d="M0 0h9v9z"/>'), "sepia")

    def test_a_file_the_guard_would_refuse_is_refused_here_too(self) -> None:
        with pytest.raises(RecipeError):
            adapt_to_scheme("demo", svg('<script>alert(1)</script><path d="M0 0h9v9z"/>'), "dark")

    def test_nothing_but_a_colour_changes(self) -> None:
        source = svg('<path d="M0 0h9v9z" stroke-width="2" opacity=".5"/>')
        out = adapt_to_scheme("demo", source, "dark")

        def without_fills(text: str) -> list[tuple[str, dict[str, str]]]:
            root = ET.fromstring(text)  # noqa: S314 - the output of the code under test
            return [
                (e.tag, {k: v for k, v in e.attrib.items() if k != "fill"}) for e in root.iter()
            ]

        assert without_fills(out) == without_fills(source) and out != source


class TestWhatAProjectsRulesLetTheConsoleDo:
    """A mark the project says is not to be changed is not repainted, whatever the stone."""

    BLACK = svg('<path d="M3 3h18v18z"/>')

    def test_a_mark_that_is_not_to_be_changed_is_drawn_as_it_is_on_both_stones(self) -> None:
        for scheme in ("light", "dark"):
            assert adapt_to_scheme("demo", self.BLACK, scheme, repaint="no") == self.BLACK

    def test_a_mark_that_may_only_be_black_or_white_is_pure_black_or_pure_white(self) -> None:
        assert adapt_to_scheme("demo", self.BLACK, "light", repaint="white-black") == self.BLACK
        dark = adapt_to_scheme("demo", self.BLACK, "dark", repaint="white-black")

        assert fills(dark)[-1] == "#ffffff"
        assert PAPER not in dark

    def test_the_brand_colour_is_not_used_for_a_mark_that_may_only_be_black_or_white(self) -> None:
        dark = adapt_to_scheme("demo", self.BLACK, "dark", brand="336699", repaint="white-black")

        assert "#336699" not in dark and fills(dark)[-1] == "#ffffff"

    def test_without_a_rule_the_stone_s_own_paper_stands_in_as_it_always_did(self) -> None:
        assert PAPER in adapt_to_scheme("demo", self.BLACK, "dark")


class TestWhatThePackagedNoticesPromise:
    def test_a_mark_promised_unmodified_is_never_recoloured_beyond_what_its_notice_says(
        self,
    ) -> None:
        """code-server's notice said so and the console painted it white on the dark stone."""
        from noust.recipes import list_recipes
        from noust.recipes.logos import FORBIDS_ALTERING, logo_repaint

        checked = []
        for recipe in list_recipes():
            rule = logo_repaint(recipe.name, recipe.logo)
            if not recipe.logo or recipe.logo.endswith(".png") or not rule:
                continue
            checked.append(recipe.name)
            plain = logo_svg(recipe.name)
            for scheme in ("light", "dark"):
                drawn = logo_svg(recipe.name, scheme)
                if rule == "no":
                    assert drawn == plain, (recipe.name, scheme)
                else:
                    painted = {paint for paint in fills(drawn) if paint not in ("", "none")}
                    assert painted <= {"#000000", "#ffffff", "#000", "#fff"}, (recipe.name, scheme)
        assert {"code-server", "navidrome", "flarum", "anythingllm", "open-webui"} <= set(checked)
        assert FORBIDS_ALTERING.search("the file is shown unmodified")

    def test_code_server_reads_on_the_dark_stone_in_the_white_its_guidelines_allow(self) -> None:
        dark = logo_svg("code-server", "dark")

        assert 'fill="#ffffff"' in dark
        assert min(contrast("#ffffff", surface) for surface in DARK_SURFACES) >= 7
