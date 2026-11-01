# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""scripts/recipe_logos.py ``add``: the one way a file somebody else drew enters the package."""

from __future__ import annotations

import importlib.util
import struct
import sys
import zlib
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

from noust.recipes import RecipeError
from noust.recipes.logos import parse_logo_notice

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
DAY = date(2026, 10, 29)
SOURCE = "https://github.com/example/demo/blob/0123456789abcdef0123456789abcdef01234567/logo.svg"
HOSTILE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" onload="alert(1)">'
    "<script>alert(2)</script><style>.a{fill:#336699}</style>"
    '<path class="a" d="M0 0h24v24z"/><use href="https://evil.example/s.svg#a"/></svg>'
)


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    """The logos tool, loaded as a module."""
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("recipe_logos_tool", SCRIPTS / "recipe_logos.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["recipe_logos_tool"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recipes_dir(tmp_path: Path) -> Path:
    """A folder of recipes with one, ``demo``, that has the monogram."""
    (tmp_path / "demo.yaml").write_text("name: demo\ntitle: Demo\nlogo: logo.svg\nversion: '1'\n")
    (tmp_path / "assets" / "demo").mkdir(parents=True)
    (tmp_path / "assets" / "demo" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><title>Demo</title>'
        '<rect width="64" height="64" fill="#24292f"/><text x="32" y="41">DE</text></svg>'
    )
    return tmp_path


def picture() -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 6, 0, 0, 0))
        + chunk(b"tEXt", b"Comment\x00hello")
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x10\x20\x30\xff" * 256) * 256))
        + chunk(b"IEND", b"")
    )


def test_a_file_becomes_the_logo_with_its_notice_and_the_recipe_names_it(
    tool: ModuleType, recipes_dir: Path
) -> None:
    written = tool.add_logo(
        "demo",
        HOSTILE.encode(),
        title="Demo",
        source=SOURCE,
        licence="MIT",
        retrieved=DAY,
        copyright_line="Copyright (c) 2024 The Demo Authors",
        terms="no rule on the project's logo was found (checked 2026-10-29)",
        recipes_dir=recipes_dir,
    )

    text = written.read_text()
    assert written == recipes_dir / "assets" / "demo" / "logo.svg"
    assert (
        "script" not in text
        and "onload" not in text
        and "evil" not in text
        and "<style" not in text
    )
    assert 'fill="#336699"' in text, "the stylesheet was applied to the shape before it was dropped"
    notice = parse_logo_notice(
        "demo", (recipes_dir / "assets" / "demo" / "LOGO-NOTICE").read_text()
    )
    assert notice.source == SOURCE and notice.license == "MIT" and notice.retrieved == DAY
    assert notice.mark == "Demo" and "no rule" in notice.terms and notice.changes
    assert "logo: logo.svg" in (recipes_dir / "demo.yaml").read_text()


def test_a_picture_replaces_the_monogram_and_is_stripped_of_what_it_carried(
    tool: ModuleType, recipes_dir: Path
) -> None:
    written = tool.add_logo(
        "demo",
        picture(),
        title="Demo",
        source=SOURCE.replace("logo.svg", "logo.png"),
        licence="Apache-2.0",
        retrieved=DAY,
        recipes_dir=recipes_dir,
    )

    assert written == recipes_dir / "assets" / "demo" / "logo.png"
    assert b"tEXt" not in written.read_bytes() and b"hello" not in written.read_bytes()
    assert not (recipes_dir / "assets" / "demo" / "logo.svg").exists()
    assert "logo: logo.png" in (recipes_dir / "demo.yaml").read_text()


def test_the_notice_of_a_picture_says_what_was_done_to_a_picture_and_not_to_a_drawing(
    tool: ModuleType, recipes_dir: Path
) -> None:
    """The line about a stylesheet and editor data is true of an SVG only."""
    source = SOURCE.replace("logo.svg", "logo.png")
    tool.add_logo(
        "demo",
        picture(),
        title="Demo",
        source=source,
        licence="MIT",
        retrieved=DAY,
        recipes_dir=recipes_dir,
    )
    kept = (recipes_dir / "assets" / "demo" / "LOGO-NOTICE").read_text()

    assert "header and pixels only" in kept and "the artwork is unchanged" in kept
    assert "elements" not in kept and "stylesheet" not in kept and "scaled" not in kept

    tool.add_logo(
        "demo",
        picture(),
        title="Demo",
        source=source,
        licence="MIT",
        retrieved=DAY,
        scaled=True,
        recipes_dir=recipes_dir,
    )
    scaled = (recipes_dir / "assets" / "demo" / "LOGO-NOTICE").read_text()

    assert (
        "scaled to 256 pixels and written again with the picture's header and pixels only" in scaled
    )


