# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A logo somebody else drew becomes one the console may serve, or it is refused whole."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from noust.recipes import RecipeError
from noust.recipes.logo_sanitize import sanitize_svg
from noust.recipes.logos import MAX_LOGO_BYTES, validate_svg

SVG = "http://www.w3.org/2000/svg"
INKSCAPE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    "<!-- Created with Inkscape -->\n"
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
    'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" '
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
    'xmlns:cc="http://creativecommons.org/ns#" width="128px" height="128px" version="1.1" '
    'sodipodi:docname="logo.svg" inkscape:version="1.2">'
    '<sodipodi:namedview id="nv" pagecolor="#fff" inkscape:zoom="3"/>'
    '<metadata id="md"><rdf:RDF><cc:Work rdf:about=""><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">x'
    "</dc:title></cc:Work></rdf:RDF></metadata>"
    '<g inkscape:label="Layer 1" inkscape:groupmode="layer" id="layer1" transform="translate(0.123456 0)">'
    '<path id="p1" style="fill:#336699;fill-opacity:1;stroke:none" d="M10.123456 10.98765 L 20.0 20.5 Z" '
    'inkscape:connector-curvature="0"/>'
    "</g></svg>"
)


def clean(text: str, name: str = "demo", **options: str) -> str:
    """Sanitize and prove the answer passes the guard the package holds every logo to."""
    result = sanitize_svg(name, text, **options)
    assert validate_svg(name, result) == result
    return result


def tree(text: str) -> ET.Element:
    return ET.fromstring(text)  # noqa: S314 - the output of the sanitizer under test


def names(root: ET.Element) -> list[str]:
    return [element.tag.rsplit("}", 1)[-1] for element in root.iter()]


class TestWhatAnEditorLeavesBehind:
    def test_editor_data_metadata_and_foreign_namespaces_are_gone(self) -> None:
        out = clean(INKSCAPE, title="Demo")
        root = tree(out)

        assert names(root) == ["svg", "title", "g", "path"]
        assert "inkscape" not in out and "sodipodi" not in out and "rdf" not in out
        assert "Created with" not in out and "<?xml" not in out

    def test_the_size_becomes_a_viewbox_and_the_title_is_the_one_given(self) -> None:
        out = clean(INKSCAPE, title="Demo Logo")
        root = tree(out)

        assert root.attrib["viewBox"] == "0 0 128 128"
        assert "width" not in root.attrib and "height" not in root.attrib
        assert root.attrib["role"] == "img"
        assert root.find(f"{{{SVG}}}title").text == "Demo Logo"  # type: ignore[union-attr]

    def test_a_viewbox_the_file_has_is_kept_and_wins_over_width_and_height(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="2 3 40 50"><path d="M0 0h9v9z"/></svg>'
        )

        assert tree(out).attrib["viewBox"] == "2 3 40 50"

    def test_numbers_are_rounded_and_the_path_is_written_compactly(self) -> None:
        out = clean(INKSCAPE)
        root = tree(out)
        path = root.find(f".//{{{SVG}}}path")
        group = root.find(f".//{{{SVG}}}g")

        assert path is not None and group is not None
        assert path.attrib["d"] == "M10.12 10.99 20 20.5Z"
        assert group.attrib["transform"].startswith("translate(.123")

    def test_a_style_attribute_becomes_presentation_attributes(self) -> None:
        out = clean(INKSCAPE)
        path = tree(out).find(f".//{{{SVG}}}path")

        assert path is not None
        assert path.attrib["fill"] == "#336699" and path.attrib["stroke"] == "none"
        assert "style" not in path.attrib and "fill-opacity" in path.attrib

    def test_a_stylesheet_of_classes_is_applied_to_the_elements_and_dropped(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><defs><style type="text/css">'
            ".st0{fill:#ff0000;}\n.st1{fill:none;stroke:#00f;stroke-width:2}\ncircle{opacity:.5}"
            "</style></defs>"
            '<path class="st0" d="M0 0h10v10z"/><g class="st1"><circle class="st0" cx="5" cy="5" r="2"/></g></svg>'
        )
        root = tree(out)
        path = root.find(f".//{{{SVG}}}path")
        group = root.find(f".//{{{SVG}}}g")
        circle = root.find(f".//{{{SVG}}}circle")

        assert "style" not in names(root) and "class" not in out
        assert path is not None and path.attrib["fill"] == "#ff0000"
        assert group is not None and group.attrib["stroke"] == "#00f"
        assert (
            circle is not None
            and circle.attrib["fill"] == "#ff0000"
            and circle.attrib["opacity"] == ".5"
        )

    def test_current_color_is_the_colour_the_file_sets_for_it(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" style="color: #e97a42">'
            '<path fill="currentColor" d="M0 0h10v10z"/><g color="#123456"><circle r="2" stroke="currentColor" fill="none"/></g>'
            '<rect width="3" height="3" fill="currentColor" style="color:red"/></svg>'
        )

        assert "currentColor" not in out
        assert 'fill="#e97a42"' in out and 'stroke="#123456"' in out and 'fill="red"' in out

    def test_what_is_not_drawn_is_dropped(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<g display="none"><path d="M0 0h1v1z"/></g>'
            '<path style="display:none" d="M0 0h2v2z"/>'
            '<g></g><path d="M0 0h24v24z"/></svg>'
        )

        assert names(tree(out)).count("path") == 1 and "g" not in names(tree(out))

    def test_a_filter_is_dropped_and_what_used_it_stays(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><defs><filter id="f"><feGaussianBlur stdDeviation="2"/>'
            '</filter></defs><path filter="url(#f)" d="M0 0h24v24z"/></svg>'
        )

        assert "filter" not in out and "<path" in out


