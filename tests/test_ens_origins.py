# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where code may be deployed from (ENS G18, op.exp.5 and mp.sw.1).

An empty list allows any origin, as every earlier version did. A list names
hosts, organisations or repositories, and a source outside it is refused
where every source is classified, before git or a download runs.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli
from noust.core.config import Config
from noust.core.ens import origins
from noust.core.ens.origins import (
    OriginNotAllowed,
    check_origin,
    describe_origin,
    matches,
    origin_kind,
)
from noust.core.exceptions import SourceError
from noust.core.recipe_sources import is_recipe_tree, recipe_source_dir
from noust.core.store import NoustStore, get_store


class TestMatching:
    @pytest.mark.parametrize(
        ("pattern", "source"),
        [
            ("github.com/acme", "https://github.com/acme/shop.git"),
            ("github.com/acme", "git@github.com:acme/shop.git"),
            ("github.com/Acme", "https://github.com/acme/shop"),
            ("github.com/acme/shop", "https://github.com/acme/shop.git#main"),
            ("git.example.com", "https://git.example.com/team/api.git"),
            ("*.example.com", "https://git.example.com/team/api.git"),
            ("https://github.com/acme", "https://github.com/acme/shop.git"),
            ("releases.example.com", "https://releases.example.com/app-1.0.tar.gz"),
            ("local", "/srv/code/shop"),
        ],
    )
    def test_an_allowed_origin_matches(self, pattern: str, source: str) -> None:
        assert matches(pattern, describe_origin(source))

    @pytest.mark.parametrize(
        ("pattern", "source"),
        [
            ("github.com/acme", "https://github.com/acme-evil/shop.git"),
            ("github.com/acme", "https://github.com/other/acme.git"),
            ("github.com/acme/shop", "https://github.com/acme/shop-fork.git"),
            ("git.example.com", "https://git.example.com.evil.net/team/api.git"),
            ("*.example.com", "https://example.com.evil.net/x.git"),
            ("github.com", "https://gitlab.com/acme/shop.git"),
            ("github.com", "/srv/code/shop"),
            ("local", "https://github.com/acme/shop.git"),
        ],
    )
    def test_anything_else_does_not(self, pattern: str, source: str) -> None:
        assert not matches(pattern, describe_origin(source))

    def test_credentials_in_a_url_do_not_change_the_origin(self) -> None:
        origin = describe_origin("https://x-access-token:secret@github.com/acme/shop.git")

        assert origin.host == "github.com"
        assert origin.path == ("acme", "shop")
        assert "secret" not in origin.display


class TestTheGuard:
    def test_an_empty_list_allows_everything(self) -> None:
        check_origin("https://gitlab.com/anyone/anything.git", [])

    def test_a_source_outside_the_list_is_refused_with_what_to_do(self) -> None:
        with pytest.raises(OriginNotAllowed) as caught:
            check_origin("https://gitlab.com/anyone/anything.git", ["github.com/acme"])

        assert isinstance(caught.value, SourceError)
        assert "gitlab.com/anyone/anything" in caught.value.message
        assert "security.allowed_sources" in (caught.value.details or "")

    def test_a_refusal_is_recorded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorded: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            origins, "_record_refusal", lambda display, allowed: recorded.append((display, allowed))
        )

        with pytest.raises(OriginNotAllowed):
            check_origin("https://gitlab.com/x/y.git", ["github.com/acme"])

        assert recorded and recorded[0][0] == "gitlab.com/x/y"

    def test_the_list_is_read_from_the_security_section(self) -> None:
        class Config:
            def get(self, key: str, default: Any = None) -> Any:
                return ["github.com/acme", " ", 3] if key == "security.allowed_sources" else default

        assert origins.allowed_sources(Config()) == ["github.com/acme", "3"]

    def test_the_source_chokepoint_applies_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.managers import source_manager

        monkeypatch.setattr(origins, "allowed_sources", lambda config=None: ["github.com/acme"])

        with pytest.raises(OriginNotAllowed):
            source_manager._validate_source_keeping_credentials("https://gitlab.com/x/y.git")
        kind, normalized = source_manager._validate_source_keeping_credentials(
            "https://github.com/acme/shop.git"
        )
        assert kind == "git" and normalized.endswith("acme/shop.git")


@pytest.fixture
def recipe_folder(sandbox: Path) -> Iterator[Path]:
    """
    The folder a recipe's source is rendered into, beside the store of the test.

    Args:
        sandbox: The isolated home.

    Yields:
        The folder, made.
    """
    NoustStore.reset_instance()
    folder = recipe_source_dir("shop-example-com", get_store())
    folder.mkdir(parents=True)
    yield folder
    NoustStore.reset_instance()


