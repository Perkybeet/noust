# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The operator's own recipes (checklist D-21): the same schema, a folder of their own, no way to hurt the catalogue."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from noust.recipes import RecipeNotFoundError, get_recipe, list_recipes, logo_svg
from noust.recipes.catalog import catalogue_problems
from noust.recipes.model import RecipeError
from noust.recipes.sources import MAX_RECIPE_BYTES
from tests.recipes.factory import compose_recipe

LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Acme">'
    "<title>Acme</title>"
    '<rect width="64" height="64" rx="14" fill="#24292f"/></svg>'
)


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An operator's recipes folder that Noust is told to read."""
    directory = tmp_path / "recipes"
    directory.mkdir(mode=0o755)
    monkeypatch.setenv("NOUST_RECIPES_DIR", str(directory))
    return directory


def write_recipe(directory: Path, name: str = "acme-wiki", **overrides: object) -> Path:
    """Write a valid private recipe, with its logo, and return its file."""
    document = compose_recipe(name=name, title="Acme Wiki", **overrides)
    file = directory / f"{name}.yaml"
    file.write_text(yaml.safe_dump(document, sort_keys=False))
    file.chmod(0o644)
    assets = directory / "assets" / name
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "logo.svg").write_text(LOGO)
    return file


class TestAFolderOfRecipes:
    def test_a_private_recipe_is_in_the_catalogue_with_its_origin(self, folder: Path) -> None:
        write_recipe(folder)

        recipe = get_recipe("acme-wiki")

        assert recipe.origin == "operator"
        assert recipe.title == "Acme Wiki"
        assert recipe.summary()["origin"] == "operator"
        assert "acme-wiki" in {entry.name for entry in list_recipes()}

    def test_the_shipped_recipes_say_so(self, folder: Path) -> None:
        assert get_recipe("vaultwarden").origin == "shipped"

    def test_its_logo_is_read_from_its_own_assets(self, folder: Path) -> None:
        write_recipe(folder)

        assert "Acme" in logo_svg("acme-wiki")

    def test_an_absent_folder_is_an_empty_one(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NOUST_RECIPES_DIR", str(tmp_path / "nothing-here"))

        assert catalogue_problems() == []
        with pytest.raises(RecipeNotFoundError):
            get_recipe("acme-wiki")

    def test_an_edit_is_seen_without_a_restart(self, folder: Path) -> None:
        file = write_recipe(folder)
        assert get_recipe("acme-wiki").slogan == "A demo."

        file.write_text(
            yaml.safe_dump(compose_recipe(name="acme-wiki", title="Acme Wiki", slogan="Changed."))
        )

        assert get_recipe("acme-wiki").slogan == "Changed."


class TestOneBadFileDoesNotTakeTheCatalogueDown:
    def test_an_invalid_recipe_is_a_problem_naming_the_field(self, folder: Path) -> None:
        file = write_recipe(folder)
        document = yaml.safe_load(file.read_text())
        document["colour"] = "red"
        file.write_text(yaml.safe_dump(document))

        names = {entry.name for entry in list_recipes()}

        assert "acme-wiki" not in names and "vaultwarden" in names
        [problem] = catalogue_problems()
        assert problem.name == "acme-wiki" and "colour" in problem.error
        assert str(file) in (problem.details or "")

    def test_the_error_for_a_private_recipe_names_its_file_not_the_package(
        self, folder: Path
    ) -> None:
        file = write_recipe(folder)
        file.write_text("schema: 2\nname: acme-wiki\n")

        with pytest.raises(RecipeError) as raised:
            get_recipe("acme-wiki")

        assert str(file) in (raised.value.details or "")

    def test_a_floating_tag_is_refused_there_as_it_is_in_the_package(self, folder: Path) -> None:
        text = yaml.safe_dump(compose_recipe(name="acme-wiki", title="Acme Wiki"))
        (folder / "acme-wiki.yaml").write_text(text.replace("1.2.3@sha256", "latest@sha256"))
        (folder / "acme-wiki.yaml").chmod(0o644)
        (folder / "assets" / "acme-wiki").mkdir(parents=True)
        (folder / "assets" / "acme-wiki" / "logo.svg").write_text(LOGO)

        [problem] = catalogue_problems()

        assert "moves" in problem.error

    def test_a_name_the_package_holds_is_refused(self, folder: Path) -> None:
        write_recipe(folder, name="vaultwarden")

        [problem] = catalogue_problems()

        assert problem.name == "vaultwarden" and "shipped" in problem.error
        assert get_recipe("vaultwarden").origin == "shipped"

    def test_a_file_that_is_not_a_recipe_is_named(self, folder: Path) -> None:
        (folder / "notes.txt").write_text("hello")
        (folder / "Bad_Name.yaml").write_text("x: 1")
        (folder / "Bad_Name.yaml").chmod(0o644)

        names = {problem.name for problem in catalogue_problems()}

        assert names == {"notes.txt", "Bad_Name"}

    def test_an_oversized_document_is_refused(self, folder: Path) -> None:
        file = write_recipe(folder)
        file.write_text("# " + "x" * MAX_RECIPE_BYTES + "\n")

        [problem] = catalogue_problems()

        assert "larger" in problem.error


class TestTheFolderIsOnlyReadWhenItCanBeTrusted:
    def test_a_folder_everyone_can_write_is_not_read(self, folder: Path) -> None:
        write_recipe(folder)
        folder.chmod(0o777)

        [problem] = catalogue_problems()

        assert problem.name == "(folder)" and "written" in problem.error
        with pytest.raises(RecipeNotFoundError):
            get_recipe("acme-wiki")

    def test_a_file_the_group_can_write_is_not_read(self, folder: Path) -> None:
        file = write_recipe(folder)
        file.chmod(0o664)

        [problem] = catalogue_problems()

        assert "written" in problem.error

    def test_a_link_as_the_recipe_is_not_followed(self, folder: Path, tmp_path: Path) -> None:
        real = tmp_path / "outside.yaml"
        real.write_text(yaml.safe_dump(compose_recipe(name="acme-wiki")))
        os.symlink(real, folder / "acme-wiki.yaml")

        [problem] = catalogue_problems()

        assert "symbolic link" in problem.error

    def test_a_linked_asset_is_not_followed(self, folder: Path, tmp_path: Path) -> None:
        write_recipe(folder)
        logo = folder / "assets" / "acme-wiki" / "logo.svg"
        logo.unlink()
        outside = tmp_path / "logo.svg"
        outside.write_text(LOGO)
        os.symlink(outside, logo)

        [problem] = catalogue_problems()

        assert "symbolic link" in problem.error

    def test_a_folder_that_is_a_link_is_not_read(self, tmp_path: Path, monkeypatch) -> None:
        real = tmp_path / "real"
        real.mkdir()
        write_recipe(real)
        os.symlink(real, tmp_path / "link")
        monkeypatch.setenv("NOUST_RECIPES_DIR", str(tmp_path / "link"))

        [problem] = catalogue_problems()

        assert problem.name == "(folder)" and "symbolic link" in problem.error