class TestGradientsAndClipPaths:
    GRADIENT = (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 24 24">'
        '<defs><linearGradient id="base"><stop offset="0" style="stop-color:#f00"/><stop offset="1" stop-color="#00f"/></linearGradient>'
        '<linearGradient id="a" xlink:href="#base" x1="0" y1="0" x2="24" y2="0" gradientUnits="userSpaceOnUse"/>'
        '<linearGradient id="unused"><stop offset="0"/></linearGradient>'
        '<clipPath id="clip"><rect width="12" height="24"/></clipPath></defs>'
        '<path id="shape" d="M0 0h24v24z" fill="url(#a)" clip-path="url(#clip)"/></svg>'
    )

    def test_a_gradient_that_borrows_its_stops_gets_them_and_the_rest_is_pruned(self) -> None:
        out = clean(self.GRADIENT, name="demo")
        root = tree(out)

        gradients = root.findall(f".//{{{SVG}}}linearGradient")
        assert len(gradients) == 1
        assert [stop.attrib["stop-color"] for stop in gradients[0]] == ["#f00", "#00f"]
        assert gradients[0].attrib["x2"] == "24"
        assert "href" not in out and "unused" not in out and "base" not in out

    def test_ids_are_the_package_s_own_so_two_inlined_logos_never_meet(self) -> None:
        out = clean(self.GRADIENT, name="demo")
        ids = re.findall(r'id="([^"]+)"', out)

        assert ids and all(identifier.startswith("demo-") for identifier in ids)
        assert re.search(r'fill="url\(#demo-\d+\)"', out)
        assert re.search(r'clip-path="url\(#demo-\d+\)"', out)

    def test_a_gradient_found_outside_defs_is_moved_there(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g><linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient>'
            '<path d="M0 0h24v24z" fill="url(#g)"/></g></svg>'
        )
        root = tree(out)

        assert root.find(f"{{{SVG}}}defs/{{{SVG}}}linearGradient") is not None

    def test_a_paint_that_names_nothing_the_file_has_is_refused(self) -> None:
        with pytest.raises(RecipeError, match="nothing"):
            sanitize_svg(
                "demo",
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h1v1z" fill="url(#nowhere)"/></svg>',
            )


class TestUse:
    def test_a_use_of_a_shape_in_the_same_file_is_written_out(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 24 24">'
            '<defs><path id="dot" d="M0 0h2v2z"/></defs>'
            '<use xlink:href="#dot" x="4" y="5" fill="#123456"/><use href="#dot" transform="rotate(90)"/></svg>'
        )
        root = tree(out)

        assert "use" not in names(root) and names(root).count("path") == 2
        assert "translate(4 5)" in out and "rotate(90)" in out and 'fill="#123456"' in out

    def test_a_use_of_another_file_is_dropped_whatever_the_scheme(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><use href="https://evil.example/sprite.svg#a"/>'
            '<use href="//evil.example/s.svg#a"/><use href="sprite.svg#a"/><use href="data:image/svg+xml,x"/>'
            '<path d="M0 0h24v24z"/></svg>'
        )

        assert names(tree(out)) == ["svg", "title", "path"] and "evil" not in out

    def test_a_chain_of_uses_that_grows_without_end_is_refused(self) -> None:
        """Each level uses the one before twice: eleven levels are two thousand copies."""
        levels = ['<path id="l0" d="M0 0h1v1z"/>']
        for index in range(1, 12):
            levels.append(
                f'<g id="l{index}"><use href="#l{index - 1}"/><use href="#l{index - 1}"/></g>'
            )
        text = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><defs>'
            + "".join(levels)
            + '</defs><use href="#l11"/></svg>'
        )

        with pytest.raises(RecipeError, match=r"too (many|large|busy)"):
            sanitize_svg("demo", text)

    def test_a_use_that_uses_itself_is_refused(self) -> None:
        with pytest.raises(RecipeError, match="itself"):
            sanitize_svg(
                "demo",
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><defs><g id="a"><use href="#a"/></g></defs><use href="#a"/></svg>',
            )


