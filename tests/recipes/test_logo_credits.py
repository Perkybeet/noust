# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A logo says where it comes from and under what licence, and the one that does not is refused."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.recipes import RecipeError, get_recipe, logo_svg, parse_recipe
from noust.recipes.logo_paint import PAPER
from noust.recipes.logos import LogoNotice, logo_credit, parse_logo_notice
from tests.recipes.factory import compose_recipe

NOTICE = """\
Mark of: Demo
Source: https://github.com/example/demo/blob/0123456789abcdef0123456789abcdef01234567/assets/logo.svg
Licence: MIT
Copyright: Copyright (c) 2024 The Demo Authors
Terms: the project publishes no trademark policy (checked 2026-10-29)
Retrieved: 2026-10-29
Changes: rewritten by scripts/recipe_logos.py; the artwork is unchanged
"""

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" role="img"><title>Demo</title>'
    '<path d="M0 0h24v24z" fill="#336699"/></svg>'
)


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The assets of a recipe named ``demo``, in a folder of this test's own."""
    from noust.recipes import assets

    directory = tmp_path / "demo"
    directory.mkdir()
    (directory / "logo.svg").write_text(SVG)
    real = assets.asset_resource

    def resource(name: str, asset: str) -> Any:
        return directory.joinpath(asset) if name == "demo" else real(name, asset)

    monkeypatch.setattr(assets, "asset_resource", resource)
    return directory


class TestTheNotice:
    def test_it_is_one_line_per_field_and_the_source_the_licence_and_the_day_are_required(
        self,
    ) -> None:
        notice = parse_logo_notice("demo", NOTICE)

        assert isinstance(notice, LogoNotice)
        assert notice.license == "MIT"
        assert notice.source.startswith("https://github.com/example/demo/blob/0123456789abcdef")
        assert notice.retrieved.isoformat() == "2026-10-29"
        assert notice.copyright == "Copyright (c) 2024 The Demo Authors"
        assert notice.terms.startswith("the project publishes no trademark policy")

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda text: text.replace("Licence: MIT\n", ""), "Licence"),
            (lambda text: text.replace("Source:", "Origin:"), "Origin"),
            (lambda text: text.replace("Retrieved: 2026-10-29\n", ""), "Retrieved"),
            (lambda text: text.replace("2026-10-29\nChanges", "yesterday\nChanges"), "Retrieved"),
            (lambda text: text.replace("https://github.com", "http://github.com"), "https"),
            (lambda text: text.replace("https://github.com", "ftp://github.com"), "https"),
            (
                lambda text: text.replace(
                    "https://github.com/example", "https://user:pw@github.com/example"
                ),
                "https",
            ),
            (lambda text: text.replace("Licence: MIT", "Licence: all rights reserved"), "licence"),
            (lambda text: text.replace("Licence: MIT", "Licence: MIT; rm -rf"), "licence"),
            (lambda text: text + "Mood: cheerful\n", "Mood"),
            (lambda text: text + "Source: https://example.org/other.svg\n", "twice"),
            (lambda text: "no fields at all\n", "not a field"),
            (lambda text: "", "Source"),
        ],
    )
    def test_a_notice_without_what_makes_a_mark_shippable_is_refused(
        self, change: Any, message: str
    ) -> None:
        with pytest.raises(RecipeError, match=message):
            parse_logo_notice("demo", change(NOTICE))

    @pytest.mark.parametrize(
        "licence",
        [
            "MIT",
            "Apache-2.0",
            "GPL-3.0-only",
            "AGPL-3.0-or-later",
            "CC-BY-4.0",
            "CC0-1.0",
            "BSD-3-Clause",
            "MIT OR Apache-2.0",
            "LicenseRef-brand-kit",
            "Zlib",
        ],
    )
    def test_a_licence_is_an_spdx_expression_or_a_reference_to_the_terms_given(
        self, licence: str
    ) -> None:
        assert (
            parse_logo_notice("demo", NOTICE.replace("Licence: MIT", f"Licence: {licence}")).license
            == licence
        )


class TestTheCreditOfARecipe:
    def test_a_file_of_the_recipe_with_a_notice_carries_the_notice_s_licence_and_source(
        self, folder: Path
    ) -> None:
        (folder / "LOGO-NOTICE").write_text(NOTICE)
        recipe = parse_recipe("demo", compose_recipe())
        summary: dict[str, Any] = recipe.summary()

        assert summary["logo_source"] == "asset"
        assert summary["logo_license"] == "MIT"
        assert summary["logo_origin"].startswith("https://github.com/example/demo/blob/")

    def test_the_monogram_has_no_notice_and_says_nothing_of_a_licence(self, folder: Path) -> None:
        recipe = parse_recipe("demo", compose_recipe())
        summary: dict[str, Any] = recipe.summary()

        assert summary["logo_license"] is None and summary["logo_origin"] is None

    def test_a_notice_that_does_not_parse_is_not_a_credit(self, folder: Path) -> None:
        """At run time a malformed notice of an operator's recipe credits nothing; the tests refuse it."""
        (folder / "LOGO-NOTICE").write_text("Licence: MIT\n")

        assert logo_credit("demo", "logo.svg") is None

    def test_a_mark_of_the_collection_carries_its_own_licence_not_the_collection_s(self) -> None:
        """simple-icons is CC0, and says where a mark is under another licence."""
        gitea = get_recipe("gitea").summary()
        forgejo = get_recipe("forgejo").summary()
        openbao = get_recipe("openbao").summary()

        assert gitea["logo_license"] == "CC0-1.0"
        assert forgejo["logo_license"] == "CC-BY-SA-4.0"
        assert openbao["logo_license"] == "CC-BY-SA-4.0"
        assert (
            gitea["logo_origin"]
            == "https://github.com/simple-icons/simple-icons/blob/16.36.0/icons/gitea.svg"
        )


class TestTheLogoThatIsServed:
    def test_a_mark_of_the_collection_is_served_as_the_package_holds_it_when_no_theme_is_asked(
        self,
    ) -> None:
        text = logo_svg("vaultwarden")

        assert "<path" in text and "fill=" not in text

    def test_a_black_mark_is_paper_on_the_dark_theme_and_still_black_on_the_light_one(self) -> None:
        assert PAPER in logo_svg("vaultwarden", scheme="dark")
        assert logo_svg("vaultwarden", scheme="light") == logo_svg("vaultwarden")

    def test_a_mark_is_the_brands_colour_when_it_reads(self) -> None:
        assert 'fill="#609926"' in logo_svg("gitea", scheme="light")
        assert 'fill="#609926"' in logo_svg("gitea", scheme="dark")

    def test_a_theme_that_is_not_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="scheme"):
            logo_svg("gitea", scheme="sepia")
