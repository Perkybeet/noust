# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A recipe's files (checklist D-11): copied or rendered, for Compose and native recipes alike."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from noust.core.store import NoustStore
from noust.recipes import RecipeError, get_recipe
from noust.recipes.deploy import _deployer_options, prepare_source
from tests.recipes.factory import compose_recipe, native_recipe
from tests.recipes.test_operator_recipes import LOGO


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The server's recipes folder, with a config template in the assets."""
    directory = tmp_path / "recipes"
    (directory / "assets" / "acme-wiki").mkdir(parents=True)
    directory.chmod(0o755)
    (directory / "assets" / "acme-wiki" / "logo.svg").write_text(LOGO)
    (directory / "assets" / "acme-wiki" / "settings.ini").write_text(
        "url = https://${DOMAIN_NAME}\nsecret = ${SECRET}\nport = ${PORT}\nprice = $$5\n"
    )
    (directory / "assets" / "acme-wiki" / "plain.conf").write_text("keep ${AS} is\n")
    (directory / "assets" / "acme-wiki" / "native.ini").write_text(
        "url = https://${DOMAIN_NAME}\nsecret = ${SECRET}\n"
    )
    monkeypatch.setenv("NOUST_RECIPES_DIR", str(directory))
    return directory


def add(folder: Path, document: dict[str, Any]) -> Any:
    """Write a recipe named acme-wiki into the folder and load it."""
    file = folder / "acme-wiki.yaml"
    file.write_text(yaml.safe_dump(document, sort_keys=False))
    file.chmod(0o644)
    return get_recipe("acme-wiki")


def compose(**overrides: Any) -> dict[str, Any]:
    """A Compose recipe that declares the variables the template uses."""
    variables = overrides.pop(
        "variables",
        {"SECRET": {"generate": "password32"}, "DOMAIN_NAME": {"from": "fqdn"}},
    )
    return compose_recipe(name="acme-wiki", title="Acme", variables=variables, **overrides)


class TestTheSchema:
    def test_a_file_is_copied_as_it_is_by_default(self, folder: Path) -> None:
        recipe = add(
            folder,
            compose(
                files={"conf/plain.conf": "plain.conf"},
                variables={"SECRET": {"generate": "password32"}},
            ),
        )

        assert recipe.files == {"conf/plain.conf": "plain.conf"}
        assert recipe.rendered_files == frozenset() and recipe.file_modes == {}

    def test_a_rendered_file_is_written_0600_unless_the_recipe_says(self, folder: Path) -> None:
        recipe = add(
            folder,
            compose(
                files={
                    "conf/settings.ini": {"asset": "settings.ini", "render": True},
                    "conf/open.ini": {"asset": "settings.ini", "render": True, "mode": "0644"},
                }
            ),
        )

        assert recipe.rendered_files == {"conf/settings.ini", "conf/open.ini"}
        assert recipe.file_modes == {"conf/settings.ini": 0o600, "conf/open.ini": 0o644}

    def test_a_native_recipe_may_ship_files_too(self, folder: Path) -> None:
        document = native_recipe(
            name="acme-wiki",
            title="Acme",
            variables={"SECRET": {"generate": "password32"}, "DOMAIN_NAME": {"from": "fqdn"}},
            files={"config/app.ini": {"asset": "native.ini", "render": True}},
        )

        recipe = add(folder, document)

        assert recipe.files == {"config/app.ini": "native.ini"}
        assert recipe.file_modes == {"config/app.ini": 0o640}

    def test_a_native_recipe_cannot_use_the_port_a_stack_is_given(self, folder: Path) -> None:
        document = native_recipe(
            name="acme-wiki",
            title="Acme",
            variables={"SECRET": {"generate": "password32"}, "DOMAIN_NAME": {"from": "fqdn"}},
            files={"config/app.ini": {"asset": "settings.ini", "render": True}},
        )

        with pytest.raises(RecipeError, match="PORT"):
            add(folder, document)

    def test_a_rendered_file_may_only_use_variables_the_recipe_declares(self, folder: Path) -> None:
        (folder / "assets" / "acme-wiki" / "bad.ini").write_text("token = ${NOBODY_DECLARED}\n")

        with pytest.raises(RecipeError, match="NOBODY_DECLARED"):
            add(folder, compose(files={"bad.ini": {"asset": "bad.ini", "render": True}}))

    def test_a_bare_dollar_in_a_rendered_file_is_a_mistake_named_at_load(
        self, folder: Path
    ) -> None:
        (folder / "assets" / "acme-wiki" / "bad.ini").write_text("price = $5\n")

        with pytest.raises(RecipeError, match="bare"):
            add(folder, compose(files={"bad.ini": {"asset": "bad.ini", "render": True}}))

    @pytest.mark.parametrize(
        ("entry", "message"),
        [
            ({"asset": "plain.conf", "mode": "0777"}, "mode must be one of"),
            ({"asset": "plain.conf", "mode": 644}, "mode must be one of"),
            ({"asset": "plain.conf", "render": "yes"}, "render must be true or false"),
            ({"asset": "plain.conf", "owner": "root"}, "unknown keys: owner"),
            ({"asset": "missing.conf"}, "not in assets"),
            ("../../etc/passwd", "plain relative path|not in assets"),
        ],
    )
    def test_a_malformed_entry_is_refused_naming_it(
        self, folder: Path, entry: Any, message: str
    ) -> None:
        with pytest.raises(RecipeError, match=message):
            add(folder, compose(files={"conf/x": entry}))

    @pytest.mark.parametrize("path", ["docker-compose.yml", ".env", "../x", "/etc/x"])
    def test_a_path_a_stack_owns_or_that_leaves_it_is_refused(
        self, folder: Path, path: str
    ) -> None:
        with pytest.raises(RecipeError, match="plain relative path"):
            add(folder, compose(files={path: "plain.conf"}))