class TestWhatRunsOrReachesAnotherResource:
    PREFIX = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 24 24" '

    def test_a_script_a_foreign_object_and_every_handler_are_removed(self) -> None:
        out = clean(
            self.PREFIX + 'onload="alert(1)" onclick="alert(2)">'
            '<script>alert(3)</script><script xlink:href="https://evil.example/x.js"/>'
            '<foreignObject width="10" height="10"><div xmlns="http://www.w3.org/1999/xhtml"><img src="x" onerror="alert(4)"/></div></foreignObject>'
            '<path onmouseover="alert(5)" onfocus="alert(6)" d="M0 0h24v24z" fill="#000"/></svg>'
        )

        assert names(tree(out)) == ["svg", "title", "path"]
        assert not re.search(r"alert|script|foreign|\bon[a-z]+=", out)

    def test_a_link_around_shapes_is_dropped_and_the_shapes_stay(self) -> None:
        out = clean(
            self.PREFIX
            + '><a xlink:href="javascript:alert(1)"><path d="M0 0h24v24z"/></a><a href="https://evil.example/"><circle r="2"/></a></svg>'
        )

        assert "<a" not in out and "href" not in out and "javascript" not in out
        assert names(tree(out)).count("path") == 1 and names(tree(out)).count("circle") == 1

    def test_animation_that_could_set_an_attribute_later_is_removed(self) -> None:
        out = clean(
            self.PREFIX
            + '><path d="M0 0h24v24z"><set attributeName="href" to="javascript:alert(1)"/>'
            '<animate attributeName="fill" values="red;blue" dur="1s"/>'
            '<animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="1s"/></path></svg>'
        )

        assert (
            names(tree(out)) == ["svg", "title", "path"]
            and "animate" not in out
            and "set" not in out
        )

    @pytest.mark.parametrize(
        "declaration",
        [
            "fill:url(https://evil.example/a.svg#x)",
            "fill:url('//evil.example/a.svg#x')",
            "fill:url(&quot;data:image/svg+xml;base64,AAAA&quot;)",
            "stroke:url(file:///etc/passwd)",
            "clip-path:url(http://evil.example/c.svg#c)",
            "fill:expression(alert(1))",
            "fill:javascript:alert(1)",
        ],
    )
    def test_a_style_that_reaches_another_origin_loses_that_declaration(
        self, declaration: str
    ) -> None:
        out = clean(
            self.PREFIX + f'><path style="{declaration};stroke-width:2" d="M0 0h24v24z"/></svg>'
        )

        assert (
            "evil" not in out
            and "expression" not in out
            and "javascript" not in out
            and "etc/passwd" not in out
        )
        assert 'stroke-width="2"' in out

    def test_a_stylesheet_that_imports_and_loads_fonts_is_dropped_with_the_rest(self) -> None:
        out = clean(
            self.PREFIX
            + "><style>@import url(https://evil.example/x.css);@font-face{font-family:x;src:url(https://evil.example/f.woff)}"
            ".a{fill:url(http://evil.example/p.svg#p);stroke:#abc}</style>"
            '<path class="a" d="M0 0h24v24z"/></svg>'
        )

        assert "evil" not in out and "import" not in out and "font" not in out
        assert 'stroke="#abc"' in out

    def test_a_character_reference_cannot_spell_what_a_check_would_refuse(self) -> None:
        out = clean(
            self.PREFIX
            + '><path fill="java&#115;cript:alert(1)" stroke="&#117;rl(http://evil.example/)" d="M0 0h24v24z"/></svg>'
        )

        assert "javascript" not in out and "evil" not in out

    @pytest.mark.parametrize(
        "tag",
        [
            '<image href="https://evil.example/x.png" width="9" height="9"/>',
            '<image xlink:href="data:image/png;base64,iVBORw0KGgo=" width="9" height="9"/>',
            '<mask id="m"><rect width="9" height="9"/></mask>',
            '<pattern id="p" width="2" height="2"><rect width="1" height="1"/></pattern>',
            '<text x="1" y="1">Logo</text>',
            '<iframe src="https://evil.example/"/>',
        ],
    )
    def test_what_cannot_be_drawn_inside_the_policy_makes_the_file_unusable(self, tag: str) -> None:
        """Dropping a raster, a mask or a text would draw another logo, so the file is refused."""
        with pytest.raises(RecipeError):
            sanitize_svg("demo", self.PREFIX + f'><path d="M0 0h24v24z"/>{tag}</svg>')


