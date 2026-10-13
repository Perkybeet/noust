# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Adding, checking and removing the operator's own recipes (checklist D-21)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click
import pytest
import yaml
from click.testing import CliRunner

from noust.cli.app import cli
from noust.cli.audit_policy import is_read_only
from noust.core.exceptions import NoustError
from noust.recipes import RecipeNotFoundError, get_recipe
from noust.recipes.model import RecipeError
from noust.recipes.private import add_recipes, read_bundles, remove_recipe
from tests.recipes.test_operator_recipes import write_recipe


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The operator's recipes folder, empty."""
    directory = tmp_path / "recipes"
    directory.mkdir(mode=0o755)
    monkeypatch.setenv("NOUST_RECIPES_DIR", str(directory))
    return directory


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A recipe laid out the way the catalogue's are, outside the folder."""
    directory = tmp_path / "mine"
    directory.mkdir()
    write_recipe(directory)
    return directory / "acme-wiki.yaml"


class TestAddingAndRemoving:
    def test_check_validates_without_adding(self, folder: Path, source: Path) -> None:
        [bundle] = read_bundles(source)

        assert bundle.recipe.name == "acme-wiki" and "logo.svg" in bundle.assets
        assert not (folder / "acme-wiki.yaml").exists()
        with pytest.raises(RecipeNotFoundError):
            get_recipe("acme-wiki")

    def test_add_copies_the_recipe_and_its_assets_into_the_folder(
        self, folder: Path, source: Path
    ) -> None:
        [added] = add_recipes(source)

        assert added.name == "acme-wiki"
        assert (folder / "acme-wiki.yaml").is_file()
        assert (folder / "assets" / "acme-wiki" / "logo.svg").is_file()
        assert get_recipe("acme-wiki").origin == "operator"
        assert (folder / "acme-wiki.yaml").stat().st_mode & 0o777 == 0o644

    def test_add_refuses_an_invalid_recipe_and_adds_nothing(
        self, folder: Path, source: Path
    ) -> None:
        document = yaml.safe_load(source.read_text())
        document["compose"] = document["compose"].replace("1.2.3@sha256", "latest@sha256")
        source.write_text(yaml.safe_dump(document))

        with pytest.raises(RecipeError, match="moves"):
            add_recipes(source)

        assert not (folder / "acme-wiki.yaml").exists()

    def test_add_refuses_a_shipped_name(self, folder: Path, tmp_path: Path) -> None:
        elsewhere = tmp_path / "other"
        elsewhere.mkdir()
        write_recipe(elsewhere, name="vaultwarden")

        with pytest.raises(RecipeError, match="shipped"):
            add_recipes(elsewhere)

    def test_add_will_not_overwrite_without_replace(self, folder: Path, source: Path) -> None:
        add_recipes(source)

        with pytest.raises(RecipeError, match="already exists"):
            add_recipes(source)
        [again] = add_recipes(source, replace=True)
        assert again.name == "acme-wiki"

    def test_a_replace_does_not_keep_an_asset_the_new_one_dropped(
        self, folder: Path, source: Path
    ) -> None:
        extra = source.parent / "assets" / "acme-wiki" / "extra.conf"
        extra.write_text("a = 1")
        add_recipes(source)
        assert (folder / "assets" / "acme-wiki" / "extra.conf").exists()
        extra.unlink()

        add_recipes(source, replace=True)

        assert not (folder / "assets" / "acme-wiki" / "extra.conf").exists()

    def test_add_refuses_a_link_in_the_source(
        self, folder: Path, source: Path, tmp_path: Path
    ) -> None:
        secret = tmp_path / "shadow"
        secret.write_text("root:x")
        os.symlink(secret, source.parent / "assets" / "acme-wiki" / "leak.conf")

        with pytest.raises(RecipeError, match="cannot be read"):
            add_recipes(source)

        assert not (folder / "acme-wiki.yaml").exists()

    def test_a_folder_of_recipes_is_added_together(self, folder: Path, tmp_path: Path) -> None:
        many = tmp_path / "many"
        many.mkdir()
        write_recipe(many, name="acme-wiki")
        write_recipe(many, name="acme-blog")

        added = add_recipes(many)

        assert {recipe.name for recipe in added} == {"acme-wiki", "acme-blog"}

    def test_remove_takes_only_a_private_recipe(self, folder: Path, source: Path) -> None:
        add_recipes(source)

        remove_recipe("acme-wiki")

        assert not (folder / "acme-wiki.yaml").exists()
        assert not (folder / "assets" / "acme-wiki").exists()
        with pytest.raises(RecipeNotFoundError):
            get_recipe("acme-wiki")
        with pytest.raises(NoustError, match="not one of yours"):
            remove_recipe("vaultwarden")
        with pytest.raises(RecipeNotFoundError):
            remove_recipe("acme-wiki")


def run(*args: str) -> Any:
    """Invoke ``noust recipe`` with these arguments."""
    return CliRunner().invoke(cli, ["recipe", *args])


class TestTheCommandLine:
    def test_add_list_and_remove(self, folder: Path, source: Path) -> None:
        added = run("add", str(source.parent))
        listed = run("list", "--origin", "operator", "--json")
        removed = run("remove", "acme-wiki")

        assert added.exit_code == 0, added.output
        assert "noust recipe install acme-wiki" in added.output
        payload = json.loads(listed.output)
        assert [item["name"] for item in payload["items"]] == ["acme-wiki"]
        assert payload["items"][0]["origin"] == "operator"
        assert removed.exit_code == 0, removed.output

    def test_list_names_the_file_that_is_not_usable(self, folder: Path) -> None:
        (folder / "notes.txt").write_text("hello")

        result = run("list", "-q", "vaultwarden")

        assert result.exit_code == 0 and "notes.txt" in result.output

    def test_check_exits_non_zero_on_an_invalid_recipe(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad-one.yaml"
        bad.write_text("schema: 2\nname: bad-one\n")

        result = run("check", str(bad))

        assert result.exit_code != 0

    def test_check_is_a_read_only_command_and_add_and_remove_are_not(self) -> None:
        group = cli.get_command(click.Context(cli), "recipe")
        assert isinstance(group, click.Group)

        def read_only(name: str) -> bool:
            return is_read_only(group.commands[name], f"recipe {name}", {})

        assert read_only("check") is True
        assert [read_only("add"), read_only("remove")] == [False, False]