class TestWhatIsWritten:
    VALUES = {"DOMAIN_NAME": "wiki.example.com", "SECRET": "s3cr3t", "PORT": "8123"}

    def test_a_compose_stack_gets_its_files_rendered_with_the_variables(
        self, folder: Path, recipe_store: NoustStore
    ) -> None:
        recipe = add(
            folder,
            compose(
                files={
                    "conf/settings.ini": {"asset": "settings.ini", "render": True},
                    "conf/plain.conf": "plain.conf",
                }
            ),
        )

        source, _ref = prepare_source(recipe, "acme-wiki", recipe_store, self.VALUES)

        directory = Path(source)
        rendered = directory / "conf/settings.ini"
        assert rendered.read_text() == (
            "url = https://wiki.example.com\nsecret = s3cr3t\nport = 8123\nprice = $5\n"
        )
        assert stat.S_IMODE(rendered.stat().st_mode) == 0o600
        # What is not rendered is copied exactly, references and all.
        assert (directory / "conf/plain.conf").read_text() == "keep ${AS} is\n"
        assert stat.S_IMODE((directory / "conf/plain.conf").stat().st_mode) == 0o644

    def test_a_variable_with_no_value_stops_the_install_before_anything_is_written(
        self, folder: Path, recipe_store: NoustStore
    ) -> None:
        recipe = add(
            folder, compose(files={"conf/settings.ini": {"asset": "settings.ini", "render": True}})
        )

        with pytest.raises(RecipeError, match="SECRET has no value"):
            prepare_source(recipe, "acme-wiki", recipe_store, {"DOMAIN_NAME": "x", "PORT": "1"})

    def test_a_native_recipe_seeds_its_files_and_records_them_as_persistent(
        self, folder: Path
    ) -> None:
        document = native_recipe(
            name="acme-wiki",
            title="Acme",
            variables={"SECRET": {"generate": "password32"}, "DOMAIN_NAME": {"from": "fqdn"}},
            persistent_paths=["data"],
            files={"config/app.ini": {"asset": "native.ini", "render": True}},
        )
        recipe = add(folder, document)

        options, persistent = _deployer_options(recipe, self.VALUES)

        assert options["seed_files"]["config/app.ini"] == (
            "url = https://wiki.example.com\nsecret = s3cr3t\n"
        )
        assert options["seed_modes"] == {"config/app.ini": 0o640}
        assert persistent == ["data", "config/app.ini"]

    def test_the_shipped_recipes_are_unchanged_by_all_this(self) -> None:
        wordpress = get_recipe("wordpress")
        options, persistent = _deployer_options(wordpress, {})

        assert "seed_files" not in options and "php_files" in options
        assert "wp-config.php" in persistent