class TestWhatAScaleCannotLose:
    def test_a_transform_keeps_the_digits_that_matter_whatever_the_size_of_the_canvas(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><g transform="matrix(0.637323,0,0,0.637323,-243.0951,-716.7254)">'
            '<path d="M0 0h10v10z"/></g></svg>'
        )

        assert 'transform="matrix(.637323,0,0,.637323,-243.095,-716.725)"' in out

    def test_a_gradient_that_is_drawn_through_a_tiny_matrix_is_not_flattened(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs>'
            '<linearGradient id="g" x1="0.456789" y1="0" x2="1" y2="0" gradientUnits="userSpaceOnUse" '
            'gradientTransform="matrix(2.2e-14,-358.5,358.5,2.2e-14,10.123456,20)"><stop offset="0.3333333" stop-color="#fff"/></linearGradient></defs>'
            '<path d="M0 0h10v10z" fill="url(#g)"/></svg>'
        )

        assert 'gradientTransform="matrix(0,-358.5,358.5,0,10.1235,20)"' in out
        assert 'x1=".456789"' in out
        assert 'offset=".333333"' in out


class TestWhatEditorsWrapAMarkIn:
    def test_a_nested_svg_becomes_a_group_that_places_its_viewbox_in_its_box(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
            '<svg x="10" y="20" width="100" height="50" viewBox="0 0 10 10" preserveAspectRatio="xMidYMid meet">'
            '<path d="M0 0h10v10z" fill="#123456"/></svg></svg>'
        )
        group = tree(out).find(f".//{{{SVG}}}g")

        assert "svg" not in names(tree(out))[1:] and group is not None
        # 50 high is the smaller side: a scale of 5, centred in the 100 wide box, from (10, 20).
        assert group.attrib["transform"] == "translate(35 20) scale(5)"
        assert tree(out).attrib["viewBox"] == "0 0 200 100"

    def test_a_nested_svg_that_stretches_scales_each_side(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><svg width="100" height="50" viewBox="0 0 10 10" '
            'preserveAspectRatio="none"><path d="M0 0h10v10z"/></svg></svg>'
        )

        assert 'transform="scale(10 5)"' in out

    def test_a_nested_svg_without_a_viewbox_only_moves_its_content(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><svg x="4" y="6" width="50" height="50"><path d="M0 0h10v10z"/></svg></svg>'
        )

        assert 'transform="translate(4 6)"' in out

    def test_an_illustrators_switch_keeps_what_it_draws_and_drops_the_foreign_object(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:i="http://ns.adobe.com/AdobeIllustrator/10.0/" viewBox="0 0 24 24">'
            '<switch><foreignObject requiredExtensions="http://ns.adobe.com/AdobeIllustrator/10.0/" width="1" height="1"><i:pgf id="x">AAAA</i:pgf></foreignObject>'
            '<g i:extraneous="self"><path d="M0 0h24v24z" fill="#abc"/></g></switch></svg>'
        )

        assert (
            names(tree(out)) == ["svg", "title", "path"]
            and "foreign" not in out.lower()
            and 'fill="#abc"' in out
        )


