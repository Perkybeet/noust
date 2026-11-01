# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A mark drawn in a picture far larger than itself is cropped to its figure, and the measure is honest."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from noust.recipes import get_recipe, logo_svg
from noust.recipes.logo_bounds import FIGURE_SHARE, crop_to_figure, figure_bounds, figure_share
from noust.recipes.logos import validate_svg


def svg(body: str, view_box: str = "0 0 100 100") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" role="img">'
        f"<title>X</title>{body}</svg>"
    )


def parse(text: str) -> ET.Element:
    return ET.fromstring(text)  # noqa: S314 - markup the test wrote or the package ships


def bounds(body: str, view_box: str = "0 0 100 100") -> tuple[float, float, float, float] | None:
    return figure_bounds(parse(svg(body, view_box)))


class TestWhatTheShapesCover:
    def test_paths_with_absolute_and_relative_commands(self) -> None:
        assert bounds('<path d="M10 20L50 20 50 60h-40z"/>') == (10, 20, 50, 60)
        assert bounds('<path d="m10 20 40 0 0 40-40 0z"/>') == (10, 20, 50, 60)

    def test_a_curve_is_held_by_its_control_points_so_it_is_never_cut(self) -> None:
        box = bounds('<path d="M0 50C0 0 100 0 100 50"/>')

        assert box is not None and box[1] <= 0 and box[0] == 0 and box[2] == 100

    def test_arcs_with_their_flags_written_without_separators(self) -> None:
        box = bounds('<path d="M10 10a5 5 0 011 1h20v20"/>')

        assert box is not None and box[0] <= 10 and box[2] >= 31

    def test_circles_ellipses_rectangles_lines_and_polygons(self) -> None:
        assert bounds('<circle cx="30" cy="40" r="10"/>') == (20, 30, 40, 50)
        assert bounds('<ellipse cx="50" cy="50" rx="20" ry="10"/>') == (30, 40, 70, 60)
        assert bounds('<rect x="5" y="6" width="10" height="20"/>') == (5, 6, 15, 26)
        assert bounds('<polygon points="10,10 90,10 50,80"/>') == (10, 10, 90, 80)
        assert bounds('<line x1="10" y1="10" x2="60" y2="30" stroke="#000"/>') == (
            9.5,
            9.5,
            60.5,
            30.5,
        )

    def test_the_transforms_that_apply_to_a_shape_move_it(self) -> None:
        assert bounds('<g transform="translate(10 20)"><rect width="10" height="10"/></g>') == (
            10,
            20,
            20,
            30,
        )
        assert bounds('<circle cx="5" cy="5" r="5" transform="matrix(2 0 0 1 3 0)"/>') == (
            3,
            0,
            23,
            10,
        )
        assert bounds('<rect width="10" height="10" transform="scale(3)"/>') == (0, 0, 30, 30)

    def test_a_shape_that_paints_nothing_is_not_part_of_the_figure(self) -> None:
        """Heimdall's file begins with an unfilled frame as large as the picture."""
        frame = '<rect fill="none" x="-1" y="-1" width="102" height="102"/>'

        assert bounds(frame + '<circle cx="50" cy="50" r="10"/>') == (40, 40, 60, 60)
        assert bounds(frame) is None

    def test_what_is_in_defs_is_not_drawn(self) -> None:
        assert bounds(
            '<defs><circle cx="500" cy="500" r="9"/></defs><rect width="9" height="9"/>'
        ) == (
            0,
            0,
            9,
            9,
        )

    @pytest.mark.parametrize(
        "body",
        [
            "<text>GR</text>",
            '<rect width="9" height="9" transform="rotate(10) frob(2)"/>',
            '<path d="M0 0 Q"/>',
        ],
    )
    def test_what_it_cannot_read_is_not_measured_rather_than_guessed(self, body: str) -> None:
        assert bounds(body) is None


class TestCropping:
    def test_a_small_figure_in_a_large_picture_gets_its_own_frame(self) -> None:
        mark = svg('<circle cx="500" cy="300" r="50"/>', "0 0 1000 600")

        cropped = crop_to_figure(mark)

        assert figure_share(mark) is not None and figure_share(mark) < FIGURE_SHARE  # type: ignore[operator]
        view_box = parse(cropped).get("viewBox")
        assert view_box == "446 246 108 108"
        assert validate_svg("demo", cropped) == cropped

    def test_a_figure_that_already_fills_its_picture_is_left_as_it_is(self) -> None:
        mark = svg('<circle cx="50" cy="50" r="48"/>')

        assert crop_to_figure(mark) == mark

    def test_a_mark_that_cannot_be_measured_is_left_as_it_is(self) -> None:
        mark = svg("<text>GR</text>")

        assert crop_to_figure(mark) == mark
        assert figure_share(mark) is None

    def test_nothing_of_the_artwork_changes_only_the_window(self) -> None:
        mark = svg('<path d="M400 250h200v100H400z" fill="#336699"/>', "0 0 1000 600")

        cropped = crop_to_figure(mark)

        assert cropped.replace(parse(cropped).get("viewBox") or "", "0 0 1000 600") == mark


def test_headscale_s_picture_is_the_figure_s() -> None:
    """The file was drawn on a 1280 by 640 canvas and the dots cover 58% of it."""
    root = parse(logo_svg("headscale"))
    height = float((root.get("viewBox") or "").split()[3])

    assert height < 640
    assert (figure_share(logo_svg("headscale")) or 0) >= FIGURE_SHARE


def test_versitygw_has_the_square_symbol_and_not_the_banner() -> None:
    """A 150 by 53 banner is a strip at 32 pixels; the company's symbol is a square."""
    root = parse(logo_svg("versitygw"))
    _left, _top, width, height = (float(item) for item in (root.get("viewBox") or "").split())

    assert 0.8 <= width / height <= 1.25
    assert get_recipe("versitygw").logo_license == "CC-BY-4.0"


def test_no_packaged_mark_is_lost_in_a_picture_many_times_its_size() -> None:
    """A blunder, not a taste: a figure under 15% of its picture is a speck at any size."""
    from noust.recipes import list_recipes

    lost = []
    for recipe in list_recipes():
        if not recipe.logo or recipe.logo.endswith(".png"):
            continue
        share = figure_share(logo_svg(recipe.name))
        if share is not None and share < 0.15:
            lost.append((recipe.name, round(share, 2)))

    assert lost == []