class TestRecipesAreAnOriginOfTheirOwn:
    """
    Noust prepares a recipe's code in a folder of its own, and it counted as "a directory on this
    server": a server that named its repositories could install nothing from the catalogue, so
    Noust blocked itself (profleet, n8n).
    """

    def test_the_folder_of_a_recipe_is_told_from_any_other_directory(
        self, recipe_folder: Path, tmp_path: Path
    ) -> None:
        other = tmp_path / "srv" / "shop"
        other.mkdir(parents=True)

        assert is_recipe_tree(recipe_folder)
        assert not is_recipe_tree(other)
        assert not is_recipe_tree(recipe_folder.parent)
        assert not is_recipe_tree(recipe_folder / "assets")
        assert not is_recipe_tree(recipe_folder.parent / "Shop.example.com")

    def test_the_kind_follows_where_the_source_is(self, recipe_folder: Path) -> None:
        assert origin_kind("local", str(recipe_folder)) == "recipe"
        assert origin_kind("local", "/srv/code/shop") == "local"
        assert origin_kind("git", "https://github.com/acme/shop.git") == "git"
        assert origin_kind("local", "push:shop.example.com") == "push"

    def test_a_recipe_is_described_by_its_folder(self, recipe_folder: Path) -> None:
        origin = describe_origin(str(recipe_folder), "recipe")

        assert origin.kind == "recipe" and origin.host is None
        assert origin.display == str(recipe_folder)

    def test_the_word_recipes_allows_them_and_only_them(self, recipe_folder: Path) -> None:
        recipe = describe_origin(str(recipe_folder), "recipe")

        assert matches("recipes", recipe)
        assert matches("Recipes", recipe)
        assert not matches("recipes", describe_origin("/srv/code/shop"))
        assert not matches("recipes", describe_origin("https://github.com/acme/shop.git"))
        assert not matches("recipes", describe_origin("push:shop.example.com"))

    def test_local_keeps_allowing_them_as_it_did_before(self, recipe_folder: Path) -> None:
        """A server that added ``local`` to get its recipes past the list is not broken by this."""
        assert matches("local", describe_origin(str(recipe_folder), "recipe"))

    def test_the_source_chokepoint_lets_a_recipe_through_on_the_word_alone(
        self, recipe_folder: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import source_manager

        monkeypatch.setattr(origins, "allowed_sources", lambda config=None: ["github.com/acme"])
        with pytest.raises(OriginNotAllowed) as refused:
            source_manager._validate_source_keeping_credentials(str(recipe_folder))
        assert "recipes" in (refused.value.details or "")

        monkeypatch.setattr(
            origins, "allowed_sources", lambda config=None: ["github.com/acme", "recipes"]
        )
        kind, _ = source_manager._validate_source_keeping_credentials(str(recipe_folder))
        assert kind == "local"
        with pytest.raises(OriginNotAllowed):
            source_manager._validate_source_keeping_credentials("/srv/code/shop")


class TestTheRefusalSuggestsACommandThatWorks:
    """
    The refusal proposed ``noust config set security.allowed_sources '[github.com/acme, /path]'``:
    a path never matches, and the brackets were stored, so copying it refused every deployment of
    the organisation. A test runs what it says.
    """

    SOURCES = [
        pytest.param("https://gitlab.com/anyone/anything.git", "git", id="a repository"),
        pytest.param("/srv/code/shop", "local", id="a directory"),
        pytest.param("push:shop.example.com", "push", id="a push"),
        pytest.param("/var/lib/noust/uploads/abc123", "upload", id="an upload"),
        pytest.param("RECIPE", "recipe", id="a recipe"),
    ]

    def refused(self, source: str, kind: str, recipe_folder: Path) -> tuple[str, OriginNotAllowed]:
        if source == "RECIPE":
            source = str(recipe_folder)
        with pytest.raises(OriginNotAllowed) as caught:
            check_origin(source, kind=kind)
        return source, caught.value

    def command_of(self, error: OriginNotAllowed) -> list[str]:
        found = re.search(r"^\s*(noust config set .+)$", error.details or "", re.MULTILINE)
        assert found, error.details
        return shlex.split(found.group(1))[1:]

    @pytest.mark.parametrize(("source", "kind"), SOURCES)
    def test_running_it_allows_the_source_and_keeps_the_rest(
        self, source: str, kind: str, real_config: Config, recipe_folder: Path
    ) -> None:
        real_config.set("security.allowed_sources", ["github.com/acme", "*.example.com"])
        source, error = self.refused(source, kind, recipe_folder)

        result = CliRunner().invoke(cli, self.command_of(error))

        assert result.exit_code == 0, result.output
        stored = Config().get("security.allowed_sources")
        assert stored[:2] == ["github.com/acme", "*.example.com"] and len(stored) == 3
        assert all("[" not in entry and "]" not in entry for entry in stored)
        check_origin(source, kind=kind)

    @pytest.mark.parametrize(
        ("source", "kind", "entry"),
        [
            ("https://gitlab.com/anyone/anything.git", "git", "gitlab.com/anyone/anything"),
            ("/srv/code/shop", "local", "local"),
            ("push:shop.example.com", "push", "push"),
            ("/var/lib/noust/uploads/abc123", "upload", "upload"),
            ("RECIPE", "recipe", "recipes"),
        ],
    )
    def test_it_proposes_the_word_for_a_place_and_the_repository_for_a_host(
        self, source: str, kind: str, entry: str, real_config: Config, recipe_folder: Path
    ) -> None:
        real_config.set("security.allowed_sources", ["github.com/acme"])
        _, error = self.refused(source, kind, recipe_folder)

        assert self.command_of(error)[-1].split(",")[-1] == entry

    def test_the_old_suggestion_with_its_brackets_is_gone(
        self, real_config: Config, recipe_folder: Path
    ) -> None:
        real_config.set("security.allowed_sources", ["github.com/acme"])
        _, error = self.refused("/srv/code/shop", "local", recipe_folder)

        assert "'[" not in (error.details or "") and "/srv/code" not in (error.details or "")