class TestWhatIsNotAnSvgWeCanRead:
    def test_entities_and_a_doctype_with_a_subset_are_refused(self) -> None:
        bomb = (
            '<!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        )
        external = '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        for head in (bomb, external):
            with pytest.raises(RecipeError, match="entit"):
                sanitize_svg(
                    "demo",
                    head
                    + '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><title>&x;</title></svg>',
                )

    def test_the_plain_doctype_an_illustrator_writes_is_dropped(self) -> None:
        out = clean(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24z"/></svg>'
        )

        assert "DOCTYPE" not in out and "w3.org/Graphics" not in out

    def test_a_processing_instruction_that_loads_a_stylesheet_is_dropped(self) -> None:
        out = clean(
            '<?xml-stylesheet href="https://evil.example/a.css"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0h24v24z"/></svg>'
        )

        assert "stylesheet" not in out and "evil" not in out

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "XML"),
            ("not xml", "XML"),
            ("<svg", "XML"),
            ('<html xmlns="http://www.w3.org/1999/xhtml"/>', "svg"),
            ('<svg xmlns="http://example.com/x" viewBox="0 0 1 1"/>', "svg"),
            ('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1v1z"/></svg>', "size"),
            (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 0 0"><path d="M0 0h1v1z"/></svg>',
                "size",
            ),
            (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="a b c d"><path d="M0 0h1v1z"/></svg>',
                "size",
            ),
            ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"/>', "nothing"),
            (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0 L 1"/></svg>',
                "path",
            ),
            (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="Q"/></svg>',
                "path",
            ),
            ("\x00<svg/>", "XML"),
        ],
    )
    def test_a_file_that_is_not_a_drawable_svg_is_refused_with_why(
        self, text: str, message: str
    ) -> None:
        with pytest.raises(RecipeError, match=message):
            sanitize_svg("demo", text)

    def test_what_is_not_text_is_refused(self) -> None:
        with pytest.raises(RecipeError, match="UTF-8"):
            sanitize_svg("demo", b"\xff\xfe<\x00s\x00v\x00g\x00/\x00>\x00")

    def test_a_file_far_larger_than_any_logo_is_refused_before_it_is_read(self) -> None:
        with pytest.raises(RecipeError, match="larger"):
            sanitize_svg("demo", "<svg " + " " * (2 * 1024 * 1024) + "/>")

    def test_a_document_nested_to_death_is_refused(self) -> None:
        text = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            + "<g>" * 300
            + '<path d="M0 0h1v1z"/>'
            + "</g>" * 300
            + "</svg>"
        )

        with pytest.raises(RecipeError, match=r"nested|deep|elements"):
            sanitize_svg("demo", text)

    def test_what_is_too_heavy_after_cleaning_is_refused_by_the_same_guard(self) -> None:
        path = (
            "M"
            + " ".join(f"{index}.{index % 97} {index}.{index % 89}" for index in range(2500))
            + "z"
        )
        text = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3000 3000"><path d="{path}"/></svg>'

        with pytest.raises(RecipeError, match="larger than"):
            sanitize_svg("demo", text)
        assert len(text) > MAX_LOGO_BYTES


class TestPathData:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("M 10,10 L 20,20", "M10 10 20 20"),
            ("m0.5 0.5l-0.25-0.75", "m.5.5-.25-.75"),
            ("M1 1H5V6h-3v-2z", "M1 1H5V6h-3v-2z"),
            ("M1.0 2.50 C 3 4 5 6 7 8 9 10 11 12 13 14", "M1 2.5C3 4 5 6 7 8 9 10 11 12 13 14"),
            ("M0 0a1.5 1.5 0 011.5 1.5", "M0 0a1.5 1.5 0 0 1 1.5 1.5"),
            ("M0 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0", "M0 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0"),
            ("M1e1 2E0", "M10 2"),
            ("M-0 -0.0001", "M0 0"),
        ],
    )
    def test_numbers_are_rounded_and_written_without_what_a_parser_does_not_need(
        self, source: str, expected: str
    ) -> None:
        out = clean(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="{source}"/></svg>'
        )
        path = tree(out).find(f".//{{{SVG}}}path")

        assert path is not None and path.attrib["d"] == expected

    def test_a_bigger_canvas_keeps_fewer_decimals(self) -> None:
        out = clean(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><path d="M100.12345 200.98765L300.5 400.25"/></svg>'
        )

        assert 'd="M100.12 200.99 300.5 400.25"' in out