def test_the_mark_a_collection_holds_under_the_name_of_the_family_is_found_for_each_member(
    tool: ModuleType,
) -> None:
    """Swagger UI and Swagger Editor are drawn with the one Swagger mark the collection has."""
    collection = {"swagger": {"title": "Swagger", "file": "/x/swagger.svg"}}

    assert tool.slug_for("swagger-ui", "Swagger UI", collection) == "swagger"
    assert tool.slug_for("swagger-editor", "Swagger Editor", collection) == "swagger"


def test_nothing_is_written_when_the_notice_or_the_file_is_not_acceptable(
    tool: ModuleType, recipes_dir: Path
) -> None:
    before = sorted(path.name for path in (recipes_dir / "assets" / "demo").iterdir())

    for source, licence, data in (
        ("http://github.com/example/demo/logo.svg", "MIT", HOSTILE.encode()),
        (SOURCE, "all rights reserved", HOSTILE.encode()),
        (SOURCE, "MIT", b"<svg"),
        (
            SOURCE,
            "MIT",
            b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'><image href='data:,x'/></svg>",
        ),
    ):
        with pytest.raises(RecipeError):
            tool.add_logo(
                "demo",
                data,
                title="Demo",
                source=source,
                licence=licence,
                retrieved=DAY,
                recipes_dir=recipes_dir,
            )

    assert sorted(path.name for path in (recipes_dir / "assets" / "demo").iterdir()) == before


def test_a_project_that_says_its_mark_is_not_to_be_changed_must_be_told_what_the_console_may_do(
    tool: ModuleType, recipes_dir: Path
) -> None:
    """The notice cannot promise 'shown unmodified' and leave the console to recolour the mark."""
    data = (
        b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path d='M0 0h24v24z'/></svg>"
    )
    terms = "demo.example/brand: the logo is to be used unmodified"

    with pytest.raises(RecipeError, match="repaint"):
        tool.add_logo(
            "demo",
            data,
            title="Demo",
            source=SOURCE,
            licence="MIT",
            retrieved=DAY,
            terms=terms,
            recipes_dir=recipes_dir,
        )
    tool.add_logo(
        "demo",
        data,
        title="Demo",
        source=SOURCE,
        licence="MIT",
        retrieved=DAY,
        terms=terms,
        repaint="no",
        recipes_dir=recipes_dir,
    )

    notice = parse_logo_notice(
        "demo", (recipes_dir / "assets" / "demo" / "LOGO-NOTICE").read_text()
    )
    assert notice.repaint == "no"


def test_a_notice_cannot_promise_a_mark_unmodified_without_saying_what_is_repainted() -> None:
    """The parser holds the packaged notices to it."""
    text = (
        "Mark of: Demo\nSource: https://example.com/a.svg\nLicence: MIT\n"
        "Terms: the logo is shown unmodified\nRetrieved: 2026-10-30\n"
    )

    with pytest.raises(RecipeError, match="Repaint"):
        parse_logo_notice("demo", text)
    assert parse_logo_notice("demo", text + "Repaint: no\n").repaint == "no"
    with pytest.raises(RecipeError, match="Repaint is one of"):
        parse_logo_notice("demo", text + "Repaint: sometimes\n")


def test_a_file_with_a_picture_far_larger_than_its_figure_is_cropped_and_the_notice_says_so(
    tool: ModuleType, recipes_dir: Path
) -> None:
    """Headscale's file: dots in a canvas twice as large."""
    big = (
        b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1000 600'>"
        b"<circle cx='500' cy='300' r='50' fill='#336699'/></svg>"
    )

    tool.add_logo(
        "demo",
        big,
        title="Demo",
        source=SOURCE,
        licence="MIT",
        retrieved=DAY,
        recipes_dir=recipes_dir,
    )

    written = (recipes_dir / "assets" / "demo" / "logo.svg").read_text()
    notice = (recipes_dir / "assets" / "demo" / "LOGO-NOTICE").read_text()
    assert 'viewBox="446 246 108 108"' in written
    assert "cropped to the figure" in notice and "the shapes are not touched" in notice


def test_a_figure_that_is_a_strip_at_thirty_two_pixels_is_said_to_the_person_adding_it(
    tool: ModuleType,
) -> None:
    """VersityGW's banner: 150 by 53, and the symbol in it a few pixels."""
    banner = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 150 50'>"
        "<path d='M0 0h150v50H0z'/></svg>"
    )
    square = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 50 50'><path d='M0 0h50v50H0z'/></svg>"

    assert "strip" in (tool.strip_warning(banner) or "")
    assert tool.strip_warning(square) is None


@pytest.mark.parametrize(
    "line",
    [
        "Copyright (C) 2007 Free Software Foundation, Inc. <https://fsf.org/>",
        "Copyright (c) the FSF",
    ],
)
def test_the_free_software_foundation_is_the_author_of_a_licence_text_not_of_a_logo(
    tool: ModuleType, recipes_dir: Path, line: str
) -> None:
    """A GPL project's LICENSE file begins with the FSF's line; it was being copied as the logo's credit."""
    with pytest.raises(RecipeError, match="Free Software Foundation"):
        tool.add_logo(
            "demo",
            b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path d='M0 0h24v24z'/></svg>",
            title="Demo",
            source=SOURCE,
            licence="AGPL-3.0-only",
            retrieved=DAY,
            copyright_line=line,
            recipes_dir=recipes_dir,
        )

    assert not (recipes_dir / "assets" / "demo" / "LOGO-NOTICE").exists()


def test_the_authors_of_the_project_are_credited_the_way_the_website_does(
    tool: ModuleType, recipes_dir: Path
) -> None:
    """The project's own people are the ones a logo of theirs is credited to."""
    tool.add_logo(
        "demo",
        b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path d='M0 0h24v24z'/></svg>",
        title="Demo",
        source=SOURCE,
        licence="AGPL-3.0-only",
        retrieved=DAY,
        copyright_line="the Demo contributors",
        recipes_dir=recipes_dir,
    )

    notice = parse_logo_notice(
        "demo", (recipes_dir / "assets" / "demo" / "LOGO-NOTICE").read_text()
    )
    assert notice.copyright == "the Demo contributors"


def test_no_packaged_mark_credits_the_free_software_foundation() -> None:
    """The fourteen that did are credited to the people of their projects."""
    from noust.recipes import list_recipes, read_asset

    found = []
    for recipe in list_recipes():
        try:
            text = read_asset(recipe.name, "LOGO-NOTICE")
        except (FileNotFoundError, OSError, RecipeError):
            continue
        if "Free Software Foundation" in parse_logo_notice(recipe.name, text).copyright:
            found.append(recipe.name)

    assert found == []


def test_a_project_whose_text_withholds_its_logo_is_not_given_the_collection_s_mark_either(
    tool: ModuleType,
) -> None:
    """Element's policy lets logos point to services Element runs; the collection holds its mark anyway."""
    collection = {"element": {"title": "Element", "file": "/x/element.svg"}}

    assert tool.slug_for("element-web", "Element Web", collection) is None


@pytest.mark.parametrize(
    "name",
    [
        "apprise-api",
        "element-web",
        "evershop",
        "evolutionapi",
        "flipt",
        "frappe-lending",
        "frappe-lms",
        "gitness",
        "joplin-server",
        "jupyterlab",
        "labelstudio",
        "leantime",
        "mindsdb",
        "once-campfire",
        "openpanel",
        "owncast",
        "photoprism",
        "plausible",
        "plunk",
        "pydio-cells",
        "sequin",
        "sim",
        "stalwart",
        "teable",
        "trek",
        "typebot",
        "usesend",
    ],
)
def test_a_project_that_does_not_let_a_third_party_show_its_logo_is_not_given_one(
    tool: ModuleType, recipes_dir: Path, name: str
) -> None:
    with pytest.raises(SystemExit, match="monogram"):
        tool.add_logo(
            name,
            HOSTILE.encode(),
            title=name,
            source=SOURCE,
            licence="MIT",
            retrieved=DAY,
            recipes_dir=recipes_dir,
        )


def test_the_collection_s_licence_is_recorded_mark_by_mark(tool: ModuleType) -> None:
    """CC0 unless the collection says otherwise; a custom licence is a reference with its address."""
    plain = tool.index_entry(
        {"title": "Gitea", "slug": "gitea", "hex": "609926", "source": "https://gitea.io"}
    )
    sharealike = tool.index_entry(
        {
            "title": "Forgejo",
            "hex": "FB923C",
            "license": {"type": "CC-BY-SA-4.0"},
            "guidelines": "https://x.example/",
        }
    )
    custom = tool.index_entry(
        {
            "title": "Keycloak",
            "hex": "4D4D4D",
            "license": {"type": "custom", "url": "https://lf.example/trademark"},
        }
    )

    assert plain == {
        "title": "Gitea",
        "hex": "609926",
        "license": "CC0-1.0",
        "source": "https://gitea.io",
    }
    assert (
        sharealike["license"] == "CC-BY-SA-4.0" and sharealike["guidelines"] == "https://x.example/"
    )
    assert (
        custom["license"] == "LicenseRef-custom"
        and custom["license_url"] == "https://lf.example/trademark"
    )


def test_the_packaged_marks_pass_the_tool_s_own_check(tool: ModuleType) -> None:
    """The same check CI can run: every named slug, the index, every notice."""
    icons = Path.home() / "recetas-work" / "src" / "simple-icons" / "pkg" / "package"
    if not icons.is_dir():
        pytest.skip("the unpacked simple-icons package is not on this machine")

    assert tool.main(["check", "--icons", str(icons)]) == 0
